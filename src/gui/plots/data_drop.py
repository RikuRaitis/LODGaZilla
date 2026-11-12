"""Navigator/legend data drops, insertion hints and delayed card selection."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QWidget

from src.gui.plots.base import SERIES_MIME_TYPE, series_from_mime
from src.jobs.plot_drop import DropMetadataTask
from src.data.xyz import AxisSource
from src.gui.plots.legend_groups import GROUP_MIME_TYPE
from src.data.plot_policy import CARD_TITLES, HORIZONTAL_CARD_TYPES, PlotSource, SELECTION_MIME_TYPE, plan_cards
from src.gui.plots.workspace_layout import side_drop_position

VIDEO_MIME_TYPE = "application/x-lodgazilla-video"
IMAGE_MIME_TYPE = "application/x-lodgazilla-image"


def exec_data_drag(drag, actions):
    """Install workspace routing only for the lifetime of a native data drag."""
    controllers = [controller for window in QApplication.topLevelWidgets()
                   for controller in window.findChildren(DataDropController)
                   if controller.workspace.isVisible()]
    for controller in controllers:
        controller.start()
    try:
        return drag.exec(actions)
    finally:
        for controller in controllers:
            controller.stop()


class _GuardedDropMetadataTask(DropMetadataTask):
    """Always report completion so one failed inspection cannot block later drags."""

    def run(self):
        try:
            super().run()
        except Exception:  # noqa: BLE001 - any inspection failure means "no compatible cards"
            self.signals.inspected.emit(self.key, ())
            self.signals.completed.emit(self.key, ())


class CardDropSelector(QFrame):
    """Equal-width choices with vertical dividers; drag routing stays underneath."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("QFrame { background: palette(base); border: 2px solid palette(highlight); }")
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(2, 2, 2, 2)
        self.layout.setSpacing(0)
        self.options = ()
        self.labels = []
        self.hide()

    def set_options(self, options):
        if self.options == options:
            return
        self.options = options
        for label in self.labels:
            self.layout.removeWidget(label)
            label.deleteLater()
        self.labels = []
        for option in options:
            label = QLabel(CARD_TITLES[option], self)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            self.layout.addWidget(label, 1)
            self.labels.append(label)

    def choice_at(self, point):
        if not self.rect().contains(point) or not self.options:
            return None
        index = min(len(self.options) - 1, max(0, point.x()) * len(self.options) // max(1, self.width()))
        return self.options[index]

    def highlight(self, choice):
        for option, label in zip(self.options, self.labels):
            selected = option == choice
            label.setStyleSheet(
                "border: none; border-right: 1px solid palette(mid); padding: 5px; "
                + ("background: palette(highlight); color: palette(highlighted-text);" if selected
                   else "background: palette(base); color: palette(text);")
            )


class DataDropController(QObject):
    """Route all card children consistently, including WebEngine/media viewports."""

    create_requested = Signal(object, str, int)
    # Payload, target card and "left"/"right": open the default card beside target.
    create_beside_requested = Signal(object, object, str)

    def __init__(self, workspace, delay_ms=1000):
        super().__init__(workspace)
        self.workspace = workspace
        self.stack = workspace.splitter
        self.delay_ms = delay_ms
        self.line = QFrame(workspace.viewport())
        self.line.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.line.setStyleSheet("background: palette(highlight); border: none;")
        self.line.hide()
        self.card_hint = QFrame(workspace.viewport())
        self.card_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.card_hint.setStyleSheet("background: transparent; border: 2px solid palette(highlight);")
        self.card_hint.hide()
        self.selector = CardDropSelector(workspace.viewport())
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._show_selector)
        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.timeout.connect(self.finish)
        self._key = None
        self.payload = None
        self.options = ()
        self.gap = None
        self.legend = None
        self._job = None
        self._point = QPoint()
        self._delay_elapsed = False
        self._handled_drag_targets: set[QWidget] = set()
        workspace.verticalScrollBar().valueChanged.connect(self.clear_preview)

    def start(self):
        self.finish()
        self._handled_drag_targets.clear()
        QApplication.instance().installEventFilter(self)

    def stop(self):
        QApplication.instance().removeEventFilter(self)
        self.finish()
        self._handled_drag_targets.clear()

    def clear_preview(self, *_args):
        self.timer.stop()
        self.line.hide()
        self.card_hint.hide()
        self.selector.hide()
        self.gap = None
        self._delay_elapsed = False
        if self.legend is not None:
            self.legend.clear_drop_hint()
            self.legend = None

    def finish(self):
        self._leave_timer.stop()
        self.clear_preview()
        self._key = None
        self.payload = None
        self.options = ()

    def _read_payload(self, mime):
        kind = next((kind for kind in (SELECTION_MIME_TYPE, GROUP_MIME_TYPE, SERIES_MIME_TYPE, IMAGE_MIME_TYPE, VIDEO_MIME_TYPE)
                     if mime.hasFormat(kind)), None)
        if kind is None:
            return False
        key = (kind, bytes(mime.data(kind)))
        if key == self._key:
            return self.payload is not None
        self.finish()
        self._key = key
        if kind == SELECTION_MIME_TYPE:
            try:
                items = tuple(PlotSource.from_payload(item) for item in json.loads(key[1].decode("utf-8")))
                if not items:
                    return False
                self.payload = {"items": items}
            except (KeyError, TypeError, ValueError):
                return False
            self._start_metadata()
        elif kind == GROUP_MIME_TYPE:
            try:
                data = json.loads(key[1].decode("utf-8"))
                axes = tuple(None if item is None else AxisSource(Path(item["h5_path"]), str(item["series_name"]),
                                                                 item.get("component", "value")) for item in data["axes"])
                if not axes or not any(axis is not None for axis in axes):
                    return False
                self.payload = {"axes": axes, "color": data.get("color")}
            except (KeyError, TypeError, ValueError):
                return False
            self._start_metadata()
        elif kind == SERIES_MIME_TYPE:
            series = series_from_mime(mime)
            if series is None:
                return False
            self.payload = {"series": series,
                            "component": json.loads(key[1].decode("utf-8")).get("component", "value"),
                            "gnss_group": mime.hasFormat("application/x-lodgazilla-gnss-group")}
            if self.payload["component"] not in ("value", "time"):
                self.payload = None
                return False
            self._start_metadata()
        else:
            try:
                payload = json.loads(key[1].decode("utf-8"))
                card_type = "image" if kind == IMAGE_MIME_TYPE else "video"
                self.payload = {"folder": Path(payload["folder"]),
                                card_type + "_path": Path(payload[card_type + "_path"]),
                                "offset_seconds": float(payload.get("offset_seconds", 0.0))}
            except (KeyError, TypeError, ValueError):
                self.payload = None
                return False
            self.options = (card_type,)
        return True

    def _start_metadata(self):
        # At most one inspection is in flight; a later drag supersedes its result.
        if self._job is not None or not self.payload or not any(key in self.payload for key in ("series", "axes", "items")):
            return
        entries = (self.payload["items"] if "items" in self.payload else
                   [(axis.path, axis.name) for axis in self.payload["axes"] if axis is not None]
                   if "axes" in self.payload else self.payload["series"])
        self._job = _GuardedDropMetadataTask(self._key, entries, selection="items" in self.payload)
        self._job.signals.inspected.connect(self._metadata_inspected)
        self._job.signals.completed.connect(self._metadata_ready)
        QThreadPool.globalInstance().start(self._job)

    def _metadata_inspected(self, key, items):
        if key == self._key and self.payload is not None:
            self.payload["inspected"] = items

    def _metadata_ready(self, key, options):
        self._job = None
        if key != self._key:
            self._start_metadata()
            return
        self.options = options
        if self.gap is not None and self._delay_elapsed:
            self._show_selector()

    def destination(self, point):
        """Return a card or a gap row index, respecting horizontal card rows."""
        viewport = self.workspace.viewport()
        if not viewport.rect().contains(point):
            return None, None, 0
        if self.selector.isVisible() and self.selector.geometry().contains(point):
            return None, self.gap, self.line.y()
        stack_point = self.stack.mapFrom(viewport, point)
        for index in range(self.stack.count()):
            row = self.stack.widget(index)
            rect = QRect(row.pos(), row.size())
            # Include a small margin at row edges so the gap is easy to target.
            if stack_point.y() <= rect.top() + 4:
                y = row.mapTo(viewport, QPoint()).y()
                return None, index, y
            if stack_point.y() < rect.bottom() - 4:
                for card in self.stack.plots():
                    card_rect = QRect(card.mapTo(self.stack, QPoint()), card.size())
                    if card_rect.contains(stack_point):
                        return card, None, 0
                # A horizontal divider is not a gap between card rows.
                return None, None, 0
            if stack_point.y() <= rect.bottom() + self.stack.handleWidth():
                return None, index + 1, row.mapTo(viewport, QPoint(0, row.height())).y()
        y = (self.stack.mapTo(viewport, QPoint(0, self.stack.height())).y()
             if self.stack.count() else 4)
        return None, self.stack.count(), y

    def _creates_side_by_side_cards(self):
        """Whether the dragged data opens only card types that can share a row."""
        payload = self.payload
        if not payload or "axes" in payload or payload.get("component") == "time":
            return False
        if "image_path" in payload or "video_path" in payload:
            return True
        items = payload.get("inspected")
        plans = plan_cards(items) if items else ()
        return bool(plans) and all(plan.kind in HORIZONTAL_CARD_TYPES for plan in plans)

    def _side_position(self, card, point):
        """Return "left"/"right" when a new card can be placed beside ``card``."""
        if not self.stack.can_add_beside(card) or not self._creates_side_by_side_cards():
            return None
        legend = getattr(card, "legend", None)
        if legend is not None and legend.isVisible() and legend.rect().contains(
                legend.mapFrom(self.workspace.viewport(), point)):
            return None
        return side_drop_position(card.size(), card.mapFrom(self.workspace.viewport(), point))

    def _show_side_hint(self, card, position):
        rect = QRect(card.mapTo(self.workspace.viewport(), QPoint()), card.size())
        x = rect.left() + 1 if position == "left" else rect.right() - 3
        self.line.setGeometry(x, rect.top() + 1, 3, max(1, rect.height() - 2))
        self.line.show()
        self.line.raise_()

    def _show_card_hint(self, card):
        rect = QRect(card.mapTo(self.workspace.viewport(), QPoint()), card.size())
        self.card_hint.setGeometry(rect.adjusted(1, 1, -1, -1))
        self.card_hint.show()
        self.card_hint.raise_()

    def update_preview(self, point, source=None, modifiers=Qt.KeyboardModifier.NoModifier):
        self._point = point
        card, gap, y = self.destination(point)
        if card is not None:
            self.clear_preview()
            side = self._side_position(card, point)
            if side is not None:
                self._show_side_hint(card, side)
                return "beside", card, side
            if self.payload and "items" in self.payload:
                if getattr(source, "source_plot", None) is card:
                    # A filmstrip row dropped back on its own card stays where it is.
                    return None
                return self._selection_card_preview(card, point)
            if not self.payload or not ("series" in self.payload or "axes" in self.payload):
                return None
            internal = getattr(source, "source_plot", None) is card
            origin = getattr(source, "entry", None)
            legend = getattr(card, "legend", None)
            group_drag = "axes" in self.payload
            has_time = (any(axis is not None and axis.component == "time" for axis in self.payload["axes"])
                        if group_drag else self.payload.get("component") == "time")
            index = -1
            local = legend.mapFrom(self.workspace.viewport(), point) if legend is not None and legend.isVisible() else None
            over_legend = local is not None and legend.rect().contains(local)
            if card.accepts_axis_drop:
                if "line" not in self.options or (has_time and not card.accepts_time_axis_drop):
                    return None
                if over_legend:
                    self.legend = legend
                    legend.scroll_for_drag(local)
                    entry = legend.replacement_at(local)
                    if entry is not None and not group_drag:
                        if internal and (origin is None or origin.key == entry.key):
                            return None
                        swap = internal and bool(modifiers & Qt.KeyboardModifier.ControlModifier)
                        legend.show_replacement_hint(entry.key, origin.key if swap else None)
                        return "swap" if swap else "replace", card, entry.key
                    index = legend.show_group_insertion_hint(local)
                else:
                    index = len(card.grouped_series.groups)
                    self._show_card_hint(card)
                return "group_reorder" if internal and group_drag else "groups", card, index
            if legend is not None and legend.isVisible():
                if over_legend:
                    self.legend = legend
                    if (not card.accepts_series_drop or card.drop_card_type not in self.options
                            or has_time):
                        return None
                    if internal and origin is None:
                        return None
                    index = legend.show_drop_hint(local)
                    if internal:
                        return "reorder", card, index
            if (internal or not card.accepts_series_drop or card.drop_card_type not in self.options
                    or has_time):
                return None
            if group_drag and card.drop_card_type == "frequency" and sum(axis is not None for axis in self.payload["axes"]) != 1:
                self.clear_preview()
                return None
            if not over_legend:
                self._show_card_hint(card)
            return "group_series" if group_drag else "card", card, index
        if gap is None or (self.payload and (self.payload.get("component") == "time" or "axes" in self.payload)):
            self.clear_preview()
            return None
        if gap != self.gap:
            self.clear_preview()
            self.gap = gap
            self.timer.start(self.delay_ms)
        viewport = self.workspace.viewport()
        if not self.selector.isVisible():
            self.line.setGeometry(4, max(1, min(y, viewport.height() - 4)), max(1, viewport.width() - 8), 3)
            self.line.show()
            self.line.raise_()
        choice = None
        if self.selector.isVisible():
            choice = self.selector.choice_at(point - self.selector.pos())
            self.selector.highlight(choice)
        return "gap", gap, choice

    def _selection_card_preview(self, card, point):
        if (card.drop_card_type not in self.options or
                not (card.accepts_axis_drop or card.accepts_series_drop)):
            return None
        legend = getattr(card, "legend", None)
        index = -1
        local = legend.mapFrom(self.workspace.viewport(), point) if legend is not None and legend.isVisible() else None
        if local is not None and legend.rect().contains(local):
            self.legend = legend
            if card.accepts_axis_drop:
                legend.scroll_for_drag(local)
                index = legend.show_group_insertion_hint(local)
            else:
                index = legend.show_drop_hint(local)
        else:
            self._show_card_hint(card)
        return "selection_card", card, index

    def _show_selector(self):
        self._delay_elapsed = True
        if self.gap is None or not self.options:
            return
        viewport = self.workspace.viewport()
        height = min(72, viewport.height())
        # Put the selector under the pointer, even in a large empty workspace.
        y = max(0, min(self._point.y() - height // 2, viewport.height() - height))
        self.selector.set_options(self.options)
        self.selector.setGeometry(4, y, max(1, viewport.width() - 8), height)
        self.selector.show()
        self.selector.raise_()
        self.selector.highlight(self.selector.choice_at(self._point - self.selector.pos()))

    def apply_drop(self, destination, payload, source=None):
        """Apply the operation advertised by the preview before ending the drag."""
        target_kind, target, index = destination
        if target_kind == "selection_card":
            self.workspace.add_plot_inputs_to_card(payload["inspected"], target, index)
        elif target_kind in {"reorder", "swap"}:
            # Source legend rows may be destroyed by the operation below.
            source.handled = True
            if target_kind == "reorder":
                target.reorder_series(source.entry.key, index)
            else:
                target.swap_legend_series(source.entry.key, index)
        elif target_kind == "group_reorder":
            source.handled = True
            target.grouped_series.reorder(source.entry.group_index, index)
        elif target_kind == "groups":
            axes = payload.get("axes") or (AxisSource(*payload["series"], payload.get("component", "value")),)
            target.add_legend_group(axes, index, payload.get("color"))
        elif target_kind == "group_series":
            for axis in payload["axes"]:
                if axis is not None:
                    if index < 0:
                        target.series_dropped.emit(target, axis.path, axis.name)
                    else:
                        target.series_inserted.emit(target, axis.path, axis.name, index)
                        index += 1
        elif target_kind == "replace":
            target.replace_legend_series(index, payload["series"], payload.get("component", "value"))
        elif target_kind == "beside":
            self.create_beside_requested.emit(payload, target, index)
        elif target_kind == "card":
            if index < 0:
                target.series_dropped.emit(target, *payload["series"])
            else:
                target.series_inserted.emit(target, *payload["series"], index)
        else:
            self.create_requested.emit(payload, index or "", target)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in {QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.DragLeave,
                        QEvent.Type.Drop} or not isinstance(watched, QWidget):
            return False
        viewport = self.workspace.viewport()
        inside = watched is viewport or viewport.isAncestorOf(watched)
        if kind == QEvent.Type.DragLeave:
            if watched in self._handled_drag_targets or (inside and self.payload is not None):
                self._handled_drag_targets.discard(watched)
                # Qt leaves one child before entering the next. Preserve the
                # selector across that transition, but clear it on cancel/exit.
                self._leave_timer.start(0)
                # We intercepted entry, so the child's graphics scene must not
                # receive an unmatched leave event.
                event.accept()
                return True
            return False
        if not inside:
            return False
        if not any(event.mimeData().hasFormat(mime) for mime in
                   (SELECTION_MIME_TYPE, GROUP_MIME_TYPE, SERIES_MIME_TYPE, IMAGE_MIME_TYPE, VIDEO_MIME_TYPE)):
            return False
        self._handled_drag_targets.add(watched)
        self._leave_timer.stop()
        if not self._read_payload(event.mimeData()):
            event.ignore()
            return True
        # The receiver is a descendant of the workspace viewport. Map upward
        # from it; mapFrom() requires its argument to be an ancestor instead.
        point = watched.mapTo(viewport, event.position().toPoint())
        destination = self.update_preview(point, event.source(), event.modifiers())
        # Accept entry to receive subsequent moves across the same child widget.
        # Moves/drop decide the native copy/move versus forbidden cursor.
        if kind == QEvent.Type.DragEnter or destination is not None:
            origin = getattr(event.source(), "entry", None)
            if origin is not None and origin.group_index is not None and origin.group_sources is None:
                # Ctrl chooses a swap, never a copy. LinkAction carries the
                # dedicated swap cursor; all other accepted axis drops move.
                action = (Qt.DropAction.LinkAction if destination is not None and destination[0] == "swap"
                          and event.possibleActions() & Qt.DropAction.LinkAction else Qt.DropAction.MoveAction)
                event.setDropAction(action)
                event.accept()
            else:
                event.acceptProposedAction()
        else:
            event.ignore()
        if kind == QEvent.Type.DragEnter:
            return True
        if kind == QEvent.Type.Drop:
            payload = self.payload
            self.finish()
            if destination is not None:
                self.apply_drop(destination, payload, event.source())
        return True
