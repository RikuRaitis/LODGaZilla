"""Shared floating series legend used by LODGaZilla plot widgets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QObject, QPoint, QRect, QTimer, Qt, Signal
from shiboken6 import isValid
from PySide6.QtGui import QColor, QDrag, QPainter, QPen, QPixmap, QPolygon
from PySide6.QtWidgets import (QColorDialog, QFrame, QHBoxLayout, QLabel, QMenu, QScrollArea, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from src.data.plot_policy import SELECTION_MIME_TYPE
from src.gui.elide import middle_elide_text
from src.gui.plots.base import SERIES_MIME_TYPE, PlotBaseWidget, series_legend_name
from src.gui.plots.data_drop import exec_data_drag
from src.gui.plots.legend_groups import GROUP_MIME_TYPE
from src.gui.plots.series_hover import install_series_name_hover


@dataclass(frozen=True)
class LegendEntry:
    """One visible legend row and its optional source-series interaction."""

    key: int
    text: str
    color: str
    source: tuple[Path, str] | None = None
    color_editable: bool = True
    removable: bool = True
    draggable: bool = True
    group: str | None = None
    role: str = ""
    replaceable: bool = False
    component: str = "value"
    component_editable: bool = False
    move_removes_source: bool = True
    # A non-series row, such as a video filmstrip, drags this PlotSource as a selection.
    drag_source: object = None
    group_index: int | None = None
    group_sources: tuple | None = None


# Whether new cards show their legend: the Plot navigator's Legends button. Each card then
# shows or hides its own with its Legend button (PlotBaseWidget.legend_shown).
_LEGENDS_SHOWN = True


def legends_shown() -> bool:
    return _LEGENDS_SHOWN


def set_legends_shown(shown: bool) -> None:
    """Show or hide the legends of cards opened from now on; open cards keep their own."""
    global _LEGENDS_SHOWN
    _LEGENDS_SHOWN = bool(shown)


# Every Legend is this share of its card's width, from the Settings dialog; longer names
# are shortened in the middle with "...", like the data folder field.
LEGEND_WIDTH_PERCENT = [25]
LEGEND_MINIMUM_WIDTH = 80
# The selected series' rows: the selection blue, faint.
SELECTED_ROW_BACKGROUND = "rgba(33, 150, 243, 0.35)"
# Space between a Legend and its card's right edge, in pixels.
LEGEND_RIGHT_MARGIN = 4


def set_legend_width_percent(percent: int) -> None:
    """Use this Legend width; open legends follow on their next `reposition`."""
    LEGEND_WIDTH_PERCENT[0] = max(10, min(60, int(percent)))


def legend_width(card_width: int) -> int:
    """The Legend width for a card this wide, in pixels."""
    return max(LEGEND_MINIMUM_WIDTH, round(card_width * LEGEND_WIDTH_PERCENT[0] / 100))


class PlotLegendWidget(QFrame):
    """Theme-neutral floating legend with shared color/remove/drag affordances."""

    color_requested = Signal(int)
    remove_requested = Signal(int)
    move_requested = Signal(int)
    component_requested = Signal(int, str)

    def __init__(self, overlay_parent: QWidget, *, top_offset: int = 28) -> None:
        super().__init__(overlay_parent)
        self.overlay_parent = overlay_parent
        self.top_offset = top_offset
        self.rows: list[QWidget] = []
        self.entries: list[LegendEntry] = []
        self._headers: list[QWidget] = []
        self.group_headers: list[QWidget] = []
        self.group_model = None
        # The card's selected series: the row keys, or the group index, highlighted.
        self._selected_keys: frozenset = frozenset()
        self._selected_group: int | None = None
        self._group_header_indexes: list[int | None] = []
        self._content = self
        self._scroll = None
        self._scroll_direction = 0
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(60)
        self._scroll_timer.timeout.connect(self._scroll_during_drag)
        self.drop_hint = QFrame(self)
        self.drop_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.drop_hint.setStyleSheet("background: palette(highlight); border: none;")
        self.drop_hint.hide()
        self.swap_hint = QFrame(self)
        self.swap_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.swap_hint.hide()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { background: rgba(127, 127, 127, 40); "
            "border: 1px solid rgba(127, 127, 127, 128); }"
            "QToolButton { border: 1px solid rgba(127, 127, 127, 128); "
            "min-width: 16px; min-height: 16px; }"
        )
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(5, 4, 5, 4)
        self.layout.setSpacing(2)
        self.hide()
        # The width follows the card's width, so follow the overlay's resizes.
        overlay_parent.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        if watched is self.overlay_parent and event.type() == QEvent.Type.Resize and self.isVisible():
            self.reposition()
        return super().eventFilter(watched, event)

    def setVisible(self, visible: bool) -> None:  # noqa: N802 - Qt override
        # Cards show or hide their legend as their series change; the card's Legend button overrides that.
        self._wanted_visible = bool(visible)
        super().setVisible(self._wanted_visible and self._shown_by_card())

    def apply_legends_shown(self) -> None:
        super().setVisible(getattr(self, "_wanted_visible", False) and self._shown_by_card())
        if self.isVisible():
            self.reposition()

    def _shown_by_card(self) -> bool:
        """Whether this legend's card shows its legend; without a card, the default."""
        widget = self.parentWidget()
        while widget is not None and not isinstance(widget, PlotBaseWidget):
            widget = widget.parentWidget()
        return widget.legend_shown if widget is not None else _LEGENDS_SHOWN

    def set_group_model(self, model):
        if self._scroll is None:
            outer = self.layout
            outer.setContentsMargins(0, 0, 0, 0)
            self._content = QWidget()
            self.layout = QVBoxLayout(self._content)
            self.layout.setContentsMargins(5, 4, 5, 4)
            self.layout.setSpacing(2)
            self._scroll = QScrollArea(self)
            self._scroll.setFrameShape(QFrame.Shape.NoFrame)
            self._scroll.setWidgetResizable(True)
            self._scroll.setWidget(self._content)
            self._scroll.verticalScrollBar().valueChanged.connect(self.drop_hint.hide)
            self._scroll.verticalScrollBar().valueChanged.connect(self.swap_hint.hide)
            outer.addWidget(self._scroll)
        self.group_model = model
        model.changed.connect(self.refresh_groups)
        self.component_requested.connect(self._set_component)
        self.refresh_groups()

    def _set_component(self, key, component):
        from dataclasses import replace
        axis = self.group_model.axis(key)
        if axis is not None:
            self.group_model.replace(key, replace(axis, component=component))

    def refresh_groups(self, labels=None):
        model = self.group_model
        if model is None:
            return
        entries = []
        for index, group in enumerate(model.groups):
            for axis_index, (role, axis) in enumerate(zip(model.roles, group.axes)):
                key = index * model.width + axis_index
                text = "empty" if axis is None else series_legend_name(axis.path, axis.name)
                if axis is not None and axis.component == "time":
                    text += " [time]"
                if labels and key in labels:
                    text = labels[key]
                entries.append(LegendEntry(
                    key, text, group.color, (axis.path, axis.name) if axis else None,
                    removable=False, draggable=axis is not None,
                    group=f"{model.title} {index + 1}", role=role, replaceable=True,
                    component=axis.component if axis else "value", component_editable=model.allow_time and axis is not None,
                    group_index=index,
                ))
        self.set_entries(entries)

    def _swatch(self, entry, parent, callback):
        swatch = QToolButton(parent)
        swatch.setToolTip("Change group color" if entry.group else "Change series color")
        swatch.setEnabled(entry.color_editable)
        swatch.setStyleSheet(
            f"QToolButton {{ background: {entry.color}; "
            "border: 1px solid rgba(127, 127, 127, 128); min-width: 16px; min-height: 16px; }"
        )
        if entry.color_editable:
            swatch.clicked.connect(callback)
        return swatch

    def _choose_group_color(self, index):
        group = self.group_model.groups[index]
        color = QColorDialog.getColor(QColor(group.color), self, "Group color")
        if color.isValid():
            self.group_model.set_color(index, color.name())

    def set_entries(self, entries: list[LegendEntry]) -> None:
        self.clear_drop_hint()
        self.entries = entries
        self._clear_rows()
        previous_group = None
        for entry in entries:
            if entry.group is not None and entry.group != previous_group:
                header = QWidget(self._content)
                header.setObjectName("legendRow")
                header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                header_layout = QHBoxLayout(header)
                header_layout.setContentsMargins(0, 3, 0, 0)
                header_layout.setSpacing(4)
                index = entry.group_index
                callback = (lambda _checked=False, index=index: self._choose_group_color(index)) if self.group_model else (
                    lambda _checked=False, key=entry.key: self.color_requested.emit(key))
                header_layout.addWidget(self._swatch(entry, header, callback))
                group_entry = LegendEntry(
                    index or 0, entry.group, entry.color, removable=False,
                    group_index=index, group_sources=self.group_model.groups[index].axes if self.group_model else None,
                )
                label = _LegendLabel(group_entry, header)
                label.setStyleSheet("font-weight: bold; border: none; border-bottom: 1px solid palette(mid);")
                header_layout.addWidget(label, 1)
                self.layout.addWidget(header)
                self._headers.append(header)
                self.group_headers.append(header)
                self._group_header_indexes.append(index)
            previous_group = entry.group
            row = QWidget(self._content)
            row.setObjectName("legendRow")
            row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            label = _LegendLabel(entry, row)
            label.remove_requested.connect(self.remove_requested.emit)
            label.move_requested.connect(self.move_requested.emit)
            label.component_requested.connect(self.component_requested.emit)
            if entry.group is None:
                row_layout.addWidget(self._swatch(entry, row,
                    lambda _checked=False, key=entry.key: self.color_requested.emit(key)))
            if entry.role:
                role = _LegendLabel(entry, row)
                role.setText(entry.role)
                role.setStyleSheet("font-weight: bold; border: none;")
                roles = self.group_model.roles if self.group_model is not None else (entry.role,)
                role.setFixedWidth(max(24, max(role.fontMetrics().horizontalAdvance(name) for name in roles) + 8))
                role.component_requested.connect(self.component_requested.emit)
                role.move_requested.connect(self.move_requested.emit)
                row_layout.addWidget(role)
            row_layout.addWidget(label)
            self.layout.addWidget(row)
            self.rows.append(row)
        if self.group_model is not None and self.rows:
            # Keep an empty drop target below the final group.
            footer = QWidget(self._content)
            footer.setFixedHeight(12)
            self.layout.addWidget(footer)
            self._headers.append(footer)
        self._show_selection()
        if not self.rows:
            self.hide()
            return
        self.adjustSize()
        self.show()
        self.reposition()
        # A series drop can change the parent plot's available geometry only
        # after its current event returns.  Reposition once more after Qt has
        # applied that deferred layout; otherwise an event/state legend may
        # retain a zero-width or stale geometry until the card is resized.
        QTimer.singleShot(0, self, self.reposition)

    def set_selected(self, keys=(), group_index: int | None = None) -> None:
        """Highlight the selected series' rows (by entry key), or its whole group."""
        self._selected_keys = frozenset(keys)
        self._selected_group = group_index
        self._show_selection()

    def selected_rows(self) -> list[QWidget]:
        """The rows and group heading highlighted now."""
        rows = [row for row, entry in zip(self.rows, self.entries)
                if (self._selected_group is None and entry.group is None and entry.key in self._selected_keys)
                or (self._selected_group is not None and entry.group_index == self._selected_group)]
        rows += [header for header, index in zip(self.group_headers, self._group_header_indexes)
                 if self._selected_group is not None and index == self._selected_group]
        return rows

    def _show_selection(self) -> None:
        selected = set(map(id, self.selected_rows()))
        for row in [*self.rows, *self.group_headers]:
            row.setProperty("selectedSeries", id(row) in selected)
            row.setStyleSheet(f"QWidget#legendRow {{ background: {SELECTED_ROW_BACKGROUND}; }}"
                              if id(row) in selected else "")

    def show_drop_hint(self, point: QPoint) -> int:
        """Outline a hovered row, or mark the insertion gap between rows."""
        position = next((i for i, row in enumerate(self.rows)
                         if point.y() < row.geometry().center().y()), len(self.rows))
        hovered = next((row for row in self.rows if self._row_rect(row).contains(point)), None)
        if hovered is not None:
            self._show_row_outline(self.drop_hint, hovered)
        else:
            y = (self.rows[position].y() - 2 if position < len(self.rows)
                 else self.rows[-1].geometry().bottom() + 1 if self.rows else 4)
            self.drop_hint.setStyleSheet("background: palette(highlight); border: none;")
            self.drop_hint.setGeometry(3, max(1, min(y, self.height() - 3)), self.width() - 6, 2)
            self.drop_hint.show()
            self.drop_hint.raise_()
        # Event/state legends can contain several rows for one source.
        return self.entries[position].key if position < len(self.entries) else (
            max((entry.key for entry in self.entries), default=-1) + 1)

    def replacement_at(self, point: QPoint) -> LegendEntry | None:
        return next((entry for row, entry in zip(self.rows, self.entries)
                     if entry.replaceable and self._row_rect(row).contains(point)), None)

    def _row_rect(self, row):
        return QRect(row.mapTo(self, QPoint()), row.size())

    def scroll_for_drag(self, point):
        if self._scroll is None:
            return
        local = self._scroll.viewport().mapFrom(self, point)
        self._scroll_direction = -1 if local.y() < 14 else 1 if local.y() > self._scroll.viewport().height() - 14 else 0
        if self._scroll_direction:
            self._scroll_timer.start()
        else:
            self._scroll_timer.stop()

    def _scroll_during_drag(self):
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.value() + self._scroll_direction * 12)

    def show_group_insertion_hint(self, point: QPoint) -> int:
        boundary = next((index for index, header in enumerate(self.group_headers)
                         if point.y() < self._row_rect(header).bottom()), len(self.group_headers))
        y = (self._row_rect(self.group_headers[boundary]).top() - 1 if boundary < len(self.group_headers)
             else self._row_rect(self.rows[-1]).bottom() + 3)
        self.drop_hint.setStyleSheet("background: palette(highlight); border: none;")
        self.drop_hint.setGeometry(3, y, self.width() - 6, 2)
        self.drop_hint.show()
        self.drop_hint.raise_()
        return boundary

    def show_replacement_hint(self, key: int, swap_key: int | None = None) -> None:
        self.swap_hint.hide()
        for hint, row_key in ((self.drop_hint, key), (self.swap_hint, swap_key)):
            if row_key is None:
                continue
            row = next(row for row, entry in zip(self.rows, self.entries) if entry.key == row_key)
            border = "dashed" if swap_key is not None else "solid"
            self._show_row_outline(hint, row, border)

    def _show_row_outline(self, hint, row, border="solid"):
        hint.setStyleSheet(f"background: transparent; border: 2px {border} palette(highlight);")
        hint.setGeometry(self._row_rect(row).adjusted(-2, -1, 2, 1))
        hint.show()
        hint.raise_()

    def clear_drop_hint(self) -> None:
        self._scroll_timer.stop()
        self.drop_hint.hide()
        self.swap_hint.hide()

    def _clear_rows(self) -> None:
        for row in self.rows + self._headers:
            self.layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows.clear()
        self._headers.clear()
        self.group_headers.clear()
        self._group_header_indexes.clear()

    def card_width(self) -> int:
        """The width of the card this legend belongs to."""
        card = self.overlay_parent
        while card is not None and not isinstance(card, PlotBaseWidget):
            card = card.parentWidget()
        return (card or self.overlay_parent).width()

    def reposition(self) -> None:
        if not self.rows:
            return
        # The same share of the card's width on every card; names that do not fit are elided.
        width = min(legend_width(self.card_width()), max(LEGEND_MINIMUM_WIDTH, self.overlay_parent.width() - 8))
        if self._scroll is not None:
            self.layout.activate()
            size = self.layout.sizeHint()
            self._content.setMinimumHeight(self.layout.minimumSize().height())
            available = max(40, self.overlay_parent.height() - self.top_offset - 4)
            self.setFixedSize(width, min(size.height() + 2, available))
        else:
            self.layout.activate()
            self.setFixedSize(width, self.layout.sizeHint().height())
        y = max(4, min(self.top_offset, self.overlay_parent.height() - self.height() - 4))
        self.move(self._right_aligned_x(), y)
        self.raise_()

    def _right_aligned_x(self) -> int:
        """At the card's right edge, with a small margin, on every card."""
        card = self.overlay_parent
        while card is not None and not isinstance(card, PlotBaseWidget):
            card = card.parentWidget()
        overlay_right = self.overlay_parent.width()
        margin = LEGEND_RIGHT_MARGIN
        if card is not None:
            # The card's frame already keeps the overlay a few pixels from its edge.
            card_right = self.overlay_parent.mapFrom(card, QPoint(card.width(), 0)).x()
            margin = max(0, LEGEND_RIGHT_MARGIN - max(0, card_right - overlay_right))
        return max(4, overlay_right - self.width() - margin)


