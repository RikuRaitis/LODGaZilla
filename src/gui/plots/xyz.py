"""Cartesian 3D card with independently assigned X/Y/Z sources per trace."""

import numpy as np
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QVector3D
from PySide6.QtWidgets import QApplication, QComboBox, QToolButton

from src.data.xyz import XYZ_MAXIMUM_POINTS, AxisSource, XYZData
from src.gui.plots.base import PlotBaseWidget
from src.gui.plots.card_markers import PointMarkers
from src.gui.plots.icons import plot_control_icon
from src.gui.plots.theme import canvas_background
from src.gui.plots.legend import PlotLegendWidget
from src.gui.plots.legend_groups import group_display_name
from src.gui.plots.series_hover import HIT_TOLERANCE_PIXELS, distance_squared, install_series_name_hover
from src.gui.plots.marker_info import MarkerInfoEntry
from src.gui.plots.markers import (
    FIRST_MARKER, SECOND_MARKER, sync_marker_color, marker_color, marker_width, selection_rgba,
)
from src.gui.plots.value_boxes import ValueBoxItem, ValueBoxLayer
from src.jobs.xyz import XYZLoadCoordinator


# Where the 3D plot draws its axes: through the data's 0, 0, 0, or along the box's minimum corner.
AXES_ORIGIN = "origin"
AXES_CORNER = "corner"
AXES_POSITIONS = {AXES_ORIGIN: "Axes at 0, 0, 0", AXES_CORNER: "Axes at corner"}


def axes_origin(low, high, position: str):  # type: ignore[no-untyped-def]
    """Scene position, in the -1..1 box, where the three axes meet.

    At the origin, a 0 outside an axis' data range is held at the nearest box edge,
    so the axes stay with the data.
    """
    if position != AXES_ORIGIN:
        return np.full(3, -1.0)
    low, high = np.asarray(low, dtype=float), np.asarray(high, dtype=float)
    scale = np.where(high > low, (high - low) / 2, 1.0)
    return np.clip((0.0 - (low + high) / 2) / scale, -1.0, 1.0)


def axis_tick_label(axis_name: str, low: float, high: float, fraction: float) -> str:
    """The axis name and its value ``fraction`` of the way from ``low`` to ``high``."""
    span = high - low
    value = low + fraction * span
    if abs(value) < 1e-6 * span:
        value = 0.0
    # Preserve small differences around a large offset (Unix time).
    digits = min(16, max(6, int(np.ceil(np.log10(max(abs(value), 1)) - np.log10(span))) + 3))
    return f"{axis_name} {value:.{digits}g}"


class XYZView(gl.GLViewWidget):
    scroll_requested = Signal(int)
    # A press and release without dragging, which would otherwise rotate.
    clicked = Signal(object, object)
    double_clicked = Signal(object)
    camera_changed = Signal()

    def mousePressEvent(self, event):
        self._press_position = event.position()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        start = getattr(self, "_press_position", None)
        self._press_position = None
        if start is not None and (event.position() - start).manhattanLength() < QApplication.startDragDistance():
            self.clicked.emit(event.button(), event.position())
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self._press_position = None
        self.double_clicked.emit(event.button())

    def paintGL(self, *args, **kwargs):
        super().paintGL(*args, **kwargs)
        # Only a real camera or size change: the values boxes repaint the canvas when told,
        # and an unconditional signal made every paint request the next one.
        camera = (repr(self.cameraParams()), self.width(), self.height())
        if camera != getattr(self, "_painted_camera", None):
            self._painted_camera = camera
            self.camera_changed.emit()

    def project(self, positions):
        """Canvas pixels of scene positions for the current camera; NaN behind it."""
        width, height = max(1, self.width()), max(1, self.height())
        matrix = self.projectionMatrix((0, 0, width, height), (0, 0, width, height)) * self.viewMatrix()
        transform = np.array(matrix.data(), dtype=float).reshape(4, 4).T
        points = np.c_[np.asarray(positions, dtype=float), np.ones(len(positions))] @ transform.T
        with np.errstate(divide="ignore", invalid="ignore"):
            ndc = points[:, :2] / points[:, 3:4]
        ndc[points[:, 3] <= 0] = np.nan
        return np.c_[(ndc[:, 0] + 1) / 2 * width, (1 - ndc[:, 1]) / 2 * height]

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.opts["distance"] = max(0.2, min(200, self.opts["distance"] * 0.999 ** event.angleDelta().y()))
            self.update()
        else:
            self.scroll_requested.emit(event.angleDelta().y())
        event.accept()


