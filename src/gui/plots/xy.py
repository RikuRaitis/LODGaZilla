"""X/Y plot card for comparing two cached series."""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from src.gui.plots.base import PlotBaseWidget, apply_view_range_state, view_range_state
from src.gui.plots.interactive import use_full_viewport_updates
from src.gui.plots.legend import PlotLegendWidget
from src.data.sampling import SampledResult, decimation_step
from src.data.xyz import AxisSource, nearest_sample_indices
from src.jobs.plot_groups import GroupedPlotLoadCoordinator
from src.gui.plots.theme import apply_plot_palette
from src.gui.plots.marker_info import MarkerInfoEntry, marker_entries_from_samples
from src.gui.plots.markers import (
    FIRST_MARKER, SECOND_MARKER, sync_marker_color, marker_color, marker_width, selection_rgba,
)
from src.gui.plots.value_boxes import ValueBoxItem, ValueBoxLayer
from src.gui.plots.series import (
    MarkerDataLoadCoordinator,
    MarkerSample,
    SelectionStatistics,
    SelectionStatisticsLoadCoordinator,
    _selection_statistics_entry,
    _attribute_text,
    _canonical_time_values,
    _series_group_for_navigator_name,
    _time_axis_metadata_from_group,
)


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


class XYPlotWidget(PlotBaseWidget):
    """One or more X/Y graphs with independently assigned axis pairs."""
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
        self._sync_time: float | None = None
        self._selection_items: list[pg.PlotDataItem] = []
        self._marker_times: dict[int, float | None] = {FIRST_MARKER: None, SECOND_MARKER: None}
        self._graph_items = [(self.curve, self.points)]
        self._time = np.empty(0)
        self._x = np.empty(0)
        self._y = np.empty(0)
        self.time_range = time_range
        self._marker_request_ids = {FIRST_MARKER: 0, SECOND_MARKER: 0}
        self._marker_loads = {index: MarkerDataLoadCoordinator(self) for index in (FIRST_MARKER, SECOND_MARKER)}
        for index, loads in self._marker_loads.items():
            loads.loaded.connect(lambda request_id, time_value, samples, index=index:
                                 self._show_loaded_marker_data(index, request_id, time_value, samples))
        self._selection_request_id = 0
        self._selection_loads = SelectionStatisticsLoadCoordinator(self)
        self._selection_loads.loaded.connect(self._show_loaded_selection_statistics)
        self.value_boxes = ValueBoxLayer(self.plot_widget)
        self.apply_marker_style()
        self.plot_widget.scene().sigMouseClicked.connect(self._on_scene_click)
        self.content_layout.addWidget(self.plot_widget, stretch=1)
        self.legend = PlotLegendWidget(self.plot_widget)
        self.legend.set_group_model(self.grouped_series)
        self.grouped_series.insert((AxisSource(*x_entry), AxisSource(*y_entry) if y_entry else None))

    def apply_theme(self) -> None:
        apply_plot_palette(self.plot_widget)
        self.value_boxes.apply_style()

    def apply_marker_style(self) -> None:
        for index, highlight in enumerate(self.highlights):
            highlight.setPen(pg.mkPen(marker_color(index), width=max(2, marker_width())))
        for item in self._selection_items:
            item.setPen(pg.mkPen(selection_rgba(2.0), width=6))
        self.value_boxes.apply_style()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if hasattr(self, "legend"):
            self.legend.reposition()

    def add_series(self, h5_path: Path, series_name: str) -> None:
        self.add_legend_group((AxisSource(h5_path, series_name),))

    def on_series_changed(self) -> None:
        self.hide_marker_data()
        for highlight in self.highlights:
            highlight.setData([], [])
        self._auto_range_load = True
        self._loading = True
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

    def restore_workspace_state(self, state: dict) -> None:
        super().restore_workspace_state(state)
        if state.get("view"):
            # The saved view replaces the fit to the data after loading.
            self._auto_range_load = False
            self.plot_item.vb.disableAutoRange()
            apply_view_range_state(self.plot_item.vb, state["view"])

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
            curve.setPen(pg.mkPen(color, width=1))
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
        for index in (FIRST_MARKER, SECOND_MARKER):
            self._draw_marker(index)
        self._update_selection()
        self.set_sync_marker(self._sync_time)

    def set_vertical_marker(self, time_value: float, index: int = FIRST_MARKER) -> None:
        self._marker_times[index] = float(time_value)
        self._draw_marker(index)
        self._update_selection()

    def clear_vertical_marker(self, index: int = FIRST_MARKER) -> None:
        self._marker_times[index] = None
        self.highlights[index].setData([], [])
        self.hide_marker_data(index)
        self._update_selection()

    def marker_values(self) -> tuple[float | None, float | None]:
        return self._marker_times[FIRST_MARKER], self._marker_times[SECOND_MARKER]

    def set_sync_marker(self, time_value: float | None) -> None:
        self._sync_time = time_value
        self.sync_highlight.setPen(pg.mkPen(sync_marker_color(), width=2, style=Qt.PenStyle.DashLine))
        points = [point for point in (self._point_at(result, time_value) for result in self.results) if point]
        self.sync_highlight.setData([point[0] for point in points], [point[1] for point in points])

    def _draw_marker(self, index: int) -> None:
        points = [self._point_at(result, self._marker_times[index]) for result in self.results]
        points = [point for point in points if point is not None]
        self.highlights[index].setData([point[0] for point in points], [point[1] for point in points])

    @staticmethod
    def _point_at(result, time_value: float | None) -> tuple[float, float] | None:  # type: ignore[no-untyped-def]
        """The graph's point nearest a marker time, or None outside the graph."""
        if time_value is None or not _result_contains_time(result, time_value):
            return None
        index = int(np.argmin(np.abs(result[0] - time_value)))
        return float(result[1][index]), float(result[2][index])

    def selection_range(self) -> tuple[float, float] | None:
        times = self.marker_values()
        return None if None in times else tuple(sorted(times))

    def _update_selection(self) -> None:
        """Highlight each graph between the markers and load the selection statistics."""
        selection = self.selection_range()
        while len(self._selection_items) < len(self.results):
            # Above the graph so it stays visible, and translucent so the graph's
            # color shows through; the marker points stay on top.
            item = pg.PlotDataItem(pen=pg.mkPen(selection_rgba(2.0), width=6))
            item.setZValue(10)
            self.plot_item.addItem(item)
            self._selection_items.append(item)
        for index, item in enumerate(self._selection_items):
            result = self.results[index] if index < len(self.results) else None
            if selection is None or not isinstance(result, tuple):
                item.setData([], [])
                continue
            inside = (result[0] >= selection[0]) & (result[0] <= selection[1])
            item.setData(result[1][inside], result[2][inside])
        if selection is None:
            self._selection_request_id = self._selection_loads.invalidate()
            self.value_boxes.set_selection_duration(None)
            return
        scale = next((getattr(result, "seconds_per_time_unit", 1.0) for result in self.results
                      if isinstance(result, tuple)), 1.0)
        self.value_boxes.set_selection_duration((selection[1] - selection[0]) * scale)
        if self.series:
            self._selection_request_id = self._selection_loads.request(*selection, list(self.series))

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
        if not self._contains_time(time_value):
            self.hide_marker_data(index)
            return
        self._marker_request_ids[index] = self._marker_loads[index].request(time_value, list(self.series))

    def hide_marker_data(self, index: int | None = None) -> None:
        for marker in (FIRST_MARKER, SECOND_MARKER) if index is None else (index,):
            self._marker_request_ids[marker] = self._marker_loads[marker].invalidate()
            self.value_boxes.clear(marker)

    def dispose(self) -> None:
        self.loader.shutdown()
        for loads in self._marker_loads.values():
            loads.shutdown()
        self._selection_loads.shutdown()

    def set_data_time_range(self, time_range: tuple[float, float] | None) -> None:
        """Apply a navigator data filter without changing the X/Y view."""
        self.time_range = tuple(sorted(time_range)) if time_range is not None else None
        self._reload_keeping_view()

    def on_sample_limit_changed(self) -> None:
        self._reload_keeping_view()

    def _reload_keeping_view(self) -> None:
        view_range = self.plot_item.vb.viewRange()
        self.on_series_changed()
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
            self.clear_vertical_marker(index)
            (self.marker_cleared if index == FIRST_MARKER else self.second_marker_cleared).emit()
            return
        if not len(self._time):
            return
        point = self.plot_item.vb.mapSceneToView(mouse_event.scenePos())
        distances = (self._x - point.x()) ** 2 + (self._y - point.y()) ** 2
        if not np.any(np.isfinite(distances)):
            return
        time_value = float(self._time[int(np.nanargmin(distances))])
        self.set_vertical_marker(time_value, index)
        (self.marker_placed if index == FIRST_MARKER else self.second_marker_placed).emit(time_value)

    def _show_loaded_marker_data(
        self, index: int, request_id: int, time_value: float, samples: list[MarkerSample]
    ) -> None:
        """One values box per graph, at its point nearest the marker."""
        if request_id != self._marker_request_ids[index]:
            return
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
) -> tuple[np.ndarray, np.ndarray, np.ndarray, str, str]:
    """Align Y samples to the closest X timestamp, preserving the shared time marker.

    Only X samples inside the Y time coverage are paired, so Y is never
    extrapolated as a flat run, and absolute and relative times are not mixed.
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
        x_time_dataset = x_group[_attribute_text(x_group.attrs.get("time_dataset", "time_unix"))]
        x_time = _canonical_time_values(np.asarray(x_time_dataset, dtype=float), x_metadata)
        x_values = np.asarray(x_group["value"], dtype=float)
        y_times = _canonical_time_values(
            np.asarray(y_group[_attribute_text(y_group.attrs.get("time_dataset", "time_unix"))], dtype=float),
            y_metadata,
        )
        if not len(x_time) or not len(y_times):
            raise ValueError("Series has no samples")
        if not x_absolute and y_metadata.seconds_per_unit > 0 and x_metadata.seconds_per_unit > 0:
            # Relative times stay in X's native unit; match Y in that unit.
            y_times = y_times * (y_metadata.seconds_per_unit / x_metadata.seconds_per_unit)
        finite_y = y_times[np.isfinite(y_times)]
        if not len(finite_y):
            raise ValueError("Y series has no valid timestamps")
        # Endpoint clamping outside measured coverage invents constant tails,
        # even when the gap is less than half a sample interval.
        lower, upper = float(finite_y.min()), float(finite_y.max())
        if time_range is not None:
            lower, upper = max(lower, min(time_range)), min(upper, max(time_range))
        selected = (x_time >= lower) & (x_time <= upper)
        x_time, x_values = x_time[selected], x_values[selected]
        if not len(x_time):
            raise ValueError("Selected area has no samples" if time_range is not None
                             else "X and Y series have no overlapping time range")
        # Keep interaction responsive for very large raw series.
        available = len(x_time)
        step = decimation_step(available, maximum_points)
        x_time, x_values = x_time[::step], x_values[::step]
        indices = nearest_sample_indices(y_times, x_time)
        # Several X samples may use the same Y sample after an axis replacement.
        # h5py fancy indexing requires strictly increasing, unique indices.
        unique, inverse = np.unique(indices, return_inverse=True)
        y_values = np.asarray(y_group["value"][unique], dtype=float)[inverse]
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
                        maximum_points=maximum_points)
