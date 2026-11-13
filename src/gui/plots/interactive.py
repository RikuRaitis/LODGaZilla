"""PyQtGraph interactions tailored for LODGaZilla line plots."""

from __future__ import annotations

from datetime import datetime
import math
from typing import Callable

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import QApplication, QGraphicsView, QMenu

from src.gui.plots.markers import (
    FIRST_MARKER, SECOND_MARKER, SELECTION_BRUSH, sync_marker_color, marker_color, marker_width,
)


def use_full_viewport_updates(canvas: pg.PlotWidget) -> None:
    """Repaint the whole canvas on every scene change.

    PyQtGraph's default minimal updates repaint only the areas Qt believes
    changed. Markers, the selection and values boxes change their bounds
    lazily, so moving them could leave old markers, selections and boxes on
    screen until the card was resized.
    """
    canvas.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)


class TimeAxisItem(pg.AxisItem):
    """Formats unchanged native X coordinates as wall-clock or elapsed time."""

    REAL_DATETIME = "real_datetime"
    LOG_ELAPSED = "log_elapsed"
    representation_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__(orientation="bottom")
        self.representation = self.REAL_DATETIME
        self.raw_origin = 0.0
        self.seconds_per_unit = 1.0
        self.log_start_unix_seconds: float | None = None
        self.is_unix_time = False
        # The card's time offset in seconds: labels show shared time = card time + offset.
        self.display_offset = 0.0

    def set_display_offset(self, seconds: float) -> None:
        if float(seconds) != self.display_offset:
            self.display_offset = float(seconds)
            self.picture = None
            self.update()

    def _offset_units(self) -> float:
        return self.display_offset / self.seconds_per_unit if self.seconds_per_unit else 0.0

    def tickValues(self, minVal, maxVal, size):  # type: ignore[no-untyped-def,override]  # noqa: N802,N803
        """Place ticks at round shared times, which are offset from the card's own time."""
        shift = self._offset_units()
        if not shift:
            return super().tickValues(minVal, maxVal, size)
        return [(spacing, [value - shift for value in values])
                for spacing, values in super().tickValues(minVal + shift, maxVal + shift, size)]

    def configure(
        self,
        *,
        raw_origin: float,
        seconds_per_unit: float,
        log_start_unix_seconds: float | None,
        is_unix_time: bool,
    ) -> None:
        self.raw_origin = float(raw_origin)
        self.seconds_per_unit = float(seconds_per_unit)
        self.log_start_unix_seconds = log_start_unix_seconds
        self.is_unix_time = bool(is_unix_time)
        self.picture = None
        self.update()

    def show_representation_menu(self) -> None:
        menu = QMenu()
        real_action = QAction("Real date/time", menu, checkable=True)
        elapsed_action = QAction("Time from log start", menu, checkable=True)
        real_action.setChecked(self.representation == self.REAL_DATETIME)
        elapsed_action.setChecked(self.representation == self.LOG_ELAPSED)
        real_action.triggered.connect(lambda: self.set_representation(self.REAL_DATETIME))
        elapsed_action.triggered.connect(lambda: self.set_representation(self.LOG_ELAPSED))
        menu.addAction(real_action)
        menu.addAction(elapsed_action)
        menu.exec(QCursor.pos())

    def set_representation(self, representation: str) -> None:
        if representation not in {self.REAL_DATETIME, self.LOG_ELAPSED}:
            return
        if representation == self.representation:
            return
        self.representation = representation
        self.picture = None
        self.update()
        self.representation_changed.emit(representation)

    def tickStrings(self, values, scale, spacing):  # type: ignore[no-untyped-def,override]
        shift = self._offset_units()
        if shift:
            values = [float(value) + shift for value in values]
        if self.representation == self.LOG_ELAPSED:
            spacing_seconds = abs(float(spacing) * self.seconds_per_unit)
            return [
                self._elapsed_text(
                    (float(value) - self.raw_origin) * self.seconds_per_unit,
                    spacing_seconds,
                )
                for value in values
            ]
        return self._datetime_strings(values, float(spacing))

    def _datetime_strings(self, values, spacing: float) -> list[str]:  # type: ignore[no-untyped-def]
        """Use a date only where it adds information, never a second line."""
        spacing_seconds = abs(spacing * self.seconds_per_unit)
        previous_date = None
        labels: list[str] = []
        for value in values:
            current = self._datetime_for_value(float(value))
            if current is None:
                labels.append(self._elapsed_text(
                    (float(value) - self.raw_origin) * self.seconds_per_unit, spacing_seconds
                ))
                continue
            date_changed = previous_date is None or current.date() != previous_date
            previous_date = current.date()
            if spacing_seconds >= 86_400:
                labels.append(current.strftime("%Y-%m-%d"))
                continue
            if spacing_seconds >= 1:
                time_text = current.strftime("%H:%M:%S" if spacing_seconds < 60 else "%H:%M")
            else:
                time_text = current.strftime("%H:%M:%S.") + f"{current.microsecond // 1000:03d}"
            labels.append(
                f"{current.strftime('%d %b')} {time_text}" if date_changed else time_text
            )
        return labels

    def _datetime_for_value(self, value: float) -> datetime | None:
        if self.is_unix_time:
            timestamp = value * self.seconds_per_unit
        elif self.log_start_unix_seconds is not None:
            timestamp = self.log_start_unix_seconds + (value - self.raw_origin) * self.seconds_per_unit
        else:
            # Caches must carry explicit log-start metadata for calendar time.
            return None
        try:
            return datetime.fromtimestamp(timestamp).astimezone()
        except (OSError, OverflowError, ValueError):
            return None

    @staticmethod
    def _elapsed_text(seconds: float, spacing_seconds: float = 1.0) -> str:
        """Format elapsed ticks with only the precision useful at this zoom."""
        sign = "-" if seconds < 0 else ""
        if spacing_seconds >= 1.0:
            decimal_places = 0
        elif spacing_seconds >= 0.1:
            decimal_places = 1
        elif spacing_seconds >= 0.01:
            decimal_places = 2
        elif spacing_seconds >= 0.001:
            decimal_places = 3
        else:
            decimal_places = min(9, max(4, math.ceil(-math.log10(max(spacing_seconds, 1e-9)))))

        scale = 10**decimal_places
        scaled_seconds = round(abs(seconds) * scale)
        whole_seconds, fraction = divmod(scaled_seconds, scale)
        hours, remainder = divmod(whole_seconds, 3600)
        days, hours = divmod(hours, 24)
        minutes, whole_seconds = divmod(remainder, 60)
        parts: list[str] = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")

        if spacing_seconds >= 3600:
            return sign + " ".join(parts or ["0h"])

        if minutes:
            parts.append(f"{minutes}min")
        if spacing_seconds >= 60:
            return sign + " ".join(parts or ["0min"])

        second_text = str(whole_seconds)
        if decimal_places:
            second_text += f".{fraction:0{decimal_places}d}"
        parts.append(f"{second_text}sec")
        return sign + " ".join(parts)


