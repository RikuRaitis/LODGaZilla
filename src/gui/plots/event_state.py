"""Event and state timeline plot card for LODGaZilla."""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import h5py
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QFontMetricsF, QPalette
from PySide6.QtWidgets import (
    QColorDialog,
    QGraphicsSimpleTextItem,
    QWidget,
)

from src.gui.plots.interactive import InteractivePlotWidget
from src.gui.plots.selection_info import SelectionDurationInfo
from src.gui.plots.series_hover import install_series_name_hover, item_at_canvas_point, name_at_canvas_point
from src.gui.plots.series_style import SELECT_TOLERANCE_PIXELS, series_line_width
from src.gui.plots.base import (
    SERIES_COLORS, PlotBaseWidget, apply_view_range_state, assign_series_colors, series_legend_name, state_entry,
    view_range_state,
)
from src.gui.plots.card_markers import TimeCardMarkers
from src.gui.plots.legend import LegendEntry, PlotLegendWidget
from src.gui.plots.theme import apply_plot_palette
from src.gui.plots.marker_info import marker_entries_from_samples
from src.gui.plots.markers import FIRST_MARKER, SECOND_MARKER
from src.gui.plots.value_boxes import ValueBoxItem, ValueBoxLayer
from src.gui.plots.series import (
    TimeAxisMetadata,
    MarkerValueLoads,
    MarkerSample,
    VisibleDataLoadCoordinator,
    _attribute_text,
    _canonical_time_values,
    _canonical_to_native_range,
    _display_time_axis_metadata,
    _series_group_for_navigator_name,
    _time_axis_metadata_from_group,
    _visible_sample_bounds,
)


EVENT_COLORS = SERIES_COLORS


# Samples read for one lane's view. A lane a few thousand pixels wide shows no more; with
# more in view, every Nth sample is read (the drawing merges sub-pixel bars anyway).
EVENT_STATE_MAX_SAMPLES = 200_000


