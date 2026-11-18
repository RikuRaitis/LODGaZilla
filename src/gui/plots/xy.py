"""X/Y plot card for comparing two cached series."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import h5py
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from src.gui.plots.base import PlotBaseWidget, apply_view_range_state, view_range_state
from src.gui.plots.card_markers import PointMarkers
from src.gui.plots.interactive import use_full_viewport_updates
from src.gui.plots.legend import PlotLegendWidget
from src.gui.plots.legend_groups import group_display_name
from src.gui.plots.series_hover import install_series_name_hover, item_at_canvas_point, name_at_canvas_point
from src.gui.plots.series_style import SELECT_TOLERANCE_PIXELS, series_line_width
from src.data.sampling import SampledResult, decimation_step
from src.data.h5_series import _canonical_to_native_range, _visible_sample_bounds
from src.data.xyz import AxisSource, _aligned_values
from src.jobs.plot_groups import GroupedPlotLoadCoordinator
from src.gui.plots.theme import apply_plot_palette
from src.gui.plots.marker_info import MarkerInfoEntry, marker_entries_from_samples
from src.gui.plots.markers import (
    FIRST_MARKER, SECOND_MARKER, sync_marker_color, marker_color, marker_width, track_selection_layer, track_selection_rgba,
)
from src.gui.plots.value_boxes import ValueBoxItem, ValueBoxLayer
from src.gui.plots.series import (
    MarkerValueLoads,
    MarkerSample,
    SelectionStatistics,
    SelectionStatisticsLoadCoordinator,
    _selection_statistics_entry,
    _attribute_text,
    _canonical_time_values,
    _series_group_for_navigator_name,
    _time_axis_metadata_from_group,
)



# The selection drawn above every graph (Selection drawn: Always on top), below the
# marker points (19 and 20) and above the graphs (10, the selected one 15).
SELECTION_TOP_Z = 18

class XYInteractivePlotWidget(pg.PlotWidget):
    """Keep ordinary wheel input reserved for scrolling the plot workspace."""

    scroll_requested = Signal(int)

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        use_full_viewport_updates(self)

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.scroll_requested.emit(event.angleDelta().y())
            event.accept()
            return
        super().wheelEvent(event)


# Default points per X/Y graph; the card's Sample limit selector can change it.
XY_MAXIMUM_POINTS = 100_000


class XYPlotWidget(PointMarkers, PlotBaseWidget):
    """One or more X/Y graphs with independently assigned axis pairs."""
    has_center_view = True
    help_topic = "xy"

    marker_placed = Signal(float)
    marker_cleared = Signal()
    second_marker_placed = Signal(float)
    second_marker_cleared = Signal()
    accepts_axis_drop = True
    default_sample_limit = XY_MAXIMUM_POINTS
    # Auto draws each graph as a line with its points.
    supports_draw_style = True

    def __init__(
        self,
        x_entry: tuple[Path, str],
        y_entry: tuple[Path, str] | None = None,
        parent=None,
        *,
        time_range: tuple[float, float] | None = None,
    ) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*x_entry, parent)
        self.configure_legend_groups("XY")
        self.results = []
        self._loading = False
        self._auto_range_load = True
        self.loader = GroupedPlotLoadCoordinator(self, load_xy_group)
        self.loader.loaded.connect(self._loaded)
        self.plot_widget = XYInteractivePlotWidget(self)
        self.plot_widget.scroll_requested.connect(self.wheel_scrolled)
        self.plot_item = self.plot_widget.getPlotItem()
        self.plot_item.showGrid(x=True, y=True, alpha=0.2)
        # Right click places the second marker instead of opening pyqtgraph's menu.
        self.plot_item.setMenuEnabled(False)
        apply_plot_palette(self.plot_widget)
        self.curve = self.plot_item.plot(pen=pg.mkPen("#2196f3", width=1))
        self.points = pg.ScatterPlotItem(size=4, pen=pg.mkPen("#2196f3"), brush=pg.mkBrush("#2196f3"))
        self.plot_item.addItem(self.points)
        # One highlight per marker, and the selected stretch of each graph.
        self.highlights = [pg.ScatterPlotItem(size=10, brush=None) for _ in (FIRST_MARKER, SECOND_MARKER)]
        for highlight in self.highlights:
            highlight.setZValue(20)
            self.plot_item.addItem(highlight)
        self.highlight = self.highlights[FIRST_MARKER]
        # A Video card's stored sync data marker: a dashed purple ring on each graph.
        self.sync_highlight = pg.ScatterPlotItem(
            size=16, brush=None, pen=pg.mkPen(sync_marker_color(), width=2, style=Qt.PenStyle.DashLine))
        self.sync_highlight.setZValue(19)
        self.plot_item.addItem(self.sync_highlight)
        self._init_point_markers()
        self._selection_items: list[pg.PlotDataItem] = []
        # Marker times whose values boxes are shown; a reload shows them again.
        self._value_box_times: dict[int, float | None] = {FIRST_MARKER: None, SECOND_MARKER: None}
        self._graph_items = [(self.curve, self.points)]
        self._time = np.empty(0)
        self._x = np.empty(0)
        self._y = np.empty(0)
        self.time_range = time_range
        self._marker_loads = MarkerValueLoads(self, self._show_loaded_marker_data)
        self._selection_request_id = 0
        self._selection_loads = SelectionStatisticsLoadCoordinator(self)
        self._selection_loads.loaded.connect(self._show_loaded_selection_statistics)
        self.value_boxes = ValueBoxLayer(self.plot_widget)
        self.apply_marker_style()
        self.plot_widget.scene().sigMouseClicked.connect(self._on_scene_click)
        # A graph's X and Y sources are named while the pointer rests on its line or points.
        install_series_name_hover(self.plot_widget.viewport(), self.series_name_at)
        self.content_layout.addWidget(self.plot_widget, stretch=1)
        self.legend = PlotLegendWidget(self.plot_widget)
        self.legend.set_group_model(self.grouped_series)
        self.grouped_series.insert((AxisSource(*x_entry), AxisSource(*y_entry) if y_entry else None))

    def apply_theme(self) -> None:
        apply_plot_palette(self.plot_widget)
        self.value_boxes.apply_style()

    def center_view(self) -> None:
        """Show every graph in full again, as when it was loaded."""
        self.plot_item.autoRange()

    def apply_marker_style(self) -> None:
        for index, highlight in enumerate(self.highlights):
            highlight.setPen(pg.mkPen(marker_color(index), width=max(2, marker_width())))
        self._draw_selection()
        self.value_boxes.apply_style()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if hasattr(self, "legend"):
            self.legend.reposition()

    def add_series(self, h5_path: Path, series_name: str) -> None:
        self.add_legend_group((AxisSource(h5_path, series_name),))

    def on_series_changed(self, *, keep_graphs: bool = False) -> None:
        """Load the graphs again; ``keep_graphs`` shows the current ones until the new arrive.

        A legend change clears them at once, since they no longer match the groups; a new
        Sample limit or time range keeps them, so the card does not go blank.
        """
        self.hide_marker_data(forget=False)
        self._auto_range_load = True
        if not keep_graphs:
            # The graphs may have moved or gone: the selection does not follow them.
            self.select_series(None)
            for highlight in self.highlights:
                highlight.setData([], [])
            self._loaded([], auto_range=False)
        self._loading = True
        self.loader.request([group.axes for group in self.grouped_series.groups], self.time_range,
                            self.sample_limit)

    def workspace_state(self) -> dict:
        return {**super().workspace_state(), "view": view_range_state(self.plot_item.vb)}

    def apply_draw_style(self, style: str) -> None:
        for curve, points in self._graph_items:
            curve.setVisible(style != "points")
            points.setVisible(style != "lines")
        # The selected stretch follows: a line, points, or both.
        self._draw_selection()

    def restore_workspace_state(self, state: dict) -> None:
        super().restore_workspace_state(state)
        if state.get("view"):
            # The saved view replaces the fit to the data after loading.
            self._auto_range_load = False
            self.plot_item.vb.disableAutoRange()
            apply_view_range_state(self.plot_item.vb, state["view"])

    def graph_at(self, position):  # type: ignore[no-untyped-def]
        """The index of the graph drawn within SELECT_TOLERANCE_PIXELS of a canvas position, or None."""
        items = [(item, index) for index, (curve, points) in enumerate(self._graph_items)
                 if index < len(self.grouped_series.groups) for item in (curve, points)]
        return item_at_canvas_point(self.plot_widget, position, items, SELECT_TOLERANCE_PIXELS)

    def apply_series_widths(self):
        """Every graph at the series width, the selected one wider and in front (its selection too)."""
        for index, (curve, _points) in enumerate(self._graph_items):
            if index >= len(self.grouped_series.groups):
                continue
            selected = index == self.selected_series
            curve.setPen(pg.mkPen(self.grouped_series.groups[index].color, width=series_line_width(selected=selected)))
            curve.setZValue(15 if selected else 10)
        self._draw_selection()

    def selected_legend_rows(self):  # type: ignore[no-untyped-def]
        return (), self.selected_series

    def series_name_at(self, position):  # type: ignore[no-untyped-def]
        """The graph drawn under a canvas position, named by its sources, for the hover name."""
        items = [(item, group_display_name(group))
                 for group, (curve, points) in zip(self.grouped_series.groups, self._graph_items)
                 for item in (curve, points)]
        return name_at_canvas_point(self.plot_widget, position, items)

    def _loaded(self, results, *, auto_range=True):
        self._loading = False
        self.results = results
        self.update_sample_coverage(results)
        while len(self._graph_items) < len(self.grouped_series.groups):
            curve = self.plot_item.plot()
            points = pg.ScatterPlotItem(size=4)
            self.plot_item.addItem(points)
            self._graph_items.append((curve, points))
        for index, (curve, points) in enumerate(self._graph_items):
            result = results[index] if index < len(results) else None
            if not isinstance(result, tuple):
                curve.setData([], [])
                points.setData([], [])
                continue
            color = self.grouped_series.groups[index].color
            curve.setPen(pg.mkPen(color, width=series_line_width(selected=index == self.selected_series)))
            curve.setData(result[1], result[2])
            points.setData(result[1], result[2], pen=pg.mkPen(color), brush=pg.mkBrush(color))
        self.apply_draw_style(self.draw_style)
        complete = [result for result in results if isinstance(result, tuple)]
        self._time, self._x, self._y = (tuple(np.concatenate([result[index] for result in complete]) for index in range(3))
                                      if complete else (np.empty(0), np.empty(0), np.empty(0)))
        for side, label_index, role in (("bottom", 3, "X"), ("left", 4, "Y")):
            self.plot_item.setLabel(side, complete[0][label_index] if len(complete) == 1 else role)
        if auto_range and self._auto_range_load and complete:
            self.plot_item.enableAutoRange()
        # Markers keep their times across reloads; redraw them on the new data.
        self._redraw_markers()
        self._update_selection()
        if complete:
            # Values boxes follow the reloaded points (Sample limit, trim, legend change).
            for index, time_value in self._value_box_times.items():
                if time_value is not None:
                    self.show_marker_data(time_value, index)

    def _redraw_markers(self) -> None:
        """Each graph's points nearest the two markers and the sync marker."""
        self.sync_highlight.setPen(pg.mkPen(sync_marker_color(), width=2, style=Qt.PenStyle.DashLine))
        for item, time_value in ((self.highlights[FIRST_MARKER], self._marker_times[FIRST_MARKER]),
                                 (self.highlights[SECOND_MARKER], self._marker_times[SECOND_MARKER]),
                                 (self.sync_highlight, self._sync_time)):
            points = [point for point in (self._point_at(result, time_value) for result in self.results) if point]
            item.setData([point[0] for point in points], [point[1] for point in points])

    @staticmethod
    def _point_at(result, time_value: float | None) -> tuple[float, float] | None:  # type: ignore[no-untyped-def]
        """The graph's point nearest a marker time, or None outside the graph."""
        if time_value is None or not _result_contains_time(result, time_value):
            return None
        # Times ascend: a binary search instead of a pass over every point.
        times = result[0]
        upper = min(int(np.searchsorted(times, time_value)), len(times) - 1)
        lower = max(0, upper - 1)
        index = lower if abs(time_value - times[lower]) <= abs(times[upper] - time_value) else upper
        return float(result[1][index]), float(result[2][index])

    def _update_selection(self) -> None:
        """Highlight each graph between the markers and load the selection statistics."""
        selection = self.selection_range()
        self._draw_selection(selection)
        if selection is None:
            self._selection_request_id = self._selection_loads.invalidate()
            self.value_boxes.set_selection_duration(None)
            return
        scale = next((getattr(result, "seconds_per_time_unit", 1.0) for result in self.results
                      if isinstance(result, tuple)), 1.0)
        self.value_boxes.set_selection_duration((selection[1] - selection[0]) * scale)
        if self.series:
            self._selection_request_id = self._selection_loads.request(*selection, list(self.series))

    def _draw_selection(self, selection=None) -> None:  # type: ignore[no-untyped-def]
        """Each graph's stretch between the markers, recolored (Settings > Plot)."""
        if selection is None:
            selection = self.selection_range()
        while len(self._selection_items) < len(self.results):
            item = pg.PlotDataItem()
            self.plot_item.addItem(item)
            self._selection_items.append(item)
        # The stretch between the markers in the selection color, at its graph's width: above
        # every graph, above its own graph only, or not drawn (Settings > Plot). The marker
        # points stay on top.
        layer, rgba = track_selection_layer(), track_selection_rgba()
        for index, item in enumerate(self._selection_items):
            result = self.results[index] if index < len(self.results) else None
            if selection is None or not isinstance(result, tuple) or layer == "off":
                item.setData([], [])
                continue
            inside = (result[0] >= selection[0]) & (result[0] <= selection[1])
            width = series_line_width(selected=index == self.selected_series)
            lines, points = self.draw_style != "points", self.draw_style != "lines"
            item.setData(result[1][inside], result[2][inside],
                         pen=pg.mkPen(rgba, width=width) if lines else None,
                         symbol="o" if points else None, symbolSize=4, symbolPen=None, symbolBrush=pg.mkBrush(rgba))
            own = self._graph_items[index][0].zValue() if index < len(self._graph_items) else 10
            item.setZValue(SELECTION_TOP_Z if layer == "always_top" else own + 0.5)

    def _show_loaded_selection_statistics(
        self, request_id: int, _left: float, _right: float, statistics: list[SelectionStatistics]
    ) -> None:
        if request_id != self._selection_request_id:
            return
        by_entry = {self.series[item.series_index]: item for item in statistics if item.series_index < len(self.series)}
        details = {}
        for group in self.grouped_series.groups:
            lines = []
            for role, axis in zip(("X", "Y"), group.axes):
                item = by_entry.get((axis.path, axis.name)) if axis is not None else None
                if item is not None:
                    lines.extend(f"{role} {line}" for line in _selection_statistics_entry(item, group.color).details)
            details[_graph_key(group)] = tuple(lines)
        self.value_boxes.set_statistics(details)

    def show_marker_data(self, time_value: float, index: int = FIRST_MARKER) -> None:
        # An X/Y plot made from a line-plot selection represents only that
        # selected time window.  Do not show values from the full source H5
        # series when a linked marker is outside this plot's own data.
        self._value_box_times[index] = float(time_value)
        if not self._contains_time(time_value):
            self.hide_marker_data(index, forget=False)
            return
        self._marker_loads.request(index, time_value, list(self.series))

    def hide_marker_data(self, index: int | None = None, *, forget: bool = True) -> None:
        """Hide values boxes; ``forget=False`` shows them again after the next reload."""
        self._marker_loads.invalidate(index)
        for marker in (FIRST_MARKER, SECOND_MARKER) if index is None else (index,):
            self.value_boxes.clear(marker)
            if forget:
                self._value_box_times[marker] = None

    def dispose(self) -> None:
        self.loader.shutdown()
        self._marker_loads.shutdown()
        self._selection_loads.shutdown()

    def set_data_time_range(self, time_range: tuple[float, float] | None) -> None:
        """Apply a navigator data filter without changing the X/Y view."""
        self.time_range = tuple(sorted(time_range)) if time_range is not None else None
        self._reload_keeping_view()

    def on_sample_limit_changed(self) -> None:
        self._reload_keeping_view()

    def _reload_keeping_view(self) -> None:
        view_range = self.plot_item.vb.viewRange()
        self.on_series_changed(keep_graphs=True)
        self._auto_range_load = False
        self.plot_item.vb.setRange(xRange=view_range[0], yRange=view_range[1], padding=0)

    def _contains_time(self, time_value: float) -> bool:
        if not len(self._time):
            return False
        return any(_result_contains_time(result, time_value) for result in self.results)

    def _on_scene_click(self, mouse_event) -> None:  # type: ignore[no-untyped-def]
        """Left click: first marker; right click: second; a double click removes it."""
        index = {Qt.MouseButton.LeftButton: FIRST_MARKER,
                 Qt.MouseButton.RightButton: SECOND_MARKER}.get(mouse_event.button())
        if index is None:
            return
        mouse_event.accept()
        if mouse_event.double():
            self._clear_marker_from_view(index)
            return
        if index == FIRST_MARKER:
            # A left click selects the graph under it, or none, and still places the first marker.
            self.select_series(self.graph_at(self.plot_widget.mapFromScene(mouse_event.scenePos())))
        if not len(self._time):
            return
        point = self.plot_item.vb.mapSceneToView(mouse_event.scenePos())
        distances = (self._x - point.x()) ** 2 + (self._y - point.y()) ** 2
        if not np.any(np.isfinite(distances)):
            return
        self._place_marker_from_view(index, float(self._time[int(np.nanargmin(distances))]))

    def _show_loaded_marker_data(self, index: int, time_value: float, samples: list[MarkerSample]) -> None:
        """One values box per graph, at its point nearest the marker."""
        entries = dict(zip((self.series[sample.series_index] for sample in samples),
                           marker_entries_from_samples(samples, ["#000000"] * len(self.series))))
        items = []
        for group, result in zip(self.grouped_series.groups, self.results):
            point = self._point_at(result, time_value)
            axes = [entries.get((axis.path, axis.name)) if axis is not None else None for axis in group.axes]
            if point is None or None in axes:
                continue
            x_entry, y_entry = axes
            entry = MarkerInfoEntry(
                f"{x_entry.name} / {y_entry.name}", group.color, f"{x_entry.value}, {y_entry.value}",
                tuple(f"X {detail}" for detail in x_entry.details) + tuple(f"Y {detail}" for detail in y_entry.details),
                full_name=group_display_name(group),
            )
            items.append(ValueBoxItem(_graph_key(group), entry, point[1], x_value=point[0]))
        self.value_boxes.set_items(index, time_value, items)


