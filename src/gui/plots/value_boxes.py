"""Per-series values boxes placed where each series crosses a marker."""

from __future__ import annotations

from dataclasses import dataclass
import math
from html import escape

import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QFrame, QTextBrowser, QWidget

from src.gui.plots.marker_info import MarkerInfoEntry, format_duration
from src.gui.plots.markers import FIRST_MARKER, SECOND_MARKER

# Background opacity of every values box, in percent; set from the Settings dialog.
VALUE_BOX_OPACITY = [50]
BOX_GAP = 2
EDGE_MARGIN = 4
MARKER_OFFSET = 8
MAX_TEXT_WIDTH = 360


def set_value_box_opacity(percent: int) -> None:
    VALUE_BOX_OPACITY[0] = max(0, min(100, int(percent)))


@dataclass(frozen=True)
class ValueBoxItem:
    """One series' values at a marker, and where its line crosses the marker."""

    key: object
    entry: MarkerInfoEntry
    y_value: float | None
    # Cards whose markers are points rather than times anchor each box at its
    # own point: view coordinates (`x_value`, `y_value`) or canvas pixels.
    x_value: float | None = None
    # Canvas pixels, or a callable returning them (None when not visible).
    anchor: object = None


class ValueBox(QTextBrowser):
    """One series at one marker: a single line with its value.

    Hovering shows the full details; a click pins them in a view whose
    text can be selected and copied, until the next click outside it.
    """

    def __init__(self, layer: ValueBoxLayer, marker_index: int) -> None:
        super().__init__(layer.canvas)
        self.layer = layer
        self.marker_index = marker_index
        self.item: ValueBoxItem | None = None
        self.y_value: float | None = None
        self.extra_details: tuple[str, ...] = ()
        _configure_text_view(self)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.hide()

    def show_item(self, item: ValueBoxItem, extra_details: tuple[str, ...]) -> None:
        self.item = item
        self.y_value = item.y_value
        self.extra_details = extra_details
        entry = item.entry
        self.setHtml(f'<span style="color:{entry.color}"><b>{escape(entry.value)}</b></span>')
        self.apply_style()
        _fit_to_text(self)

    def details_html(self) -> str:
        """The series name, its details and any selection statistics."""
        entry = self.item.entry
        lines = [f"<b>{escape(entry.name)}: {escape(entry.value)}</b>"]
        lines += [escape(detail).replace(chr(10), "<br>") for detail in entry.details + self.extra_details]
        return f'<span style="color:{entry.color}">' + "<br>".join(lines) + "</span>"

    def details_text(self) -> str:
        entry = self.item.entry
        return "\n".join((f"{entry.name}: {entry.value}",) + entry.details + self.extra_details)

    def apply_style(self) -> None:
        self.setStyleSheet(_background_style(self.layer.canvas))

    def enterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().enterEvent(event)
        self.layer.details.show_for(self, pinned=False)

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().leaveEvent(event)
        self.layer.details.hide_hover(self)

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        # Consume clicks so they never place markers on the card underneath.
        event.accept()
        if event.button() == Qt.MouseButton.LeftButton:
            self.layer.details.show_for(self, pinned=True)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        event.accept()

    def contextMenuEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        event.accept()


class ValueDetails(QTextBrowser):
    """A box's full details: shown while hovering, or pinned by a click.

    A pinned view's text can be selected and copied; any click outside it closes it.
    """

    def __init__(self, layer: ValueBoxLayer) -> None:
        super().__init__(layer.canvas)
        self.layer = layer
        self.box: ValueBox | None = None
        self.pinned = False
        _configure_text_view(self)
        self.document().setDocumentMargin(4)
        self.hide()

    def show_for(self, box: ValueBox, *, pinned: bool) -> None:
        if self.pinned and not pinned:
            return
        self.box = box
        self.pinned = pinned
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
            if pinned else Qt.TextInteractionFlag.NoTextInteraction
        )
        app = QApplication.instance()
        if pinned and app is not None:
            app.installEventFilter(self)
        self.refresh()

    def refresh(self) -> None:
        """Update the content and place it beside its box, after the box changes."""
        box = self.box
        if box is None or box.item is None or box.isHidden():
            self.close_details()
            return
        self.setHtml(box.details_html())
        self.setStyleSheet(_background_style(self.layer.canvas, opaque=True, border=self.pinned))
        _fit_to_text(self)
        canvas = self.layer.canvas
        left = box.x() + box.width() + BOX_GAP
        if left + self.width() > canvas.width() - EDGE_MARGIN:
            left = box.x() - BOX_GAP - self.width()
        left = max(EDGE_MARGIN, min(left, canvas.width() - self.width() - EDGE_MARGIN))
        top = max(EDGE_MARGIN, min(box.y(), canvas.height() - self.height() - EDGE_MARGIN))
        self.move(int(left), int(top))
        self.show()
        self.raise_()

    def hide_hover(self, box: ValueBox) -> None:
        if not self.pinned and self.box is box:
            self.close_details()

    def close_details(self) -> None:
        if self.pinned and QApplication.instance() is not None:
            QApplication.instance().removeEventFilter(self)
        self.pinned = False
        self.box = None
        self.hide()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        if (self.pinned and event.type() == QEvent.Type.MouseButtonPress
                and isinstance(watched, QWidget) and watched.isWidgetType()
                and watched is not self and not self.isAncestorOf(watched)):
            self.close_details()
        return False