def load_event_state_series(
    h5_path: Path, series_name: str, *, time_range: tuple[float, float] | None = None
) -> dict[str, object]:
    """Read only the requested timeline window, retaining both state edges."""
    with h5py.File(h5_path, "r") as h5_file:
        group = _series_group_for_navigator_name(h5_file, series_name)
        dataset_name = _attribute_text(group.attrs.get("time_dataset", "time_unix"))
        metadata = _time_axis_metadata_from_group(h5_file, group)
        labels_raw = _attribute_text(group.attrs.get("state_labels_json", "{}"))
        try:
            labels_value = json.loads(labels_raw)
        except json.JSONDecodeError:
            labels_value = {}
        labels = (
            {str(key): str(value) for key, value in labels_value.items()}
            if isinstance(labels_value, dict)
            else {}
        )
        time_dataset = group[dataset_name]
        sample_count = len(time_dataset)
        start, stop = 0, sample_count
        if time_range is not None and sample_count:
            native_range = _canonical_to_native_range(tuple(sorted(time_range)), metadata)
            start, stop = _visible_sample_bounds(time_dataset, native_range)
            # The preceding transition establishes the state at the left
            # viewport edge; the following one closes its final rectangle.
            start = max(0, start - 1)
            stop = min(sample_count, stop + 1)
        step = max(1, -(-(stop - start) // EVENT_STATE_MAX_SAMPLES))
        native_times = np.asarray(time_dataset[start:stop:step], dtype=np.float64)
        times = _canonical_time_values(native_times, metadata)
        file_start_seconds = h5_file.attrs.get("time_start_seconds")
        file_end_seconds = h5_file.attrs.get("time_end_seconds")
        def canonical_file_boundary(value, fallback: float) -> float:  # type: ignore[no-untyped-def]
            try:
                raw_seconds = float(value)
            except (TypeError, ValueError):
                return fallback
            if metadata.is_unix_time:
                return raw_seconds
            if metadata.log_start_unix_seconds is not None:
                return metadata.log_start_unix_seconds + (
                    raw_seconds - metadata.raw_origin * metadata.seconds_per_unit
                )
            return raw_seconds / metadata.seconds_per_unit if metadata.seconds_per_unit else fallback
        return {
            "time": times,
            "value": np.asarray(group["value"][start:stop:step], dtype=np.float64),
            "name": _attribute_text(group.attrs.get("navigator_name", series_name)),
            "unit": _attribute_text(group.attrs.get("unit", "")),
            "kind": _attribute_text(group.attrs.get("event_state_kind", "")),
            "labels": labels,
            "metadata": metadata,
            "h5_start": canonical_file_boundary(file_start_seconds, float(times[0]) if len(times) else 0.0),
            "h5_end": canonical_file_boundary(file_end_seconds, float(times[-1]) if len(times) else 0.0),
        }


def is_event_state_series(h5_path: Path, series_name: str) -> bool:
    """Return whether a series holds events or states (`is_event_state_group`), drawn as a timeline."""
    from src.data.h5_series import is_event_state_group

    try:
        with h5py.File(h5_path, "r") as h5_file:
            return is_event_state_group(_series_group_for_navigator_name(h5_file, series_name))
    except (OSError, KeyError, ValueError):
        return False


EVENT_STATE_HALF_HEIGHT = 0.36


def event_state_lane_height(plot_item):
    """Shared bar height in logical pixels: label font plus four pixels per side."""
    return QFontMetricsF(plot_item.vb.getViewWidget().font()).height() + 8


def update_event_state_lane_heights(plot_item, items):
    """Keep timeline bars text-sized regardless of ViewBox scaling or card height."""
    pixel_height = event_state_lane_height(plot_item)
    units_per_pixel = abs(plot_item.vb.viewPixelSize()[1])
    for item in items:
        if isinstance(item, pg.BarGraphItem):
            gain = abs(item.transform().m22())
            if not gain:
                continue
            half = pixel_height * units_per_pixel / (2 * gain)
            center = (item.opts['y0'] + item.opts['y1']) / 2
            if not np.allclose(item.opts['y1'] - item.opts['y0'], 2 * half):
                item.setOpts(y0=center - half, y1=center + half)


def _event_state_label(value, labels) -> str:  # type: ignore[no-untyped-def]
    """A state's or event's name from the label map, or its value."""
    value_key = str(int(value)) if float(value).is_integer() else f"{value:g}"
    return str(labels.get(value_key, value_key))


def _merge_below_pixel(starts, ends, values, plot_item):  # type: ignore[no-untyped-def]
    """Bars narrower than a pixel, more than the lane has pixels: one bar per pixel column.

    A merged bar spans its column's bars and keeps the first one's value, so a dense lane
    draws a bounded number of bars at any zoom level.
    """
    width_pixels = max(1.0, float(plot_item.vb.width()))
    if len(starts) <= 2 * width_pixels:
        return starts, ends, values
    left, right = float(starts[0]), float(ends[-1])
    pixel = (right - left) / width_pixels
    if not np.isfinite(pixel) or pixel <= 0:
        return starts, ends, values
    columns = np.floor((starts - left) / pixel)
    _, first = np.unique(columns, return_index=True)
    merged_ends = np.maximum.reduceat(ends, first)
    return starts[first], merged_ends, values[first]


def render_event_state_lane(plot_item, lane, data, color, outline_width=None):
    """Draw the shared timeline bars and labels; return their graphics items. The bars'
    outline is ``outline_width`` pixels, by default the series line width."""
    outline_width = series_line_width() if outline_width is None else outline_width
    items = []
    times = np.asarray(data["time"], dtype=float)
    values = np.asarray(data["value"], dtype=float)
    if not len(times):
        return items
    labels = data["labels"] if isinstance(data["labels"], dict) else {}
    kind = str(data.get("kind", "")).lower()
    # An explicit kind decides; otherwise a configured state-label map identifies state
    # data. Other series are rendered as indexed events, so the UI remains useful for new data.
    is_state = kind == "state" or (kind != "event" and (bool(labels) or _looks_like_binary_state(values)))
    is_boolean = _looks_like_binary_state(values)
    span = max(0.001, float(times[-1] - times[0]))
    h5_end = max(float(data.get("h5_end", times[-1])), float(times[-1]))
    extend_to_h5_end = str(data.get("unit", "")).strip().casefold() == "bool"
    if is_state:
        # States begin at their recorded timestamps; the earlier state is unknown.
        starts = times
        # Boolean states persist through the end of the file after their last transition.
        ends = np.concatenate((times[1:], [(h5_end if extend_to_h5_end else times[-1])]))
    else:
        # Keep event boxes visible while clipping the outer boxes to the
        # recorded data window.  In particular, no event extends before
        # the first or beyond the last sample.
        event_width = max(0.001, span * 0.018)
        if len(times) > 1:
            # Close events stay separate boxes: at most 60% of their typical spacing.
            gaps = np.diff(times)
            gaps = gaps[gaps > 0]
            if len(gaps):
                event_width = max(1e-6, min(event_width, 0.6 * float(np.median(gaps))))
        starts = np.maximum(times - event_width / 2, times[0])
        ends = np.minimum(times + event_width / 2, times[-1])
    if is_state and len(times) > 1:
        # A state lasts until it changes: repeated samples of one state are one bar.
        changes = np.concatenate(([True], values[1:] != values[:-1]))
        if not np.all(changes):
            kept = np.flatnonzero(changes)
            ends = np.concatenate((starts[kept][1:], ends[-1:]))
            starts, values = starts[kept], values[kept]
    starts, ends, values = _merge_below_pixel(starts, ends, values, plot_item)
    if is_boolean:
        for state in ("0", "1"):
            mask = values == float(state)
            if not np.any(mask):
                continue
            state_color = (QColor(color).darker(170).name() if state == "0" else color)
            brush = QColor(state_color)
            brush.setAlpha(170)
            boxes = pg.BarGraphItem(
                x0=starts[mask], x1=ends[mask], y0=np.full(np.count_nonzero(mask), lane - EVENT_STATE_HALF_HEIGHT),
                y1=np.full(np.count_nonzero(mask), lane + EVENT_STATE_HALF_HEIGHT), brush=brush,
                pen=pg.mkPen(state_color, width=outline_width),
            )
            plot_item.addItem(boxes)
            items.append(boxes)
    else:
        brush = QColor(color)
        brush.setAlpha(170)
        boxes = pg.BarGraphItem(
            x0=starts, x1=ends, y0=np.full(len(starts), lane - EVENT_STATE_HALF_HEIGHT),
            y1=np.full(len(starts), lane + EVENT_STATE_HALF_HEIGHT), brush=brush,
            pen=pg.mkPen(color, width=outline_width),
        )
        plot_item.addItem(boxes)
        items.append(boxes)
    # Text items are graphics-scene objects, so a busy event log must not
    # create one for every sample. Bars remain exact; labels are selected
    # at a pixel-informed density and become more detailed as users zoom.
    # Each label gets room for the widest one, so thinned labels do not overlap.
    font = plot_item.vb.getViewWidget().font()
    # Texts only for the distinct values (for the width) and the labels drawn, not every sample.
    metrics = QFontMetricsF(font)
    label_width = max(42.0, max(metrics.horizontalAdvance(_event_state_label(value, labels))
                                for value in np.unique(values)) + 12.0)
    label_budget = max(1, int(max(1.0, plot_item.vb.width()) / label_width))
    label_stride = max(1, int(np.ceil(len(starts) / label_budget)))
    shown = np.unique(np.concatenate((np.arange(0, len(starts), label_stride), [len(starts) - 1])))
    for start, end, value in zip(starts[shown], ends[shown], values[shown]):
        text = _event_state_label(value, labels)
        label = pg.TextItem(text, color="#ffffff", anchor=(0.5, 0.5))
        label.setFont(font)
        label.textItem.document().setDocumentMargin(0)
        label.setPos((float(start) + float(end)) / 2, lane)
        plot_item.addItem(label)
        items.append(label)

    update_event_state_lane_heights(plot_item, items)
    return items


class EventStatePlotWidget(TimeCardMarkers, PlotBaseWidget):
    """Compact one-lane-per-series timeline for indexed events and states."""
    has_center_view = True
    # Its lanes keep their own heights: Center view changes only the time axis.
    center_view_fits_y = False
    help_topic = "event"

    accepts_series_drop = True

    # Colors are keyed by source in `_series_colors`, so inserting, removing
    # or reordering series keeps their colors; inserted series get an unused one.

    marker_placed = Signal(float)
    marker_cleared = Signal()
    second_marker_placed = Signal(float)
    second_marker_cleared = Signal()
    time_axis_representation_changed = Signal(str)

    def __init__(self, h5_path: Path, series_name: str, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(h5_path, series_name, parent)
        self.plot_widget = InteractivePlotWidget(self)
        self.plot_widget.y_axis_zoom_enabled = False
        self.plot_widget.scroll_requested.connect(self.wheel_scrolled)
        self.plot_widget.marker_placed.connect(self.marker_placed)
        self.plot_widget.marker_cleared.connect(self.marker_cleared)
        self.plot_widget.second_marker_placed.connect(self.second_marker_placed)
        self.plot_widget.second_marker_cleared.connect(self.second_marker_cleared)
        self.plot_widget.time_axis_representation_changed.connect(self.time_axis_representation_changed)
        self.plot_item = self.plot_widget.getPlotItem()
        # Auto-ranged time fits the data exactly, so lanes reach the plot area's edges where the
        # Plot navigator's full range ends; the lanes' vertical range is set explicitly.
        self.plot_item.getViewBox().setDefaultPadding(0.0)
        self.plot_item.showGrid(x=True, y=False, alpha=0.2)
        apply_plot_palette(self.plot_widget)
        self.plot_item.hideButtons()
        self.plot_item.setLabel("bottom", "")
        self.plot_item.getAxis("left").setStyle(showValues=True)
        self._items: list[object] = []
        self._lane_axis_labels: list[QGraphicsSimpleTextItem] = []
        # The legend entries shown; a reload with the same ones leaves the legend alone.
        self._shown_legend_entries: list | None = None
        self._timeline_data: list[dict[str, object]] = []
        self.data_time_range: tuple[float, float] | None = None
        # `_timeline_data` contains only H5 series that loaded successfully.
        # Keep the corresponding original entry index with it: a legend row,
        # colour and left-axis lane must never be addressed by the compacted
        # list position.
        self._timeline_entries: list[tuple[int, Path, str]] = []
        self._visible_time_range: tuple[float, float] | None = None
        self._window_reload_in_progress = False
        self._visible_reload_timer = QTimer(self)
        self._visible_reload_timer.setSingleShot(True)
        self._visible_reload_timer.setInterval(90)
        self._visible_reload_timer.timeout.connect(self._reload_visible_window)
        self._visible_request_id = 0
        self._visible_request: tuple[tuple[float, float], tuple[tuple[Path, str], ...]] | None = None
        # A queued series or data range load: its series and whether it keeps the current view.
        # A view change by the user or a workspace while it loads makes it keep the view.
        self._explicit_load: tuple[tuple[tuple[Path, str], ...], bool] | None = None
        # None loads series in the background while an event loop runs and
        # synchronously otherwise (scripts and scripted tests).
        self.background_loads: bool | None = None
        self._visible_loads = VisibleDataLoadCoordinator(self)
        self._visible_loads.loaded.connect(self._show_loaded_visible_window)
        # `_colors` follows `series` order; `_series_colors` is the source of truth.
        self._series_colors: dict[tuple[Path, str], str] = {}
        self._colors: list[str] = []
        self.time_axis_metadata: TimeAxisMetadata | None = None
        self._marker_loads = MarkerValueLoads(self, self._show_loaded_marker_data)
        self.value_boxes = ValueBoxLayer(self.plot_widget)
        self.plot_widget.marker_cleared.connect(lambda: self.hide_marker_data(FIRST_MARKER))
        self.plot_widget.second_marker_cleared.connect(lambda: self.hide_marker_data(SECOND_MARKER))
        self.selection_info = SelectionDurationInfo(self.plot_widget, self.value_boxes, lambda: self.time_axis_metadata)
        self.plot_widget.marker_snap_resolver = self.nearest_time
        self.plot_item.vb.sigResized.connect(self._update_lane_vertical_scale)
        self.plot_item.vb.sigYRangeChanged.connect(self._update_lane_heights)
        self.plot_item.vb.sigRangeChanged.connect(lambda *_args: self._update_lane_axis_labels())
        self.plot_item.vb.sigXRangeChanged.connect(lambda *_args: self._schedule_visible_reload())
        self.legend = EventStateLegend(self.plot_widget, self)
        self.content_layout.addWidget(self.plot_widget, stretch=1)
        # Each lane's bars, with its series name, for the name shown while the pointer rests on one.
        self._lane_hover_items: list[tuple[object, str]] = []
        # Each lane's bars and its series' source, for selecting a lane with a click.
        self._lane_bars: list[tuple[object, tuple | None]] = []
        self.plot_widget.plot_left_clicked.connect(self._select_series_at)
        install_series_name_hover(self.plot_widget.viewport(),
                                  lambda position: name_at_canvas_point(self.plot_widget, position, self._lane_hover_items))
        self.on_series_changed()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self.legend.reposition()
        QTimer.singleShot(0, self, self._update_lane_vertical_scale)

    def plot_view_box(self):  # type: ignore[no-untyped-def]
        return self.plot_item.getViewBox()

    def required_left_axis_width(self) -> int:
        """Return enough graphics-layout space for all timeline lane names."""
        if not self._timeline_data:
            return 0
        metrics = QFontMetrics(self.plot_item.getAxis("left").font())
        widest_label = max(metrics.horizontalAdvance(str(data["name"])) for data in self._timeline_data)
        # Text padding, the tick, and a comfortable gap before the plot's
        # vertical y-axis line.
        return widest_label + 30

    def set_time_axis_display_metadata(self, metadata: TimeAxisMetadata) -> None:
        display = _display_time_axis_metadata(metadata)
        self.plot_widget.configure_time_axis(
            raw_origin=display.raw_origin,
            seconds_per_unit=display.seconds_per_unit,
            log_start_unix_seconds=display.log_start_unix_seconds,
            is_unix_time=display.is_unix_time,
        )

    def restore_own_time_axis_metadata(self) -> None:
        if self.time_axis_metadata is not None:
            self.set_time_axis_display_metadata(self.time_axis_metadata)

    def set_vertical_marker(self, x_value: float, index: int = FIRST_MARKER) -> None:
        # Outside the lanes' data the marker is not drawn on this card.
        if self._contains_time(x_value):
            super().set_vertical_marker(x_value, index)
        else:
            self.clear_vertical_marker(index)

    def set_data_time_range(self, time_range: tuple[float, float] | None) -> None:
        """Filter timeline data while preserving the user's current ViewBox."""
        view_range = tuple(self.plot_item.vb.viewRange()[0])
        self.data_time_range = tuple(sorted(time_range)) if time_range is not None else None
        self._visible_time_range = None
        if self._load_series(keep_view=True):
            self.plot_item.vb.setXRange(*view_range, padding=0)

    def apply_theme(self) -> None:
        apply_plot_palette(self.plot_widget)
        self.value_boxes.apply_style()
        for label in self._lane_axis_labels:
            label.setBrush(self.plot_widget.palette().color(QPalette.ColorRole.Text))

    def nearest_time(self, x_value: float) -> float:
        times = [np.asarray(item["time"], dtype=float) for item in self._timeline_data]
        times = [item for item in times if len(item)]
        if not times:
            return x_value
        values = np.concatenate(times)
        return float(values[np.argmin(np.abs(values - x_value))])

    def show_marker_data(self, x_value: float, index: int = FIRST_MARKER) -> None:
        if not self._contains_time(x_value):
            self.hide_marker_data(index)
            return
        # State rectangles can intentionally extend past their last recorded
        # transition. Show the nearest state/event for every assigned series
        # instead of omitting a lane whose raw samples stop earlier.
        self._marker_loads.request(index, x_value, list(self.series), allow_outside_series=True)

    def hide_marker_data(self, index: int | None = None) -> None:
        self._marker_loads.invalidate(index)
        for marker in (FIRST_MARKER, SECOND_MARKER) if index is None else (index,):
            self.value_boxes.clear(marker)

    def dispose(self) -> None:
        self._visible_reload_timer.stop()
        self._marker_loads.shutdown()
        self._visible_loads.shutdown()

    def set_series_color(self, index: int, color: str) -> None:
        if 0 <= index < len(self.series):
            self._series_colors[self.series[index]] = color
            self.on_series_changed()

    def workspace_state(self) -> dict:
        return {**super().workspace_state(), "colors": list(self._colors),
                "view": view_range_state(self.plot_view_box())}

    def restore_workspace_state(self, state: dict) -> None:
        entries = [state_entry(entry) for entry in state.get("series", [])]
        self._series_colors.update(zip(entries, (str(color) for color in state.get("colors", []))))
        super().restore_workspace_state(state)
        # Lanes set the vertical range; only the time range is restored.
        apply_view_range_state(self.plot_view_box(), state.get("view"), y=False)

    def _sync_colors(self) -> None:
        self._colors = assign_series_colors(self.series, self._series_colors, EVENT_COLORS)
        self._series_colors = dict(zip(self.series, self._colors))

    def _contains_time(self, x_value: float) -> bool:
        # Outside the Plot navigator trim the card shows no data, so no values either (as Line).
        if self.data_time_range is not None and not self.data_time_range[0] <= x_value <= self.data_time_range[1]:
            return False
        times = [np.asarray(item["time"], dtype=float) for item in self._timeline_data]
        times = [item for item in times if len(item)]
        if not times:
            return False
        return min(float(item[0]) for item in times) <= x_value <= max(float(item[-1]) for item in times)

    def on_series_changed(self) -> None:
        self._load_series(keep_view=False)

    def _uses_background_loads(self) -> bool:
        if self.background_loads is not None:
            return self.background_loads
        return QThread.currentThread().loopLevel() > 0

    def _load_series(self, *, keep_view: bool) -> bool:
        """Reload every series; True when the timeline was drawn at once, False when queued."""
        # This load supersedes a pending background window load.
        self._visible_request_id = self._visible_loads.invalidate()
        self._visible_request = None
        load_range = self._effective_load_range()
        if self.series and self._uses_background_loads():
            # A replaced load that kept the view still does.
            keep_view = keep_view or (self._explicit_load is not None and self._explicit_load[1])
            self._explicit_load = (tuple(self.series), keep_view)
            self._visible_request_id = self._visible_loads.request([
                partial(load_event_state_series, h5_path, series_name, time_range=load_range)
                for h5_path, series_name in self.series
            ])
            return False
        self._explicit_load = None
        loaded: list[object] = []
        for h5_path, series_name in self.series:
            try:
                loaded.append(load_event_state_series(h5_path, series_name, time_range=load_range))
            except (OSError, KeyError, ValueError) as error:
                loaded.append(error)
        self._render_timeline(loaded)
        return True

    def _render_timeline(self, loaded: list[object]) -> None:
        """Draw one lane per successfully loaded series; `loaded` follows `series`."""
        if self.selected_series is not None and self.selected_series not in self.series:
            self._selected_series = None
        self._clear_items()
        names = [str(data["name"]) for data in loaded if isinstance(data, dict)]
        if names != [label.text() for label in self._lane_axis_labels]:
            self._clear_lane_axis_labels()
        self._timeline_data = []
        self._timeline_entries = []
        self._sync_colors()
        for source_index, ((h5_path, series_name), data) in enumerate(zip(self.series, loaded)):
            if isinstance(data, dict):
                self._timeline_data.append(data)
                self._timeline_entries.append((source_index, h5_path, series_name))
        if not self._timeline_data:
            self.plot_item.getAxis("left").setTicks([])
            self.legend.hide()
            self._shown_legend_entries = None
            return
        metadata = self._timeline_data[0]["metadata"]
        if isinstance(metadata, TimeAxisMetadata):
            self.time_axis_metadata = metadata
            self.set_time_axis_display_metadata(metadata)
        for lane, (data, entry) in enumerate(zip(self._timeline_data, self._timeline_entries)):
            source_index, _h5_path, _series_name = entry
            self._draw_lane(lane, data, self._colors[source_index], source_index)
        # AxisItem may cull one of several explicit tick texts. Retain its
        # ticks, but draw lane names ourselves so every series is labelled.
        self.plot_item.getAxis("left").setTicks([[
            (lane, "") for lane, _data in enumerate(self._timeline_data)
        ]])
        # PyQtGraph's deferred Y auto-range may otherwise replace the lane
        # range after the second series is added.  The second tick then lies
        # outside the rendered axis, even though it is present in _tickLevels.
        self.plot_item.enableAutoRange(axis=pg.ViewBox.YAxis, enable=False)
        self._update_lane_vertical_scale()
        self._create_lane_axis_labels()
        QTimer.singleShot(0, self, self._update_lane_vertical_scale)
        QTimer.singleShot(0, self, self._update_lane_axis_labels)
        self.plot_item.enableAutoRange(axis=pg.ViewBox.XAxis, enable=True)
        entries = self._legend_entries()
        if entries != self._shown_legend_entries or not self.legend.isVisible():
            # A pan or zoom reload has the same entries: the legend, and a drag's drop
            # hint in it, stay as they are.
            self._shown_legend_entries = entries
            self.legend.set_entries(entries)
            self.show_selected_series_in_legend()

    def _effective_load_range(
        self, visible_range: tuple[float, float] | None = None
    ) -> tuple[float, float] | None:
        visible_range = self._visible_time_range if visible_range is None else visible_range
        if visible_range is None:
            return self.data_time_range
        if self.data_time_range is None:
            return visible_range
        lower = max(self.data_time_range[0], visible_range[0])
        upper = min(self.data_time_range[1], visible_range[1])
        return (lower, upper) if lower <= upper else self.data_time_range

    def _schedule_visible_reload(self) -> None:
        if self._window_reload_in_progress:
            return
        if self._explicit_load is not None:
            # The view was set by the user or a workspace; keep it when the load arrives.
            self._explicit_load = (self._explicit_load[0], True)
        if self._timeline_data:
            self._visible_reload_timer.start()

    def _reload_visible_window(self) -> None:
        """Debounce pan/zoom and repopulate only the visible H5 time window."""
        try:
            lower, upper = sorted(float(value) for value in self.plot_item.vb.viewRange()[0])
        except RuntimeError:
            return
        if not np.isfinite(lower) or not np.isfinite(upper) or upper <= lower:
            return
        current_bounds = [
            (float(item["h5_start"]), float(item["h5_end"]))
            for item in self._timeline_data
            if item.get("h5_start") is not None and item.get("h5_end") is not None
        ]
        if current_bounds:
            full_lower = min(bound[0] for bound in current_bounds)
            full_upper = max(bound[1] for bound in current_bounds)
            # The initial auto-range already represents the complete series;
            # avoid immediately reopening every H5 file for that same span.
            if self._visible_time_range is None and lower <= full_lower and upper >= full_upper:
                return
        requested = (lower, upper)
        if self._visible_time_range == requested or (
            self._visible_request is not None and self._visible_request[0] == requested
        ):
            return
        load_range = self._effective_load_range(requested)
        # This window load replaces a queued series load; it draws every series too.
        self._explicit_load = None
        self._visible_request = (requested, tuple(self.series))
        self._visible_request_id = self._visible_loads.request([
            partial(load_event_state_series, h5_path, series_name, time_range=load_range)
            for h5_path, series_name in self.series
        ])

    def _show_loaded_visible_window(self, request_id: int, loaded: list[object]) -> None:
        """Apply a background series or window load; a window load keeps the user's current view."""
        explicit = self._explicit_load
        if explicit is not None:
            if request_id != self._visible_request_id or explicit[0] != tuple(self.series) or len(loaded) != len(self.series):
                return
            self._explicit_load = None
            if explicit[1]:
                self._render_keeping_view(loaded)
            else:
                self._render_timeline(loaded)
            return
        request = self._visible_request
        if (
            request_id != self._visible_request_id
            or request is None
            or request[1] != tuple(self.series)
            or len(loaded) != len(self.series)
        ):
            return
        self._visible_request = None
        self._visible_time_range = request[0]
        self._render_keeping_view(loaded)

    def _render_keeping_view(self, loaded: list[object]) -> None:
        try:
            view_range = tuple(self.plot_item.vb.viewRange()[0])
        except RuntimeError:
            return
        self._window_reload_in_progress = True
        try:
            self._render_timeline(loaded)
            self.plot_item.vb.setXRange(*view_range, padding=0)
            self.plot_item.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
        finally:
            self._window_reload_in_progress = False

    def _draw_lane(self, lane, data, color, series_index):
        source = self.series[series_index] if 0 <= series_index < len(self.series) else None
        items = render_event_state_lane(self.plot_item, lane, data, color,
                                        series_line_width(selected=source is not None and source == self.selected_series))
        self._items.extend(items)
        name = series_legend_name(*source) if source is not None else ""
        bars = [item for item in items if isinstance(item, pg.BarGraphItem)]
        self._lane_hover_items.extend((item, name) for item in bars)
        self._lane_bars.extend((item, source) for item in bars)

    def _select_series_at(self, position) -> None:  # type: ignore[no-untyped-def]
        """A left click on a lane selects its series; elsewhere none."""
        self.select_series(item_at_canvas_point(self.plot_widget, position.toPoint(), self._lane_bars,
                                                SELECT_TOLERANCE_PIXELS))

    def apply_series_widths(self) -> None:
        """Every lane's outline at the series width, the selected lane's wider."""
        for bar, source in self._lane_bars:
            pen = pg.mkPen(bar.opts["pen"])
            pen.setWidth(series_line_width(selected=source is not None and source == self.selected_series))
            bar.setOpts(pen=pen)

    def selected_legend_rows(self):  # type: ignore[no-untyped-def]
        return tuple(index for index, source in enumerate(self.series) if source == self.selected_series), None

    def _legend_entries(self) -> list[tuple[str, str, int, str | None]]:
        entries: list[tuple[str, str, int, str | None]] = []
        for _data, (source_index, path, name) in zip(self._timeline_data, self._timeline_entries):
            entries.append((series_legend_name(path, name), self._colors[source_index], source_index, None))
        return entries

    def _clear_items(self) -> None:
        for item in self._items:
            self.plot_item.removeItem(item)
        self._items.clear()
        self._lane_hover_items = []
        self._lane_bars = []

    def _clear_lane_axis_labels(self) -> None:
        for label in self._lane_axis_labels:
            label.setParentItem(None)
            if label.scene() is not None:
                label.scene().removeItem(label)
        self._lane_axis_labels.clear()

    def _create_lane_axis_labels(self) -> None:
        if self._lane_axis_labels:
            # The same lanes as before (a pan or zoom reload): only place them again.
            self._update_lane_axis_labels()
            return
        axis = self.plot_item.getAxis("left")
        text_color = self.plot_widget.palette().color(QPalette.ColorRole.Text)
        for data in self._timeline_data:
            label = QGraphicsSimpleTextItem(str(data["name"]), axis)
            label.setBrush(text_color)
            self._lane_axis_labels.append(label)
        self._update_lane_axis_labels()

    def _update_lane_axis_labels(self) -> None:
        try:
            if not self._lane_axis_labels:
                return
            axis = self.plot_item.getAxis("left")
            bounds = axis.boundingRect()
            if bounds.height() <= 0:
                return
            for lane, label in enumerate(self._lane_axis_labels):
                # Use the ViewBox's real mapping. AxisItem can retain a stale
                # range during a graphics-layout pass, which would visibly shift
                # a title above or below its lane.
                scene_position = self.plot_item.vb.mapViewToScene(pg.Point(0, lane))
                y_position = axis.mapFromScene(scene_position).y()
                text_bounds = label.boundingRect()
                label.setPos(
                    max(0.0, bounds.width() - text_bounds.width() - 14.0),
                    y_position - text_bounds.height() / 2.0,
                )
        except RuntimeError:
            # A queued graphics-layout update can run after its plot card has
            # been removed and PyQtGraph has destroyed the native ViewBox.
            return

    def _update_lane_vertical_scale(self) -> None:
        """Keep lanes at a text-sized pixel height and leave extra space black."""
        try:
            lane_count = len(self._timeline_data)
            height = max(1.0, float(self.plot_item.vb.height()))
            lane_pixels = event_state_lane_height(self.plot_item) / (2 * EVENT_STATE_HALF_HEIGHT)
            visible_span = max(float(lane_count), height / lane_pixels)
            center = (lane_count - 1) / 2 if lane_count else 0.0
            self.plot_item.enableAutoRange(axis=pg.ViewBox.YAxis, enable=False)
            self.plot_item.vb.setYRange(center - visible_span / 2, center + visible_span / 2, padding=0)
            self._update_lane_axis_labels()
            self._update_lane_heights()
        except RuntimeError:
            # `QTimer.singleShot(0, ...)` and ViewBox resize signals can
            # arrive after a removed plot's C++ ViewBox has been deleted.
            return

    def _update_lane_heights(self, *_args):
        update_event_state_lane_heights(self.plot_item, self._items)

    def _show_loaded_marker_data(self, index: int, x_value: float, samples: list[MarkerSample]) -> None:
        # Each box sits on its series' lane.
        lanes = {source_index: lane for lane, (source_index, _path, _name) in enumerate(self._timeline_entries)}
        items = []
        for sample, entry in zip(samples, marker_entries_from_samples(samples, self._colors)):
            series_index = sample.series_index if sample.series_index is not None else 0
            if series_index < len(self.series):
                items.append(ValueBoxItem(self.series[series_index], entry, lanes.get(series_index)))
        self.value_boxes.set_items(index, x_value, items)


def _looks_like_binary_state(values: np.ndarray) -> bool:
    # One pass, no sort: this runs for every lane on every pan and zoom.
    finite = values[np.isfinite(values)]
    return bool(len(finite)) and bool(np.all((finite == 0.0) | (finite == 1.0)))


class EventStateLegend(PlotLegendWidget):
    def __init__(self, plot_widget: QWidget, plot: EventStatePlotWidget) -> None:
        super().__init__(plot_widget)
        self.plot = plot
        self.color_requested.connect(self._choose_color_for_key)
        self.remove_requested.connect(self.plot.remove_series_at)
        self.move_requested.connect(self.plot.remove_series_at)

    def set_entries(self, entries: list[tuple[str, str, int, str | None]]) -> None:
        self._state_by_series_index = {series_index: state for _text, _color, series_index, state in entries}
        super().set_entries([
            LegendEntry(series_index, text, color, self.plot.series[series_index])
            for text, color, series_index, _state in entries
            if 0 <= series_index < len(self.plot.series)
        ])

    def _choose_color_for_key(self, index: int) -> None:
        self.choose_color(index, getattr(self, "_state_by_series_index", {}).get(index))

    def choose_color(self, index: int, state: str | None) -> None:
        if not 0 <= index < len(self.plot._colors):
            return
        color = QColorDialog.getColor(QColor(self.plot._colors[index]), self, "Series color")
        if color.isValid():
            self.plot.set_series_color(index, color.name())
