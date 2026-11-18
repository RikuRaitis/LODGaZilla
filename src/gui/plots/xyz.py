"""Cartesian 3D card with independently assigned X/Y/Z sources per trace."""

import numpy as np
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QVector3D
from PySide6.QtWidgets import QApplication, QButtonGroup, QCheckBox, QHBoxLayout, QToolButton, QWidget

from OpenGL import GL
from src.gui.plots.wide_lines import WideLinePlotItem
from src.data.axis_scale import clamp_stretch, equal_scale_factors
from src.data.xyz import XYZ_MAXIMUM_POINTS, AxisSource, XYZData
from src.gui.plots.base import PlotBaseWidget
from src.gui.plots.card_markers import PointMarkers
from src.gui.plots.icons import plot_control_icon
from src.gui.plots.theme import canvas_background
from src.gui.plots.legend import PlotLegendWidget
from src.gui.plots.legend_groups import group_display_name
from src.gui.plots.series_hover import HIT_TOLERANCE_PIXELS, distance_squared, install_series_name_hover
from src.gui.plots.series_style import SELECT_TOLERANCE_PIXELS, series_line_width
from src.gui.plots.marker_info import MarkerInfoEntry
from src.gui.plots.markers import (
    FIRST_MARKER, SECOND_MARKER, sync_marker_color, marker_color, marker_width, track_selection_layer, track_selection_rgba,
)
from src.gui.plots.value_boxes import ValueBoxItem, ValueBoxLayer
from src.jobs.xyz import XYZLoadCoordinator


# Where the 3D plot draws its axes: through the data's 0, 0, 0, or along the box's minimum corner.
AXES_ORIGIN = "origin"
AXES_CORNER = "corner"
AXES_POSITIONS = {AXES_ORIGIN: "Axes at 0, 0, 0", AXES_CORNER: "Axes at corner"}
# The selection "Always on top": no depth test, blended by its opacity.
ON_TOP_GL_OPTIONS = {GL.GL_DEPTH_TEST: False, GL.GL_BLEND: True, GL.GL_CULL_FACE: False,
                     "glBlendFunc": (GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)}
# With Use shader, Auto draws a trace's points this much wider than its line.
AUTO_POINT_MARGIN_PIXELS = 3
AUTO_POINT_LIGHTER_PERCENT = 160
# A click within this many pixels of an axis line selects it; Ctrl+wheel then stretches it this much per step.
AXIS_HIT_PIXELS = 8.0
AXIS_STRETCH_PER_WHEEL_STEP = 1.15


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
    # Ctrl+wheel while an axis is selected: the axis (0-2) and the wheel's angle delta.
    axis_stretch_requested = Signal(int, int)
    # Set by the card: the selected axis, which Ctrl+wheel stretches; None zooms the camera.
    selected_axis = None

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
            if self.selected_axis is not None:
                self.axis_stretch_requested.emit(self.selected_axis, event.angleDelta().y())
                event.accept()
                return
            self.opts["distance"] = max(0.2, min(200, self.opts["distance"] * 0.999 ** event.angleDelta().y()))
            self.update()
        else:
            self.scroll_requested.emit(event.angleDelta().y())
        event.accept()