class XYZPlotWidget(PointMarkers, PlotBaseWidget):
    """One or more 3D traces, each aligning three scalar sources by timestamp."""
    help_topic = "xyz"

    series_changed = Signal()
    plot_status_changed = Signal(str)
    marker_placed = Signal(float)
    marker_cleared = Signal()
    second_marker_placed = Signal(float)
    second_marker_cleared = Signal()
    view_class = XYZView
    accepts_axis_drop = True
    accepts_time_axis_drop = True
    default_sample_limit = XYZ_MAXIMUM_POINTS
    sample_limit_noun = "trace"
    # Auto draws each trace as a line with its points.
    supports_draw_style = True

    def __init__(self, x_entry, y_entry=None, z_entry=None, parent=None):
        super().__init__(*x_entry, parent)
        self.setMinimumHeight(220)
        self.configure_legend_groups("XYZ", title="Trace", allow_time=True)
        self.traces = self.grouped_series.groups
        self.results = []
        self.time_range = None
        self._disposed = False
        self._items = []
        self._loading = False
        self._extent = None
        self._fixed_extent = None
        # Scene positions of each trace as drawn, for picking and marker points.
        self._scene_positions: list[np.ndarray | None] = []
        self._marker_items = []
        # Each trace's selection stretch, sync point and two marker points, reused.
        self._trace_marker_items: dict[int, dict] = {}
        self._init_point_markers()
        self._value_box_times: dict[int, float | None] = {FIRST_MARKER: None, SECOND_MARKER: None}
        self.loader = XYZLoadCoordinator(self)
        self.loader.loaded.connect(self._loaded)

        self.reset_button = QToolButton(self)
        self.reset_button.setToolTip("Reset camera")
        self.reset_button.setAccessibleName("Reset camera")
        self.reset_button.setAutoRaise(True)
        self.reset_button.setFixedSize(self.close_button.size())
        self.reset_button.setIconSize(QSize(20, 20))
        self.reset_button.setIcon(plot_control_icon(self, "reset-camera"))
        self.reset_button.clicked.connect(self.reset_camera)
        self.header_controls.append(self.reset_button)
        self.axes_position = AXES_ORIGIN
        self.axes_combo = QComboBox(self)
        self.axes_combo.setAccessibleName("Axes position")
        self.axes_combo.setToolTip("Axes position")
        for position, title in AXES_POSITIONS.items():
            self.axes_combo.addItem(title, position)
        self.axes_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.axes_combo.setFixedHeight(self.close_button.height() - 2)
        self.axes_combo.adjustSize()
        self.axes_combo.currentIndexChanged.connect(
            lambda index: self.set_axes_position(self.axes_combo.itemData(index)))
        self.header_controls.append(self.axes_combo)
        self.view = self.view_class(self)
        self.view.setMinimumHeight(100)
        self.view.scroll_requested.connect(self.wheel_scrolled)
        self.view.clicked.connect(self._on_view_clicked)
        self.view.double_clicked.connect(self._on_view_double_clicked)
        self.value_boxes = ValueBoxLayer(self.view)
        self.view.camera_changed.connect(self.value_boxes.reposition)
        self.content_layout.addWidget(self.view, 1)
        self.legend = PlotLegendWidget(self.view)
        self.legend.set_group_model(self.grouped_series)
        # A trace's sources are named while the pointer rests on it.
        install_series_name_hover(self.view, self.series_name_at)
        self.reset_camera()
        self.add_trace((x_entry, y_entry, z_entry))

    def series_name_at(self, position):
        """The trace drawn under a view position, named by its sources, for the hover name."""
        best, found = HIT_TOLERANCE_PIXELS ** 2, None
        for trace, positions in zip(self.traces, self._scene_positions):
            if positions is None or not len(positions):
                continue
            pixels = self.view.project(positions)
            distance = distance_squared((pixels[:, 0], pixels[:, 1]), position.x(), position.y(),
                                        lines=self.draw_style != "points")
            if distance <= best:
                best, found = distance, group_display_name(trace)
        return found

    def add_trace(self, entries):
        if len(entries) != 3:
            raise ValueError("Select exactly three data series for a 3D trace")
        sources = tuple(AxisSource(*entry) if entry is not None else None for entry in entries)
        self.grouped_series.insert(sources)

    def remove_trace(self, index):
        self.grouped_series.remove(index)

    def on_series_changed(self):
        self._fixed_extent = None
        self._request()

    def set_axis_component(self, key, component):
        if component not in {"value", "time"} or not 0 <= key < len(self.traces) * 3:
            return
        axis = self.grouped_series.axis(key)
        if axis is not None:
            self.replace_legend_series(key, (axis.path, axis.name), component)

    def on_sample_limit_changed(self):
        # Keep the axis scaling so the camera shows the same region at the new limit.
        if self._fixed_extent is None and any(isinstance(result, XYZData) for result in self.results):
            self._fixed_extent = self._extent
        self._request(keep_traces=True)

    def _request(self, *, keep_traces=False):
        """Load the traces again; ``keep_traces`` shows the current ones until the new arrive
        (a new Sample limit or trim), as X/Y does. A legend change clears them at once."""
        self.series = self.grouped_series.entries()
        self._loading = True
        if not keep_traces:
            self.results = []
            self._render()
        self.current_status = "Loading 3D data…"
        self.loader.request([trace.axes for trace in self.traces], self.time_range, self.sample_limit)
        self.series_changed.emit()

    def _loaded(self, results):
        if self._disposed:
            return
        self._loading = False
        self.results = results
        self.update_sample_coverage(results)
        messages = []
        for index, result in enumerate(results):
            if isinstance(result, XYZData):
                count = int(np.count_nonzero(np.all(np.isfinite(result.positions), axis=1)))
                messages.append(f"Trace {index + 1}: {count:,} points" + (" (sampled)" if result.sampled else ""))
            else:
                messages.append(f"Trace {index + 1}: " + ("assign the empty axes" if result is None else str(result)))
        self.current_status = "; ".join(messages)
        self.plot_status_changed.emit(self.current_status)
        self._render()

    def _render(self):
        for item in self._items:
            self.view.removeItem(item)
        self._items.clear()
        self.view.setBackgroundColor(canvas_background(self))
        finite = [result.positions[np.all(np.isfinite(result.positions), axis=1)]
                  for result in self.results if isinstance(result, XYZData)]
        if self._fixed_extent is not None:
            low, high = self._fixed_extent
            if not self._loading:
                self._fixed_extent = None
        else:
            if finite:
                low = np.min([points.min(axis=0) for points in finite], axis=0)
                high = np.max([points.max(axis=0) for points in finite], axis=0)
            else:
                low, high = np.zeros(3), np.ones(3)
            # Constant axes still need a meaningful numeric extent around the data.
            padding = np.where(high > low, 0, np.maximum(np.abs(low) * 0.01, 1))
            low, high = low - padding, high + padding
        self._extent = (low, high)
        center = low / 2 + high / 2
        scale = high / 2 - low / 2
        self._scene_positions = []
        for index, result in enumerate(self.results):
            if not isinstance(result, XYZData):
                self._scene_positions.append(None)
                continue
            positions = (result.positions - center) / scale
            self._scene_positions.append(positions)
            valid = np.all(np.isfinite(positions), axis=1)
            pairs = np.stack((positions[:-1], positions[1:]), axis=1)
            pairs = pairs[valid[:-1] & valid[1:]].reshape(-1, 3)
            color = pg.glColor(self.traces[index].color)
            if self.draw_style != "points":
                self._add_item(gl.GLLinePlotItem(pos=pairs.astype(np.float32), color=color, width=2, mode="lines",
                                                 antialias=True, glOptions="opaque"))
            if self.draw_style != "lines":
                # Points alone are drawn larger, so the trace still reads.
                self._add_item(gl.GLScatterPlotItem(pos=positions[valid].astype(np.float32), color=color,
                                                    size=4 if self.draw_style == "points" else 3, pxMode=True,
                                                    glOptions="opaque"))
        self._draw_axes(low, high)
        self._update_legend()
        # New scene positions: the marker items are made again for them.
        self._clear_marker_items()
        self._draw_markers()
        for index in (FIRST_MARKER, SECOND_MARKER):
            if self._value_box_times[index] is not None:
                self.show_marker_data(self._value_box_times[index], index)

    def _draw_axes(self, low, high):  # type: ignore[no-untyped-def]
        """Each axis spans the data's range through the point the Axes position selector chooses."""
        text_color = self.palette().text().color()
        font = QFont(self.font())
        font.setPointSize(9)
        origin = axes_origin(low, high, self.axes_position)
        corner = self.axes_position != AXES_ORIGIN
        minimums = []
        for axis_index, axis_name in enumerate("XYZ"):
            start, end = origin.copy(), origin.copy()
            start[axis_index], end[axis_index] = -1.0, 1.0
            self._add_item(gl.GLLinePlotItem(pos=np.array([start, end]), color=pg.glColor(text_color), width=1, glOptions="opaque"))
            for fraction in (0, 0.5, 1):
                label = axis_tick_label(axis_name, low[axis_index], high[axis_index], fraction)
                if fraction == 0 and corner:
                    minimums.append(label)
                    continue
                pos = start.copy()
                pos[axis_index] = -1 + 2 * fraction
                pos[(axis_index + 1) % 3] -= 0.16
                self._add_item(gl.GLTextItem(pos=pos, text=label, color=text_color, font=font))
        if corner:
            # The three axes start at one corner: one label lists their minimums, where three would overlap.
            # Just above the corner, clear of the X and Y labels beside and below it.
            self._add_item(gl.GLTextItem(pos=np.array([-1.0, -1.0, -0.84]), text="  ".join(minimums),
                                         color=text_color, font=font))

    def set_axes_position(self, position: str) -> None:
        """Draw the axes through 0, 0, 0 (`AXES_ORIGIN`) or along the box's corner (`AXES_CORNER`)."""
        if position not in AXES_POSITIONS:
            return
        self.axes_combo.blockSignals(True)
        self.axes_combo.setCurrentIndex(self.axes_combo.findData(position))
        self.axes_combo.blockSignals(False)
        if position != self.axes_position:
            self.axes_position = position
            self._render()

    def apply_draw_style(self, style: str) -> None:
        self._render()

    def _marker_point(self, trace_index, time_value):
        """Index of a trace's drawn point nearest a marker time, or None outside the trace."""
        result = self.results[trace_index] if trace_index < len(self.results) else None
        if time_value is None or not isinstance(result, XYZData) or not len(result.time):
            return None
        if not result.time[0] <= time_value <= result.time[-1]:
            return None
        # Times ascend: a binary search, not a pass over every point (video playback
        # moves the marker every frame).
        upper = min(int(np.searchsorted(result.time, time_value)), len(result.time) - 1)
        lower = max(0, upper - 1)
        point = lower if abs(time_value - result.time[lower]) <= abs(result.time[upper] - time_value) else upper
        return point if np.all(np.isfinite(result.positions[point])) else None

    def _selected_slice(self, trace_index, selection):
        """The trace's points between the markers, as a slice of its ascending times."""
        times = self.results[trace_index].time
        return slice(int(np.searchsorted(times, selection[0], side="left")),
                     int(np.searchsorted(times, selection[1], side="right")))

    def _clear_marker_items(self):
        for item in self._marker_items:
            self.view.removeItem(item)
        self._marker_items.clear()
        self._trace_marker_items.clear()

    def _draw_markers(self):
        """Marker points in their colors, and each trace's stretch between the markers.

        Each trace keeps its marker items and only updates them, so moving a marker
        costs no new GL items; the stretch between markers is a slice, not a mask.
        """
        selection = self.selection_range()
        selection_color = tuple(value / 255 for value in selection_rgba(2.0))
        for trace_index, positions in enumerate(self._scene_positions):
            if positions is None:
                continue
            items = self._trace_marker_items.get(trace_index)
            if items is None:
                items = self._trace_marker_items[trace_index] = {
                    "selection": self._add_marker_item(gl.GLLinePlotItem(
                        pos=np.zeros((2, 3), dtype=np.float32), width=6, mode="line_strip", antialias=True)),
                    "sync": self._add_marker_item(gl.GLScatterPlotItem(
                        pos=np.zeros((1, 3), dtype=np.float32), pxMode=True, glOptions="translucent")),
                    FIRST_MARKER: self._add_marker_item(gl.GLScatterPlotItem(
                        pos=np.zeros((1, 3), dtype=np.float32), pxMode=True, glOptions="translucent")),
                    SECOND_MARKER: self._add_marker_item(gl.GLScatterPlotItem(
                        pos=np.zeros((1, 3), dtype=np.float32), pxMode=True, glOptions="translucent")),
                }
            stretch = None
            if selection is not None:
                stretch = positions[self._selected_slice(trace_index, selection)]
                stretch = stretch[np.all(np.isfinite(stretch), axis=1)]
            if stretch is not None and len(stretch) > 1:
                items["selection"].setData(pos=stretch.astype(np.float32), color=selection_color)
                items["selection"].setVisible(True)
            else:
                items["selection"].setVisible(False)
            for key, time_value, color, size in (
                    ("sync", self._sync_time, sync_marker_color(), 16),
                    (FIRST_MARKER, self._marker_times[FIRST_MARKER], marker_color(FIRST_MARKER), 10 + 2 * marker_width()),
                    (SECOND_MARKER, self._marker_times[SECOND_MARKER], marker_color(SECOND_MARKER),
                     10 + 2 * marker_width())):
                point = self._marker_point(trace_index, time_value)
                if point is None:
                    items[key].setVisible(False)
                    continue
                items[key].setData(pos=positions[point:point + 1].astype(np.float32), color=pg.glColor(color),
                                   size=size)
                items[key].setVisible(True)

    def _add_marker_item(self, item):
        self.view.addItem(item)
        self._marker_items.append(item)
        return item

    def _on_view_clicked(self, button, position):
        index = {Qt.MouseButton.LeftButton: FIRST_MARKER, Qt.MouseButton.RightButton: SECOND_MARKER}.get(button)
        if index is None:
            return
        best = None
        for trace_index, positions in enumerate(self._scene_positions):
            if positions is None or not len(positions):
                continue
            pixels = self.view.project(positions)
            distances = (pixels[:, 0] - position.x()) ** 2 + (pixels[:, 1] - position.y()) ** 2
            if not np.any(np.isfinite(distances)):
                continue
            point = int(np.nanargmin(distances))
            if best is None or distances[point] < best[0]:
                best = (distances[point], float(self.results[trace_index].time[point]))
        if best is None:
            return
        self._place_marker_from_view(index, best[1])

    def _on_view_double_clicked(self, button):
        index = {Qt.MouseButton.LeftButton: FIRST_MARKER, Qt.MouseButton.RightButton: SECOND_MARKER}.get(button)
        if index is not None:
            self._clear_marker_from_view(index)

    def _redraw_markers(self):
        self._draw_markers()

    def apply_marker_style(self):
        self._draw_markers()
        self.value_boxes.apply_style()

    def show_marker_data(self, time_value, index=FIRST_MARKER):
        """One values box per trace, beside its drawn point nearest the marker."""
        self._value_box_times[index] = float(time_value)
        items = []
        for trace_index, positions in enumerate(self._scene_positions):
            point = self._marker_point(trace_index, time_value)
            if positions is None or point is None:
                continue
            result = self.results[trace_index]
            values = result.positions[point]
            entry = MarkerInfoEntry(
                f"Trace {trace_index + 1}", self.traces[trace_index].color,
                ", ".join(f"{value:.6g}" for value in values),
                tuple(f"{label or axis}: {value:.9g}" for axis, label, value in zip("XYZ", result.labels, values))
                + (f"Time: {float(result.time[point]):.6f} s", "Nearest drawn point"),
                full_name=group_display_name(self.traces[trace_index]),
            )
            items.append(ValueBoxItem(
                _trace_key(self.traces[trace_index]), entry, None,
                anchor=lambda positions=positions, point=point: _pixel(self.view, positions[point]),
            ))
        if items:
            self.value_boxes.set_items(index, time_value, items)
        else:
            self.value_boxes.clear(index)

    def hide_marker_data(self, index=None):
        for marker in (FIRST_MARKER, SECOND_MARKER) if index is None else (index,):
            self._value_box_times[marker] = None
            self.value_boxes.clear(marker)

    def _update_selection(self):
        """Selection Δt and each trace's drawn points and ranges for the expanded boxes."""
        selection = self.selection_range()
        if selection is None:
            self.value_boxes.set_selection_duration(None)
            return
        self.value_boxes.set_selection_duration(selection[1] - selection[0])
        statistics = {}
        for trace_index, (trace, result) in enumerate(zip(self.traces, self.results)):
            if not isinstance(result, XYZData):
                continue
            positions = result.positions[self._selected_slice(trace_index, selection)]
            lines = [f"Points: {len(positions):,} (drawn)"]
            if len(positions):
                for axis, label, column in zip("XYZ", result.labels, positions.T):
                    lines.append(f"{label or axis}: min {np.nanmin(column):.6g}, max {np.nanmax(column):.6g}, "
                                 f"average {np.nanmean(column):.6g}")
            statistics[_trace_key(trace)] = tuple(lines)
        self.value_boxes.set_statistics(statistics)

    def _add_item(self, item):
        self.view.addItem(item)
        self._items.append(item)

    def _update_legend(self):
        labels = {}
        for index, trace in enumerate(self.traces):
            if index < len(self.results) and isinstance(self.results[index], XYZData):
                labels.update({index * 3 + axis: label for axis, label in enumerate(self.results[index].labels)})
        self.legend.refresh_groups(labels)

    def set_data_time_range(self, time_range):
        self.time_range = tuple(sorted(time_range)) if time_range is not None else None
        self._fixed_extent = None
        self._request(keep_traces=True)

    def workspace_state(self):
        opts = self.view.opts
        center = opts["center"]
        return {**super().workspace_state(), "axes_position": self.axes_position, "camera": {
            "center": [center.x(), center.y(), center.z()], "distance": opts["distance"],
            "elevation": opts["elevation"], "azimuth": opts["azimuth"], "fov": opts["fov"]}}

    def restore_workspace_state(self, state):
        super().restore_workspace_state(state)
        self.set_axes_position(str(state.get("axes_position", AXES_ORIGIN)))
        camera = state.get("camera")
        try:
            self.view.opts["fov"] = float(camera["fov"])
            self.view.setCameraPosition(pos=QVector3D(*(float(value) for value in camera["center"])),
                                        distance=float(camera["distance"]), elevation=float(camera["elevation"]),
                                        azimuth=float(camera["azimuth"]))
        except (KeyError, TypeError, ValueError):
            pass

    def reset_camera(self):
        self.view.setCameraPosition(pos=QVector3D(0, 0, 0), distance=6, elevation=25, azimuth=45)

    def apply_theme(self):
        self.reset_button.setIcon(plot_control_icon(self, "reset-camera"))
        self.value_boxes.apply_style()
        self._render()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "legend"):
            self.legend.reposition()

    def dispose(self):
        self._disposed = True
        self.loader.shutdown()


def _trace_key(trace):
    """Identify a 3D trace by its axis sources, for its values boxes."""
    return tuple((axis.path, axis.name, axis.component) if axis is not None else None for axis in trace.axes)


def _pixel(view, position):
    pixel = view.project(np.asarray([position]))[0]
    return None if not np.all(np.isfinite(pixel)) else (float(pixel[0]), float(pixel[1]))