def load_xy_data(
    x_path: Path,
    x_name: str,
    y_path: Path,
    y_name: str,
    *,
    time_range: tuple[float, float] | None = None,
    maximum_points: int = XY_MAXIMUM_POINTS,
    cancelled=None,  # type: ignore[no-untyped-def]
) -> tuple[np.ndarray, np.ndarray, np.ndarray, str, str]:
    """Align Y samples to the closest X timestamp, preserving the shared time marker.

    Only X samples inside the Y time coverage are paired, so Y is never
    extrapolated as a flat run, and absolute and relative times are not mixed.
    Memory stays bounded whatever the series length: X is read every Nth sample
    within the range, and Y is searched in chunks (as the 3D plot loader does).
    """
    with h5py.File(x_path, "r") as x_file, h5py.File(y_path, "r") as y_file:
        x_group = _series_group_for_navigator_name(x_file, x_name)
        y_group = _series_group_for_navigator_name(y_file, y_name)
        x_metadata = _time_axis_metadata_from_group(x_file, x_group)
        y_metadata = _time_axis_metadata_from_group(y_file, y_group)
        x_absolute = x_metadata.is_unix_time or x_metadata.log_start_unix_seconds is not None
        y_absolute = y_metadata.is_unix_time or y_metadata.log_start_unix_seconds is not None
        if x_absolute != y_absolute:
            raise ValueError("Cannot align absolute timestamps with series lacking a log start time")
        x_times = x_group[_attribute_text(x_group.attrs.get("time_dataset", "time_unix"))]
        y_times = y_group[_attribute_text(y_group.attrs.get("time_dataset", "time_unix"))]
        if not len(x_times) or not len(y_times):
            raise ValueError("Series has no samples")
        if not x_absolute:
            # Relative times stay in X's native unit; Y is read in that unit.
            ratio = (y_metadata.seconds_per_unit / x_metadata.seconds_per_unit
                     if y_metadata.seconds_per_unit > 0 and x_metadata.seconds_per_unit > 0 else 1.0)
            y_metadata = replace(y_metadata, is_unix_time=True, seconds_per_unit=ratio)
        # Y times ascend, so its first and last samples bound its coverage.
        y_bounds = _canonical_time_values(np.asarray([y_times[0], y_times[-1]], dtype=float), y_metadata)
        if not np.all(np.isfinite(y_bounds)):
            raise ValueError("Y series has no valid timestamps")
        # Endpoint clamping outside measured coverage invents constant tails,
        # even when the gap is less than half a sample interval.
        lower, upper = float(y_bounds.min()), float(y_bounds.max())
        if time_range is not None:
            lower, upper = max(lower, min(time_range)), min(upper, max(time_range))
        start, stop = (0, 0) if lower > upper else _visible_sample_bounds(
            x_times, _canonical_to_native_range((lower, upper), x_metadata))
        # Keep interaction responsive for very large raw series.
        available = max(0, stop - start)
        step = decimation_step(available, maximum_points)
        x_time = _canonical_time_values(np.asarray(x_times[start:stop:step], dtype=float), x_metadata)
        x_values = np.asarray(x_group["value"][start:stop:step], dtype=float)
        selected = (x_time >= lower) & (x_time <= upper)
        x_time, x_values = x_time[selected], x_values[selected]
        if not len(x_time):
            raise ValueError("Selected area has no samples" if time_range is not None
                             else "X and Y series have no overlapping time range")
        y_values = _aligned_values(y_times, y_group["value"], y_metadata, x_time, cancelled)
        x_label = f"{_attribute_text(x_group.attrs.get('navigator_name', x_name))} {_attribute_text(x_group.attrs.get('unit', ''))}".strip()
        y_label = f"{_attribute_text(y_group.attrs.get('navigator_name', y_name))} {_attribute_text(y_group.attrs.get('unit', ''))}".strip()
        result = SampledResult((x_time, x_values, y_values, x_label, y_label),
                               shown_points=len(x_time), available_points=available)
        # Relative times stay in X's native unit; the selection's Δt converts them to seconds.
        result.seconds_per_time_unit = 1.0 if x_absolute else x_metadata.seconds_per_unit
        return result


def _graph_key(group) -> tuple:  # type: ignore[no-untyped-def]
    """Identify an X/Y graph by its axis sources, for its values boxes."""
    return tuple((axis.path, axis.name) if axis is not None else None for axis in group.axes)


def _result_contains_time(result, time_value: float) -> bool:
    """X/Y times are ascending, so the first and last samples bound the graph."""
    return (isinstance(result, tuple) and len(result[0]) > 0
            and float(result[0][0]) <= float(time_value) <= float(result[0][-1]))


def load_xy_group(axes, *, time_range=None, cancelled=None, maximum_points=XY_MAXIMUM_POINTS):
    return load_xy_data(axes[0].path, axes[0].name, axes[1].path, axes[1].name, time_range=time_range,
                        maximum_points=maximum_points, cancelled=cancelled)
