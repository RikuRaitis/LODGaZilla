"""Series names shown while the pointer rests on a series, in every card and Legend.

The name appears after the pointer has rested for the delay set in the Settings
dialog, and disappears as soon as the pointer moves again.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QPalette
from PySide6.QtWidgets import QFrame, QLabel, QWidget
from shiboken6 import isValid

# How long the pointer rests before the name is shown, in milliseconds; from the Settings dialog.
SERIES_NAME_HOVER_DELAY_MS = [700]
# How close the pointer must be to a drawn line or point, in pixels.
HIT_TOLERANCE_PIXELS = 6.0

# A resolver gets the pointer position in the watched widget's coordinates and calls
# `done` with the name under it, or None; it may answer later (map cards ask their page).
Resolver = Callable[[QPoint, Callable[[str | None], None]], None]


def set_series_name_hover_delay(milliseconds: int) -> None:
    SERIES_NAME_HOVER_DELAY_MS[0] = max(100, min(5_000, int(milliseconds)))


class SeriesNameTip(QLabel):
    """The shown name: a small tooltip-styled label beside the pointer."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setFrameShape(QFrame.Shape.Box)
        self.setMargin(4)
        self.setForegroundRole(QPalette.ColorRole.ToolTipText)
        self.setBackgroundRole(QPalette.ColorRole.ToolTipBase)
        self.setAutoFillBackground(True)

    def show_name(self, name: str, global_position: QPoint) -> None:
        self.setText(name)
        self.adjustSize()
        self.move(global_position + QPoint(12, 16))
        self.show()
        self.raise_()


class SeriesNameHover(QObject):
    """Watches one widget and shows the name its resolver finds under a resting pointer."""

    def __init__(self, widget: QWidget, resolver: Resolver, *, event_widget: QWidget | None = None) -> None:
        super().__init__(widget)
        self.widget = widget
        self.resolver = resolver
        self._request = 0
        self._position: QPoint | None = None
        self._tip: SeriesNameTip | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._resolve)
        self.watch(event_widget or widget)

    def watch(self, widget: QWidget) -> None:
        """Take pointer events from this widget too, such as a web view's input child."""
        widget.setMouseTracking(True)
        widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        kind = event.type()
        if kind in (QEvent.Type.MouseMove, QEvent.Type.HoverMove):
            self.hide_name()
            buttons = getattr(event, "buttons", None)
            if buttons is None or not buttons():
                local = event.position().toPoint() if hasattr(event, "position") else watched.mapFromGlobal(QCursor.pos())
                self._position = local if watched is self.widget else self.widget.mapFromGlobal(watched.mapToGlobal(local))
                self._timer.start(SERIES_NAME_HOVER_DELAY_MS[0])
        elif kind in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick,
                      QEvent.Type.Wheel, QEvent.Type.Hide, QEvent.Type.DragEnter, QEvent.Type.KeyPress):
            self.hide_name()
        return False

    def hide_name(self) -> None:
        """Hide the name at once, and forget an answer still on its way."""
        self._timer.stop()
        self._request += 1
        if self._tip is not None:
            self._tip.hide()

    def _resolve(self) -> None:
        if self._position is None or not self.widget.isVisible():
            return
        self._request += 1
        request, position = self._request, QPoint(self._position)
        self.resolver(position, lambda name: self._show(request, position, name))

    def _show(self, request: int, position: QPoint, name: str | None) -> None:
        # A move since the question was asked makes the answer stale; a map page can
        # answer after its card has closed.
        if request != self._request or not name or not isValid(self) or not isValid(self.widget):
            return
        if not self.widget.isVisible():
            return
        if self._tip is None:
            self._tip = SeriesNameTip(self.widget)
        self._tip.show_name(name, self.widget.mapToGlobal(position))


def install_series_name_hover(widget: QWidget, name_at: Callable[[QPoint], str | None], **options) -> SeriesNameHover:
    """Show names from a function that answers at once."""
    return SeriesNameHover(widget, lambda position, done: done(name_at(position)), **options)


# pyqtgraph canvases

def _item_points(item) -> tuple[np.ndarray, np.ndarray] | None:  # type: ignore[no-untyped-def]
    """An item's drawn points in scene pixels, or None when it has none."""
    try:
        if not item.isVisible() or item.scene() is None:
            return None
        x, y = item.getData()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    if x is None or y is None or not len(x):
        return None
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    transform = item.sceneTransform()
    return (transform.m11() * x + transform.m21() * y + transform.dx(),
            transform.m12() * x + transform.m22() * y + transform.dy())


def distance_squared(points, scene_x: float, scene_y: float, *, lines: bool) -> float:  # type: ignore[no-untyped-def]
    """The squared pixel distance from the pointer to the nearest point, or line between points."""
    x, y = points[0] - scene_x, points[1] - scene_y
    distances = x * x + y * y
    if lines and len(x) > 1:
        vx, vy = np.diff(x), np.diff(y)
        length = vx * vx + vy * vy
        t = np.clip(np.divide(-(x[:-1] * vx + y[:-1] * vy), length, out=np.zeros_like(length), where=length > 0), 0, 1)
        distances = np.concatenate((distances, (x[:-1] + t * vx) ** 2 + (y[:-1] + t * vy) ** 2))
    finite = distances[np.isfinite(distances)]
    return float(finite.min()) if len(finite) else float("inf")


def _bar_contains(item, scene_position) -> bool:  # type: ignore[no-untyped-def]
    try:
        if not item.isVisible() or item.scene() is None:
            return False
        local = item.mapFromScene(scene_position)
        opts = item.opts
        x0, x1, y0, y1 = (np.asarray(opts[key], dtype=float) for key in ("x0", "x1", "y0", "y1"))
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
        return False
    return bool(np.any((np.minimum(x0, x1) <= local.x()) & (np.maximum(x0, x1) >= local.x())
                       & (np.minimum(y0, y1) <= local.y()) & (np.maximum(y0, y1) >= local.y())))


def name_at_canvas_point(canvas, position: QPoint, items) -> str | None:  # type: ignore[no-untyped-def]
    """The name of the drawn item under a canvas position: the nearest line or point within
    `HIT_TOLERANCE_PIXELS`, or a bar containing it. `items` holds (graphics item, name) pairs."""
    import pyqtgraph as pg

    scene_position = canvas.mapToScene(position)
    best, found = HIT_TOLERANCE_PIXELS ** 2, None
    for item, name in items:
        if item is None or not name:
            continue
        if isinstance(item, pg.BarGraphItem):
            if _bar_contains(item, scene_position):
                return name
            continue
        points = _item_points(item)
        if points is None:
            continue
        lines = not isinstance(item, pg.ScatterPlotItem)
        if isinstance(item, pg.PlotDataItem):
            lines = item.opts.get("pen") is not None
        distance = distance_squared(points, scene_position.x(), scene_position.y(), lines=lines)
        if distance <= best:
            best, found = distance, name
    return found