def _swap_cursor():
    """Pointer with opposing arrows, independent of available symbol fonts."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(Qt.GlobalColor.white, 2))
    painter.setBrush(Qt.GlobalColor.black)
    painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in
                                  ((1, 1), (1, 18), (5, 14), (9, 22), (12, 20), (8, 12), (16, 12))]))
    arrows = (((15, 19), (29, 19), (25, 15), (29, 19), (25, 23)),
              ((29, 26), (15, 26), (19, 22), (15, 26), (19, 30)))
    for color, width in ((Qt.GlobalColor.white, 4), (Qt.GlobalColor.black, 2)):
        painter.setPen(QPen(color, width))
        for points in arrows:
            painter.drawPolyline(QPolygon([QPoint(x, y) for x, y in points]))
    painter.end()
    return pixmap


class LegendDragSource(QObject):
    """Keep the drag alive when its source legend is rebuilt during a drop."""

    def __init__(self, plot, entry):
        super().__init__()
        self.source_plot = plot
        self.entry = entry
        self.handled = False
        model = getattr(plot, "grouped_series", None)
        self.group = (model.groups[entry.group_index]
                      if model is not None and entry.group_index is not None else None)
        self.axis_index = entry.key % model.width if self.group is not None else None
        self.axis = self.group.axes[self.axis_index] if self.group is not None else None

    def finish_group_move(self):
        """Clear the original assignment even if the drop rebuilt the legend."""
        if self.handled or self.group is None:
            return
        self.handled = True
        if not isValid(self.source_plot):
            return
        model = self.source_plot.grouped_series
        # Inserting a group can change indices before QDrag.exec returns.
        index = next((i for i, group in enumerate(model.groups) if group is self.group), None)
        if index is None:
            return
        if self.entry.group_sources is not None:
            model.remove(index)
        elif self.group.axes[self.axis_index] == self.axis:
            model.replace(index * model.width + self.axis_index, None)


class _LegendLabel(QLabel):
    remove_requested = Signal(int)
    move_requested = Signal(int)
    component_requested = Signal(int, str)

    def __init__(self, entry: LegendEntry, parent: QWidget) -> None:
        super().__init__(parent)
        self._full_text = ""
        # The row gets the legend's width; a name that does not fit is shortened in the middle.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(8)
        self.setText(entry.text)
        self.entry = entry
        # Resting the pointer on a row shows its whole name, also when it is shortened.
        install_series_name_hover(self, lambda _position: self.hover_name())
        self.source_plot = parent
        while self.source_plot is not None and not isinstance(self.source_plot, PlotBaseWidget):
            self.source_plot = self.source_plot.parentWidget()
        self._press_position: QPoint | None = None
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        if entry.group_sources is None and entry.replaceable and entry.source is None:
            # An empty axis row.
            self.setStyleSheet("font-style: italic; color: palette(mid); border: none;")
        if entry.draggable:
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        self._full_text = str(text)
        self._show_elided_text()

    def full_text(self) -> str:
        """The row's whole name, also when it is shown shortened."""
        return self._full_text

    def hover_name(self) -> str | None:
        """The row's whole name; an empty axis row has none."""
        entry = self.entry
        if entry.replaceable and entry.source is None and entry.group_sources is None:
            return None
        return entry.text or None

    def is_elided(self) -> bool:
        return super().text() != self._full_text

    def _show_elided_text(self) -> None:
        width = self.contentsRect().width()
        shown = middle_elide_text(self._full_text, self.fontMetrics(), width) if width > 0 else self._full_text
        if shown != super().text():
            super().setText(shown)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._show_elided_text()

    def contextMenuEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.entry.group_sources is not None:
            menu = QMenu(self)
            # Deleted after use; otherwise each right-click leaves one alive with the row.
            menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            menu.addAction("Remove group", lambda: self.source_plot.grouped_series.remove(self.entry.group_index))
            menu.exec(event.globalPos())
            return
        if not self.entry.removable and not self.entry.component_editable:
            event.ignore()
            return
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if self.entry.component_editable:
            for component, title in (("value", "Values"), ("time", "Time")):
                action = menu.addAction(title)
                action.setCheckable(True)
                action.setChecked(component == self.entry.component)
                action.triggered.connect(lambda _checked=False, value=component:
                                         self.component_requested.emit(self.entry.key, value))
        if self.entry.removable:
            menu.addAction("Remove series from plot", lambda: self.remove_requested.emit(self.entry.key))
        menu.exec(event.globalPos())

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.entry.draggable and event.button() == Qt.MouseButton.LeftButton:
            self._press_position = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if (
            self.entry.draggable
            and (self.entry.source is not None or self.entry.group_sources is not None
                 or self.entry.drag_source is not None)
            and self._press_position is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.position().toPoint() - self._press_position).manhattanLength() >= 10
        ):
            mime_data = QMimeData()
            entry = self.entry
            if entry.drag_source is not None:
                mime_data.setData(SELECTION_MIME_TYPE, json.dumps([entry.drag_source.payload()]).encode("utf-8"))
            elif entry.group_sources is not None:
                axes = [None if axis is None else {"h5_path": str(axis.path), "series_name": axis.name,
                                                   "component": axis.component} for axis in entry.group_sources]
                mime_data.setData(GROUP_MIME_TYPE, json.dumps({"axes": axes, "color": entry.color}).encode("utf-8"))
            else:
                h5_path, series_name = entry.source
                mime_data.setData(SERIES_MIME_TYPE, json.dumps({"h5_path": str(h5_path), "series_name": series_name,
                                                              "component": entry.component}).encode("utf-8"))
            source = LegendDragSource(self.source_plot, self.entry)
            drag = QDrag(source)
            drag.setMimeData(mime_data)
            actions = Qt.DropAction.MoveAction if self.entry.move_removes_source else Qt.DropAction.CopyAction
            if entry.group_index is not None and entry.group_sources is None:
                # Reserve a separate native action/cursor for same-card swaps.
                # The drop router selects it only while Ctrl is held over an axis.
                actions |= Qt.DropAction.LinkAction
                drag.setDragCursor(_swap_cursor(), Qt.DropAction.LinkAction)
            result = exec_data_drag(drag, actions)
            # Internal reordering/replacement has already updated the source.
            if result == Qt.DropAction.MoveAction:
                source.finish_group_move()
            if not isValid(self):
                return
            if result == Qt.DropAction.MoveAction and not source.handled and entry.group_sources is None:
                self.move_requested.emit(self.entry.key)
            self._press_position = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._press_position = None
        if self.entry.draggable:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        event.accept()