class HorizontalPanViewBox(pg.ViewBox):
    """A plot-area drag pans only horizontally."""

    def __init__(self) -> None:
        super().__init__(enableMenu=False)
        self._last_drag_position = None

    def mouseDragEvent(self, event, axis=None) -> None:  # type: ignore[no-untyped-def]
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDragEvent(event, axis=axis)
            return
        event.accept()
        position = event.pos()
        if event.isStart():
            self._last_drag_position = position
            return
        if self._last_drag_position is None:
            self._last_drag_position = position
            return
        previous = self.mapSceneToView(self._last_drag_position)
        current = self.mapSceneToView(position)
        self.translateBy(x=previous.x() - current.x(), y=0)
        self._last_drag_position = position


MARKER_GRAB_PIXELS = 8


class InteractivePlotWidget(pg.PlotWidget):
    """Wheel, pan and two-marker behavior used by LODGaZilla time plots.

    Left click places the first marker and right click the second; a double
    click with the same button removes it. Either marker can be dragged. The
    area between two placed markers is the selection.
    """

    scroll_requested = Signal(int)
    marker_placed = Signal(float)
    marker_cleared = Signal()
    second_marker_placed = Signal(float)
    second_marker_cleared = Signal()
    time_axis_representation_changed = Signal(str)
    selection_changed = Signal(float, float)
    selection_cleared = Signal()

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        self.interactive_view_box = HorizontalPanViewBox()
        self.time_axis = TimeAxisItem()
        super().__init__(
            parent=parent,
            viewBox=self.interactive_view_box,
            axisItems={"bottom": self.time_axis},
        )
        self._axis_drag_mode = ""
        self._axis_drag_position = None
        self._axis_drag_start = None
        self.markers: list[pg.InfiniteLine | None] = [None, None]
        self.marker_snap_resolver: Callable[[float], float] | None = None
        self.snap_marker_to_nearest_point = False
        self.selection_region: pg.LinearRegionItem | None = None
        self.sync_marker_line: pg.InfiniteLine | None = None
        self._dragged_marker: int | None = None
        use_full_viewport_updates(self)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.scene().sigMouseMoved.connect(self._update_marker_cursor)
        self.time_axis.representation_changed.connect(self.time_axis_representation_changed)

    @property
    def vertical_marker(self) -> pg.InfiniteLine | None:
        return self.markers[FIRST_MARKER]

    @property
    def second_marker(self) -> pg.InfiniteLine | None:
        return self.markers[SECOND_MARKER]

    def marker_value(self, index: int = FIRST_MARKER) -> float | None:
        marker = self.markers[index]
        return None if marker is None else float(marker.value())

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.scroll_requested.emit(event.angleDelta().y())
            event.accept()
            return

        scene_position = self.mapToScene(event.position().toPoint())
        center = self.interactive_view_box.mapSceneToView(scene_position)
        factor = 0.85 if event.angleDelta().y() > 0 else 1.0 / 0.85
        plot_area = self.interactive_view_box.sceneBoundingRect()
        left_axis = self.getPlotItem().getAxis("left").sceneBoundingRect()
        # Axis geometry can overlap neighbouring scene bounds. The actual
        # data ViewBox takes precedence so Ctrl+wheel over a series is always
        # an X-only zoom.
        if not plot_area.contains(scene_position) and left_axis.contains(scene_position):
            if getattr(self, 'y_axis_zoom_enabled', True):
                self.interactive_view_box.scaleBy((1.0, factor), center=center)
        else:
            self.interactive_view_box.scaleBy((factor, 1.0), center=center)
        event.accept()

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        scene_position = self.mapToScene(event.position().toPoint())
        in_plot_area = self.interactive_view_box.sceneBoundingRect().contains(scene_position)
        if event.button() == Qt.MouseButton.RightButton and in_plot_area:
            # Placed on release, so a right drag does not move the marker.
            self._begin_axis_drag("second_marker", event.position())
            event.accept()
            return
        if (
            event.button() == Qt.MouseButton.RightButton
            and self.getPlotItem().getAxis("bottom").sceneBoundingRect().contains(scene_position)
        ):
            self.time_axis.show_representation_menu()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            plot_item = self.getPlotItem()
            if in_plot_area:
                self._dragged_marker = self.marker_near(scene_position)
                # Handle the data area here instead of letting ViewBox use its
                # default two-dimensional pan behavior.
                self._begin_axis_drag("marker" if self._dragged_marker is not None else "plot_x", event.position())
                event.accept()
                return
            if (plot_item.getAxis("left").sceneBoundingRect().contains(scene_position)
                    and getattr(self, "y_axis_zoom_enabled", True)):
                # Cards with a fixed vertical layout (Event/state lanes) keep it.
                self._begin_axis_drag("y", event.position())
                event.accept()
                return
            if plot_item.getAxis("bottom").sceneBoundingRect().contains(scene_position):
                self._begin_axis_drag("x", event.position())
                event.accept()
                return
        super().mousePressEvent(event)

    def configure_time_axis(
        self,
        *,
        raw_origin: float,
        seconds_per_unit: float,
        log_start_unix_seconds: float | None,
        is_unix_time: bool,
    ) -> None:
        self.time_axis.configure(
            raw_origin=raw_origin,
            seconds_per_unit=seconds_per_unit,
            log_start_unix_seconds=log_start_unix_seconds,
            is_unix_time=is_unix_time,
        )

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._axis_drag_mode and self._axis_drag_position is not None:
            previous = self.interactive_view_box.mapSceneToView(self.mapToScene(self._axis_drag_position.toPoint()))
            current = self.interactive_view_box.mapSceneToView(self.mapToScene(event.position().toPoint()))
            if self._axis_drag_mode == "second_marker":
                event.accept()
                return
            if self._axis_drag_mode == "marker" and self._dragged_marker is not None:
                if (event.position() - self._axis_drag_start).manhattanLength() < QApplication.startDragDistance():
                    event.accept()
                    return
                # Move the line and the selection; listeners hear the final position on release.
                self.markers[self._dragged_marker].setValue(current.x())
                self._update_selection_region(notify=False)
            elif self._axis_drag_mode in {"x", "plot_x"}:
                self.interactive_view_box.translateBy(x=previous.x() - current.x(), y=0)
            else:
                self.interactive_view_box.translateBy(x=0, y=previous.y() - current.y())
            self._axis_drag_position = event.position()
            event.accept()
            return
        scene_position = self.mapToScene(event.position().toPoint())
        self._update_marker_cursor(scene_position)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._axis_drag_mode:
            mode = self._axis_drag_mode
            clicked = (
                self._axis_drag_start is not None
                and (event.position() - self._axis_drag_start).manhattanLength() < QApplication.startDragDistance()
            )
            scene_position = self.mapToScene(event.position().toPoint())
            if mode == "marker" and self._dragged_marker is not None:
                index = self._dragged_marker
                self._dragged_marker = None
                if clicked:
                    # A left click next to either marker still places the first marker.
                    self._place_marker_at(scene_position, FIRST_MARKER)
                else:
                    self._place_marker_at(scene_position, index)
            elif mode == "plot_x" and clicked:
                self._place_marker_at(scene_position, FIRST_MARKER)
            elif mode == "second_marker" and clicked:
                self._place_marker_at(scene_position, SECOND_MARKER)
            self._axis_drag_mode = ""
            self._axis_drag_position = None
            self._axis_drag_start = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        index = {Qt.MouseButton.LeftButton: FIRST_MARKER, Qt.MouseButton.RightButton: SECOND_MARKER}.get(event.button())
        if index is not None:
            scene_position = self.mapToScene(event.position().toPoint())
            if self.interactive_view_box.sceneBoundingRect().contains(scene_position):
                self.clear_vertical_marker(index)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def _begin_axis_drag(self, mode: str, position) -> None:  # type: ignore[no-untyped-def]
        self._axis_drag_mode = mode
        self._axis_drag_position = position
        self._axis_drag_start = position

    def _place_marker_at(self, scene_position, index: int) -> None:  # type: ignore[no-untyped-def]
        x_value = self.interactive_view_box.mapSceneToView(scene_position).x()
        if self.snap_marker_to_nearest_point and self.marker_snap_resolver is not None:
            try:
                x_value = self.marker_snap_resolver(x_value)
            except (TypeError, ValueError):
                # A marker remains usable while a plot is being reloaded.
                pass
        self.set_vertical_marker(x_value, index)

    def set_vertical_marker(self, x_value: float, index: int = FIRST_MARKER) -> None:
        marker = self.markers[index]
        if marker is None:
            marker = pg.InfiniteLine(
                pos=x_value,
                angle=90,
                movable=False,
                pen=pg.mkPen(marker_color(index), width=marker_width()),
            )
            marker.setZValue(20)
            self.getPlotItem().addItem(marker)
            self.markers[index] = marker
        else:
            marker.setValue(x_value)
        self._update_selection_region()
        (self.marker_placed if index == FIRST_MARKER else self.second_marker_placed).emit(float(x_value))

    def clear_vertical_marker(self, index: int = FIRST_MARKER) -> None:
        marker = self.markers[index]
        if marker is not None:
            self.getPlotItem().removeItem(marker)
            self.markers[index] = None
            self._update_selection_region()
            (self.marker_cleared if index == FIRST_MARKER else self.second_marker_cleared).emit()

    def apply_marker_style(self) -> None:
        """Restyle placed markers after the marker settings change."""
        for index, marker in enumerate(self.markers):
            if marker is not None:
                marker.setPen(pg.mkPen(marker_color(index), width=marker_width()))
        if self.selection_region is not None:
            self.selection_region.setBrush(pg.mkBrush(*SELECTION_BRUSH))
            self.selection_region.update()

    def set_sync_marker(self, x_value: float | None) -> None:
        """Show a Video card's stored sync data marker as a dashed line, or remove it."""
        if x_value is None:
            if self.sync_marker_line is not None:
                self.getPlotItem().removeItem(self.sync_marker_line)
                self.sync_marker_line = None
            return
        if self.sync_marker_line is None:
            self.sync_marker_line = pg.InfiniteLine(
                angle=90, movable=False,
                pen=pg.mkPen(sync_marker_color(), width=2, style=Qt.PenStyle.DashLine),
            )
            self.sync_marker_line.setZValue(19)
            self.getPlotItem().addItem(self.sync_marker_line)
        self.sync_marker_line.setPen(pg.mkPen(sync_marker_color(), width=2, style=Qt.PenStyle.DashLine))
        self.sync_marker_line.setValue(x_value)

    def set_second_marker(self, x_value: float) -> None:
        self.set_vertical_marker(x_value, SECOND_MARKER)

    def clear_second_marker(self) -> None:
        self.clear_vertical_marker(SECOND_MARKER)

    def selection_range(self) -> tuple[float, float] | None:
        if self.selection_region is None:
            return None
        left, right = sorted(self.selection_region.getRegion())
        return float(left), float(right)

    def _update_selection_region(self, *, notify: bool = True) -> None:
        """Paint the area between two placed markers as the selection."""
        values = [marker.value() for marker in self.markers if marker is not None]
        if len(values) < 2:
            if self.selection_region is not None:
                self.getPlotItem().removeItem(self.selection_region)
                self.selection_region = None
                self.selection_cleared.emit()
            return
        region = tuple(sorted(float(value) for value in values))
        if self.selection_region is None:
            # The marker lines draw the boundaries; the region is only the fill.
            self.selection_region = pg.LinearRegionItem(
                values=region,
                orientation="vertical",
                movable=False,
                brush=pg.mkBrush(*SELECTION_BRUSH),
                pen=pg.mkPen(None),
            )
            self.selection_region.setZValue(5)
            self.getPlotItem().addItem(self.selection_region)
        else:
            self.selection_region.blockSignals(True)
            try:
                self.selection_region.setRegion(region)
            finally:
                self.selection_region.blockSignals(False)
        if notify:
            self.selection_changed.emit(*region)

    def marker_near(self, scene_position) -> int | None:  # type: ignore[no-untyped-def]
        """Return the marker within grabbing distance of a scene position."""
        best = None
        for index, marker in enumerate(self.markers):
            if marker is None:
                continue
            marker_position = self.interactive_view_box.mapViewToScene(pg.Point(marker.value(), 0))
            distance = abs(marker_position.x() - scene_position.x())
            if distance <= MARKER_GRAB_PIXELS and (best is None or distance < best[0]):
                best = (distance, index)
        return None if best is None else best[1]

    def _update_marker_cursor(self, scene_position) -> None:  # type: ignore[no-untyped-def]
        cursor = (
            Qt.CursorShape.SizeHorCursor
            if self.interactive_view_box.sceneBoundingRect().contains(scene_position)
            and self.marker_near(scene_position) is not None
            else Qt.CursorShape.ArrowCursor
        )
        self.setCursor(cursor)
        self.viewport().setCursor(cursor)