class XYZPlotWidget(PointMarkers, PlotBaseWidget):
    """One or more 3D traces, each aligning three scalar sources by timestamp."""
    has_center_view = True
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
        # The selection drawn again above everything (Always on top), per trace.
        self._selection_on_top: dict[int, dict] = {}
        self._init_point_markers()
        self._value_box_times: dict[int, float | None] = {FIRST_MARKER: None, SECOND_MARKER: None}
        self.loader = XYZLoadCoordinator(self)
        self.loader.loaded.connect(self._loaded)

        # The Axes selector: two buttons, axes through 0, 0, 0 or along the corner; the chosen
        # one is highlighted, like the Draw style selector.
        from src.gui.plots.theme import palette_overlay_stylesheet
        self.axes_position = AXES_ORIGIN
        self.axes_selector = QWidget(self)
        self.axes_selector.setAccessibleName("Axes position")
        axes_layout = QHBoxLayout(self.axes_selector)
        axes_layout.setContentsMargins(2, 0, 2, 0)
        axes_layout.setSpacing(0)
        axes_group = QButtonGroup(self.axes_selector)
        axes_group.setExclusive(True)
        self.axes_buttons: dict[str, QToolButton] = {}
        for position, title in AXES_POSITIONS.items():
            button = QToolButton(self.axes_selector)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setToolTip(title)
            button.setAccessibleName(title)
            button.setFixedSize(self.close_button.height() - 2, self.close_button.height() - 2)
            button.setIconSize(QSize(18, 18))
            button.setIcon(plot_control_icon(self, f"axes-{position}"))
            button.clicked.connect(lambda _checked=False, position=position: self.set_axes_position(position))
            axes_group.addButton(button)
            axes_layout.addWidget(button)
            self.axes_buttons[position] = button
        self.axes_buttons[self.axes_position].setChecked(True)
        self.axes_selector.setStyleSheet(
            palette_overlay_stylesheet()
            + " QToolButton { border: none; border-radius: 3px; background: transparent; }"
            " QToolButton:hover { background: palette(midlight); }"
            " QToolButton:checked { background: rgba(33, 150, 243, 0.40); }")
        self.axes_selector.adjustSize()
        self.header_controls.append(self.axes_selector)
        # Use shader: trace lines at the Series line widths, drawn by shaders (off by default;
        # without it OpenGL draws them 1 px wide).
        self.use_shader = False
        self.use_shader_panel = QWidget(self)
        shader_layout = QHBoxLayout(self.use_shader_panel)
        shader_layout.setContentsMargins(5, 0, 5, 0)
        self.use_shader_check = QCheckBox("Use shader", self.use_shader_panel)
        self.use_shader_check.setToolTip("Use shader")
        self.use_shader_check.toggled.connect(self.set_use_shader)
        shader_layout.addWidget(self.use_shader_check)
        self.use_shader_panel.setStyleSheet(palette_overlay_stylesheet())
        self.use_shader_panel.setFixedHeight(self.close_button.height() - 2)
        self.use_shader_panel.adjustSize()
        self.header_controls.append(self.use_shader_panel)
        # Each axis's stretch; Ctrl+wheel changes the selected axis's (a click on its line selects
        # it). Equal scale and Fit set all three at once.
        self.axis_stretch = np.ones(3)
        self.selected_axis: int | None = None
        self._axis_segments: list[np.ndarray] = []
        self.equal_scale_button = QToolButton(self)
        self.equal_scale_button.setAccessibleName("Equal scale")
        self.equal_scale_button.clicked.connect(self.equal_scale)
        self.fit_axes_button = QToolButton(self)
        self.fit_axes_button.setAccessibleName("Fit axes")
        self.fit_axes_button.setToolTip("Fit: stretch every axis to fill the box")
        self.fit_axes_button.clicked.connect(self.fit_axes)
        for button, icon in ((self.equal_scale_button, "axes-equal"), (self.fit_axes_button, "axes-fit")):
            button.setAutoRaise(True)
            button.setFixedSize(self.close_button.size())
            button.setIconSize(QSize(18, 18))
            button.setIcon(plot_control_icon(self, icon))
            self.header_controls.append(button)
        self._equal_scale_reason = "No data"
        self._update_equal_scale_button()
        self.view = self.view_class(self)
        self.view.setMinimumHeight(100)
        self.view.scroll_requested.connect(self.wheel_scrolled)
        self.view.axis_stretch_requested.connect(self.stretch_axis)
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

    def trace_at(self, position, tolerance=SELECT_TOLERANCE_PIXELS):  # type: ignore[no-untyped-def]
        """The index of the trace drawn within ``tolerance`` pixels of a view position, or None."""
        best, found = tolerance ** 2, None
        for index, positions in enumerate(self._scene_positions):
            if positions is None or not len(positions) or index >= len(self.traces):
                continue
            pixels = self.view.project(positions)
            distance = distance_squared((pixels[:, 0], pixels[:, 1]), position.x(), position.y(),
                                        lines=self.draw_style != "points")
            if distance <= best:
                best, found = distance, index
        return found

    def set_use_shader(self, enabled: bool) -> None:
        """Draw the traces at the Series line widths with shaders, or as plain 1 px lines."""
        enabled = bool(enabled)
        if self.use_shader_check.isChecked() != enabled:
            self.use_shader_check.setChecked(enabled)
            return
        if enabled != self.use_shader:
            self.use_shader = enabled
            self._render()

    def apply_series_widths(self):
        """Every trace at the series width, the selected one wider (the scene is drawn again)."""
        self._render()

    def selected_legend_rows(self):  # type: ignore[no-untyped-def]
        return (), self.selected_series

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
        # The traces may have moved or gone: the selection does not follow them.
        self._selected_series = None
        self.show_selected_series_in_legend()
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
        self._trace_items: dict[int, dict] = {}
        for index, result in enumerate(self.results):
            if not isinstance(result, XYZData):
                self._scene_positions.append(None)
                continue
            positions = (result.positions - center) / scale * self.axis_stretch
            self._scene_positions.append(positions)
            valid = np.all(np.isfinite(positions), axis=1)
            pairs = np.stack((positions[:-1], positions[1:]), axis=1)
            pairs = pairs[valid[:-1] & valid[1:]].reshape(-1, 3)
            color = pg.glColor(self.traces[index].color)
            # What the selection recolors: each segment's first sample, and each point's sample.
            trace = self._trace_items[index] = {
                "segments": np.flatnonzero(valid[:-1] & valid[1:]), "samples": np.flatnonzero(valid),
                "pairs": pairs.astype(np.float32), "color": color, "line": None, "points": None,
                "width": series_line_width(selected=index == self.selected_series), "point_size": 0}
            if self.draw_style != "points":
                # Use shader draws lines of the set widths; OpenGL's core profile draws plain lines 1 px.
                line_class = WideLinePlotItem if self.use_shader else gl.GLLinePlotItem
                trace["line"] = self._add_item(line_class(pos=trace["pairs"], color=color, width=trace["width"],
                                                          mode="lines", antialias=True, glOptions="opaque"))
            if self.draw_style != "lines":
                # Points alone are drawn larger, so the trace still reads. In Auto with Use shader the
                # lines are wide, so the points are wider still, or the line would cover them.
                size, point_color = (4 if self.draw_style == "points" else 3), color
                if self.use_shader and self.draw_style != "points":
                    # A lighter shade of the trace, so the points stand out on their line.
                    size = series_line_width(selected=index == self.selected_series) + AUTO_POINT_MARGIN_PIXELS
                    point_color = pg.glColor(QColor(self.traces[index].color).lighter(AUTO_POINT_LIGHTER_PERCENT))
                trace["point_color"], trace["point_size"] = point_color, size
                trace["points"] = self._add_item(gl.GLScatterPlotItem(
                    pos=positions[valid].astype(np.float32), color=point_color, size=size, pxMode=True,
                    glOptions="opaque"))
        self._draw_axes(low, high)
        self._update_equal_scale_button()
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
        stretch = self.axis_stretch
        minimums = []
        self._axis_segments = []
        for axis_index, axis_name in enumerate("XYZ"):
            start, end = origin.copy(), origin.copy()
            start[axis_index], end[axis_index] = -1.0, 1.0
            segment = np.array([start, end]) * stretch
            self._axis_segments.append(segment)
            # The selected axis, which Ctrl+wheel stretches, is drawn thicker in the highlight colour.
            selected = axis_index == self.selected_axis
            color = self.palette().highlight().color() if selected else text_color
            self._add_item(gl.GLLinePlotItem(pos=segment, color=pg.glColor(color), width=3 if selected else 1,
                                             glOptions="opaque"))
            for fraction in (0, 0.5, 1):
                label = axis_tick_label(axis_name, low[axis_index], high[axis_index], fraction)
                if fraction == 0 and corner:
                    minimums.append(label)
                    continue
                pos = start.copy()
                pos[axis_index] = -1 + 2 * fraction
                # Labels sit beside their axis, a fixed distance away whatever its stretch.
                pos = pos * stretch
                pos[(axis_index + 1) % 3] -= 0.16
                self._add_item(gl.GLTextItem(pos=pos, text=label, color=text_color, font=font))
        if corner:
            # The three axes start at one corner: one label lists their minimums, where three would overlap.
            # Just above the corner, clear of the X and Y labels beside and below it.
            self._add_item(gl.GLTextItem(pos=np.array([-1.0, -1.0, -1.0]) * stretch + np.array([0, 0, 0.16]),
                                         text="  ".join(minimums), color=text_color, font=font))

    def axis_at(self, position) -> int | None:  # type: ignore[no-untyped-def]
        """The axis whose line is within a few pixels of a canvas position, or None (a click selects it)."""
        point = np.array([position.x(), position.y()], dtype=float)
        best, best_distance = None, AXIS_HIT_PIXELS
        for axis_index, segment in enumerate(self._axis_segments):
            start, end = self.view.project(segment)
            if not (np.all(np.isfinite(start)) and np.all(np.isfinite(end))):
                continue
            direction = end - start
            length = float(direction @ direction)
            along = 0.0 if length == 0 else float(np.clip((point - start) @ direction / length, 0, 1))
            distance = float(np.linalg.norm(point - (start + along * direction)))
            if distance <= best_distance:
                best, best_distance = axis_index, distance
        return best

    def select_axis(self, axis_index: int | None) -> None:
        """Select the axis Ctrl+wheel stretches, or none (Ctrl+wheel then zooms the camera)."""
        if axis_index == self.selected_axis:
            return
        self.selected_axis = axis_index
        self.view.selected_axis = axis_index
        self._render()

    def stretch_axis(self, axis_index: int, delta: int) -> None:
        """Stretch one axis, as Ctrl+wheel does on the selected axis (up stretches, down shrinks)."""
        stretch = self.axis_stretch.copy()
        stretch[axis_index] = clamp_stretch(stretch[axis_index] * AXIS_STRETCH_PER_WHEEL_STEP ** (delta / 120))
        self.set_axis_stretch(stretch)

    def set_axis_stretch(self, stretch) -> None:  # type: ignore[no-untyped-def]
        stretch = np.array([clamp_stretch(value) for value in stretch], dtype=float)
        if np.array_equal(stretch, self.axis_stretch):
            return
        self.axis_stretch = stretch
        self._render()

    def equal_scale(self) -> None:
        """One metre the same length on every axis, latitude and longitude measured in metres."""
        factors = self.equal_scale_factors()
        if factors is not None:
            self.set_axis_stretch(factors)

    def fit_axes(self) -> None:
        """Every axis fills the box again."""
        self.set_axis_stretch(np.ones(3))

    def equal_scale_factors(self):  # type: ignore[no-untyped-def]
        """The Equal scale stretch for the shown data, or None (with the reason kept for the button)."""
        data = [result for result in self.results if isinstance(result, XYZData)]
        if not data or self._extent is None:
            self._equal_scale_reason = "No data"
            return None
        units, roles = data[0].units, data[0].roles
        for axis_index, name in enumerate("XYZ"):
            if any(result.units[axis_index] != units[axis_index] or result.roles[axis_index] != roles[axis_index]
                   for result in data[1:]):
                self._equal_scale_reason = f"{name} has different units on different traces"
                return None
        factors, self._equal_scale_reason = equal_scale_factors(*self._extent, units, roles)
        return factors

    def _update_equal_scale_button(self) -> None:
        possible = self.equal_scale_factors() is not None
        self.equal_scale_button.setEnabled(possible)
        self.equal_scale_button.setToolTip(
            "Equal scale: one metre the same length on every axis" if possible
            else f"Equal scale is not possible: {self._equal_scale_reason}")

    def set_axes_position(self, position: str) -> None:
        """Draw the axes through 0, 0, 0 (`AXES_ORIGIN`) or along the box's corner (`AXES_CORNER`)."""
        if position not in AXES_POSITIONS:
            return
        self.axes_buttons[position].setChecked(True)
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
        self._selection_on_top.clear()

    def _draw_selection(self) -> None:
        """Each trace's stretch between the markers in the selection color, at the trace's own
        width (Settings > Plot, Selection drawn): recolored in place, so the trace's own depth
        decides ("On its own trace"); also drawn again with no depth test ("Always on top");
        or left as it is ("Off")."""
        selection = self.selection_range()
        layer, rgba = track_selection_layer(), tuple(value / 255 for value in track_selection_rgba())
        for index, trace in getattr(self, "_trace_items", {}).items():
            selected = None
            if selection is not None and layer != "off":
                window = self._selected_slice(index, selection)
                selected = (window.start, window.stop)
            segments = trace["segments"]
            inside = (np.zeros(len(segments), dtype=bool) if selected is None
                      else (segments >= selected[0]) & (segments + 1 < selected[1]))
            if trace["line"] is not None:
                if inside.any():
                    colors = np.tile(np.asarray(trace["color"], dtype=np.float32), (len(segments) * 2, 1))
                    colors[np.repeat(inside, 2)] = rgba
                    trace["line"].setData(color=colors)
                else:
                    trace["line"].setData(color=trace["color"])
            samples = trace["samples"]
            points_inside = (np.zeros(len(samples), dtype=bool) if selected is None
                             else (samples >= selected[0]) & (samples < selected[1]))
            if trace["points"] is not None:
                if points_inside.any():
                    colors = np.tile(np.asarray(trace["point_color"], dtype=np.float32), (len(samples), 1))
                    colors[points_inside] = rgba
                    trace["points"].setData(color=colors)
                else:
                    trace["points"].setData(color=trace["point_color"])
            self._draw_selection_on_top(index, trace, inside, points_inside, rgba,
                                        on_top=layer == "always_top" and selected is not None)

    def _draw_selection_on_top(self, index, trace, inside, points_inside, rgba, *, on_top) -> None:  # type: ignore[no-untyped-def]
        """For "Always on top": the stretch once more, with no depth test, above everything."""
        items = self._selection_on_top.setdefault(index, {})
        line_pairs = trace["pairs"].reshape(-1, 2, 3)[inside].reshape(-1, 3) if on_top else np.empty((0, 3))
        if trace["line"] is not None and len(line_pairs):
            if "line" not in items:
                line_class = WideLinePlotItem if self.use_shader else gl.GLLinePlotItem
                items["line"] = self._add_marker_item(line_class(
                    pos=line_pairs.astype(np.float32), mode="lines", antialias=True, glOptions=ON_TOP_GL_OPTIONS))
            items["line"].setData(pos=line_pairs.astype(np.float32), color=rgba, width=trace["width"])
            items["line"].setVisible(True)
        elif "line" in items:
            items["line"].setVisible(False)
        positions = self._scene_positions[index]
        points = (positions[trace["samples"][points_inside]] if on_top and positions is not None
                  else np.empty((0, 3)))
        if trace["points"] is not None and len(points):
            if "points" not in items:
                items["points"] = self._add_marker_item(gl.GLScatterPlotItem(
                    pos=points.astype(np.float32), pxMode=True, glOptions=ON_TOP_GL_OPTIONS))
            items["points"].setData(pos=points.astype(np.float32), color=rgba, size=trace["point_size"])
            items["points"].setVisible(True)
        elif "points" in items:
            items["points"].setVisible(False)

    def _draw_markers(self):
        """Marker points in their colors, and each trace's stretch between the markers.

        Each trace keeps its marker items and only updates them, so moving a marker
        costs no new GL items; the stretch between markers is a slice, not a mask.
        """
        self._draw_selection()
        for trace_index, positions in enumerate(self._scene_positions):
            if positions is None:
                continue
            items = self._trace_marker_items.get(trace_index)
            if items is None:
                items = self._trace_marker_items[trace_index] = {
                    "sync": self._add_marker_item(gl.GLScatterPlotItem(
                        pos=np.zeros((1, 3), dtype=np.float32), pxMode=True, glOptions="translucent")),
                    FIRST_MARKER: self._add_marker_item(gl.GLScatterPlotItem(
                        pos=np.zeros((1, 3), dtype=np.float32), pxMode=True, glOptions="translucent")),
                    SECOND_MARKER: self._add_marker_item(gl.GLScatterPlotItem(
                        pos=np.zeros((1, 3), dtype=np.float32), pxMode=True, glOptions="translucent")),
                }
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
        # A left click on an axis line selects it, or deselects it when already selected; a click
        # anywhere else deselects it and places its marker as usual.
        axis = self.axis_at(position) if button == Qt.MouseButton.LeftButton else None
        if axis is not None:
            self.select_axis(None if axis == self.selected_axis else axis)
            return
        self.select_axis(None)
        if button == Qt.MouseButton.LeftButton:
            # Then the trace under the click, or none; the first marker is placed as usual.
            self.select_series(self.trace_at(position))
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
        return item

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
        state = {**super().workspace_state(), "axes_position": self.axes_position}
        if self.use_shader:
            state["use_shader"] = True
        if not np.all(self.axis_stretch == 1):
            state["axis_stretch"] = [float(value) for value in self.axis_stretch]
        return {**state, "camera": {
            "center": [center.x(), center.y(), center.z()], "distance": opts["distance"],
            "elevation": opts["elevation"], "azimuth": opts["azimuth"], "fov": opts["fov"]}}

    def restore_workspace_state(self, state):
        super().restore_workspace_state(state)
        self.set_axes_position(str(state.get("axes_position", AXES_ORIGIN)))
        self.set_use_shader(state.get("use_shader") is True)
        stretch = state.get("axis_stretch")
        if isinstance(stretch, list) and len(stretch) == 3 and all(isinstance(value, (int, float)) for value in stretch):
            self.set_axis_stretch(stretch)
        camera = state.get("camera")
        try:
            self.view.opts["fov"] = float(camera["fov"])
            self.view.setCameraPosition(pos=QVector3D(*(float(value) for value in camera["center"])),
                                        distance=float(camera["distance"]), elevation=float(camera["elevation"]),
                                        azimuth=float(camera["azimuth"]))
        except (KeyError, TypeError, ValueError):
            pass

    def center_view(self):
        """The shared Center view button: the camera back to the whole trace."""
        self.reset_camera()

    def reset_camera(self):
        self.view.setCameraPosition(pos=QVector3D(0, 0, 0), distance=6, elevation=25, azimuth=45)

    def apply_theme(self):
        for position, button in self.axes_buttons.items():
            button.setIcon(plot_control_icon(self, f"axes-{position}"))
        self.equal_scale_button.setIcon(plot_control_icon(self, "axes-equal"))
        from src.gui.plots.theme import palette_overlay_stylesheet
        self.use_shader_panel.setStyleSheet(palette_overlay_stylesheet())
        self.fit_axes_button.setIcon(plot_control_icon(self, "axes-fit"))
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