def _configure_text_view(view: QTextBrowser) -> None:
    view.setOpenLinks(False)
    view.setOpenExternalLinks(False)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.setFrameShape(QFrame.Shape.NoFrame)
    # Lines are short; each keeps to one row and sets the view width.
    view.setLineWrapMode(QTextBrowser.LineWrapMode.NoWrap)
    view.document().setDocumentMargin(1)


def _fit_to_text(view: QTextBrowser) -> None:
    """Size a view to its text, so a single line never wraps."""
    document = view.document()
    document.setTextWidth(-1)
    text_width = min(MAX_TEXT_WIDTH, math.ceil(document.idealWidth()))
    view.setFixedSize(text_width + 2, math.ceil(document.size().height()) + 2)


def _background_style(canvas, *, opaque: bool = False, border: bool = False) -> str:  # type: ignore[no-untyped-def]
    base = canvas.palette().color(canvas.backgroundRole())
    alpha = 235 if opaque else round(255 * VALUE_BOX_OPACITY[0] / 100)
    edge = canvas.palette().color(canvas.foregroundRole()).name() if border else "transparent"
    return (f"QTextBrowser {{ background: rgba({base.red()}, {base.green()}, {base.blue()}, {alpha}); "
            f"border: 1px solid {edge}; }}")


class ValueBoxLayer:
    """Manages the values boxes of both markers on one plot canvas or map view."""

    def __init__(self, canvas, view_box=None) -> None:  # type: ignore[no-untyped-def]
        self.canvas = canvas
        if view_box is None and hasattr(canvas, "getPlotItem"):
            view_box = canvas.getPlotItem().getViewBox()
        self.view_box = view_box
        self.boxes: dict[int, list[ValueBox]] = {FIRST_MARKER: [], SECOND_MARKER: []}
        self.items: dict[int, list[ValueBoxItem]] = {FIRST_MARKER: [], SECOND_MARKER: []}
        self.x_values: dict[int, float | None] = {FIRST_MARKER: None, SECOND_MARKER: None}
        self.selection_duration: float | None = None
        self.statistics: dict[object, tuple[str, ...]] = {}
        self.details = ValueDetails(self)
        if view_box is not None:
            view_box.sigRangeChanged.connect(lambda *_args: self.reposition())
            view_box.sigResized.connect(lambda *_args: self.reposition())

    def set_items(self, marker_index: int, x_value: float, items: list[ValueBoxItem]) -> None:
        self.x_values[marker_index] = x_value
        self.items[marker_index] = list(items)
        self._render(marker_index)
        self.reposition()

    def clear(self, marker_index: int | None = None) -> None:
        for index in (FIRST_MARKER, SECOND_MARKER) if marker_index is None else (marker_index,):
            self.x_values[index] = None
            self.items[index] = []
            for box in self.boxes[index]:
                box.hide()
        if self.details.box is not None and self.details.box.isHidden():
            self.details.close_details()
        self._repaint_canvas()

    def _repaint_canvas(self) -> None:
        """Redraw the whole canvas, so a hidden or moved box leaves nothing behind."""
        viewport = getattr(self.canvas, "viewport", None)
        (viewport() if callable(viewport) else self.canvas).update()

    def visible_boxes(self, marker_index: int | None = None) -> list[ValueBox]:
        indexes = (FIRST_MARKER, SECOND_MARKER) if marker_index is None else (marker_index,)
        return [box for index in indexes for box in self.boxes[index] if not box.isHidden()]

    def set_selection_duration(self, seconds: float | None) -> None:
        """Show the selection's duration in the details; ``None`` removes the selection."""
        self.selection_duration = seconds
        if seconds is None:
            self.statistics = {}
        self._rerender()

    def set_statistics(self, statistics: dict[object, tuple[str, ...]]) -> None:
        """Add per-series selection statistics, keyed like the box items, to the details."""
        self.statistics = dict(statistics)
        self._rerender()

    def _rerender(self) -> None:
        for index in (FIRST_MARKER, SECOND_MARKER):
            self._render(index)
        self.reposition()

    def apply_style(self) -> None:
        for box in self.boxes[FIRST_MARKER] + self.boxes[SECOND_MARKER]:
            box.apply_style()
        if not self.details.isHidden():
            self.details.refresh()

    def _render(self, marker_index: int) -> None:
        items = self.items[marker_index]
        boxes = self.boxes[marker_index]
        while len(boxes) < len(items):
            boxes.append(ValueBox(self, marker_index))
        for box, item in zip(boxes, items):
            extra = ()
            if self.selection_duration is not None:
                extra = (f"Selection Δt: {format_duration(self.selection_duration)}",) + self.statistics.get(item.key, ())
            box.show_item(item, extra)
            box.show()
            box.raise_()
        for box in boxes[len(items):]:
            box.hide()

    def reposition(self) -> None:
        """Place boxes right of their marker at their series' height, without overlap."""
        self._place_boxes()
        if not self.details.isHidden():
            self.details.refresh()
        self._repaint_canvas()

    def _place_boxes(self) -> None:
        if any(item.x_value is not None or item.anchor is not None
               for index in (FIRST_MARKER, SECOND_MARKER) for item in self.items[index]):
            self._place_at_points()
            return
        markers = [(index, x_value) for index, x_value in self.x_values.items()
                   if x_value is not None and self.visible_boxes(index)]
        if not markers:
            return
        width = self.canvas.width()
        positions = {index: self.canvas.mapFromScene(self.view_box.mapViewToScene(pg.Point(x_value, 0))).x()
                     for index, x_value in markers}
        columns: list[tuple[float, float]] = []
        # Lay out the right-hand marker first, so the other marker's boxes can
        # move to its left side when both columns would overlap.
        for index, _x_value in sorted(markers, key=lambda marker: -positions[marker[0]]):
            boxes = self.visible_boxes(index)
            box_width = max(box.width() for box in boxes)
            left = positions[index] + MARKER_OFFSET
            if left + box_width > width - EDGE_MARGIN or any(left < right and left + box_width > start
                                                              for start, right in columns):
                left = positions[index] - MARKER_OFFSET - box_width
            left = max(EDGE_MARGIN, min(left, width - box_width - EDGE_MARGIN))
            columns.append((left, left + box_width))
            self._stack(boxes, int(left))

    def _place_at_points(self) -> None:
        """Place each box right of its own point, pushed down past boxes it would cover."""
        width, height = self.canvas.width(), self.canvas.height()
        wanted = []
        # Every box that has an item, so one hidden while its point was out of view returns.
        assigned = [box for index in (FIRST_MARKER, SECOND_MARKER)
                    for box in self.boxes[index][:len(self.items[index])]]
        for box in assigned:
            point = self._point(box.item)
            if point is None or not all(math.isfinite(value) for value in point):
                box.hide()
                continue
            box.show()
            left = point[0] + MARKER_OFFSET
            if left + box.width() > width - EDGE_MARGIN:
                left = point[0] - MARKER_OFFSET - box.width()
            left = max(EDGE_MARGIN, min(left, width - box.width() - EDGE_MARGIN))
            wanted.append((max(EDGE_MARGIN, point[1] - box.height() / 2), left, box))
        placed: list[tuple[float, float, float, float]] = []
        for top, left, box in sorted(wanted, key=lambda value: value[0]):
            moved = True
            while moved:
                moved = False
                for other_left, other_top, other_right, other_bottom in placed:
                    if (left < other_right and left + box.width() > other_left
                            and top < other_bottom + BOX_GAP and top + box.height() + BOX_GAP > other_top):
                        top, moved = other_bottom + BOX_GAP, True
            top = min(top, max(EDGE_MARGIN, height - box.height() - EDGE_MARGIN))
            box.move(int(left), int(top))
            placed.append((left, top, left + box.width(), top + box.height()))

    def _point(self, item: ValueBoxItem) -> tuple[float, float] | None:
        if item.anchor is not None:
            return item.anchor() if callable(item.anchor) else item.anchor
        if self.view_box is None or item.x_value is None or item.y_value is None:
            return None
        point = self.canvas.mapFromScene(self.view_box.mapViewToScene(pg.Point(item.x_value, item.y_value)))
        return float(point.x()), float(point.y())

    def _stack(self, boxes: list[ValueBox], left: int) -> None:
        height = self.canvas.height()

        def desired_top(box: ValueBox) -> float:
            # A NaN sample at the marker has no height; it would break the sort and stacking.
            if box.y_value is None or not math.isfinite(box.y_value):
                return EDGE_MARGIN
            y = self.canvas.mapFromScene(self.view_box.mapViewToScene(pg.Point(0, box.y_value))).y()
            return y - box.height() / 2

        ordered = sorted(boxes, key=desired_top)
        tops = []
        bottom = EDGE_MARGIN - BOX_GAP
        for box in ordered:
            top = max(desired_top(box), bottom + BOX_GAP, EDGE_MARGIN)
            tops.append(top)
            bottom = top + box.height()
        # Shift the stack up when it runs past the bottom of the canvas.
        overflow = bottom - (height - EDGE_MARGIN)
        for box, top in zip(ordered, tops):
            box.move(left, int(max(EDGE_MARGIN, top - max(0.0, overflow))))
