"""Reusable PyQtGraph widget for displaying one cached HDF5 series."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path

import h5py
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from src.data.h5_series import ( # noqa: F401 - re-exported for existing callers
    TimeAxisMetadata,
    _attribute_text,
    _canonical_time_values,
    _canonical_to_native_range,
    _canonical_to_native_value,
    _h5_searchsorted,
    _series_group_for_navigator_name,
    _time_axis_metadata_from_group,
    _time_unit_seconds,
    _visible_sample_bounds,
)
from src.app.settings import DEFAULT_PLOT_SETTINGS, load_plot_settings
from src.gui.plots.base import SERIES_COLORS, assign_series_colors, series_legend_name
from src.gui.plots.interactive import InteractivePlotWidget
from src.gui.plots.diagnostic_areas import CLIPPING, SAMPLE_LOSS, DiagnosticAreas
from src.gui.plots.selection_info import SelectionDurationInfo
from src.gui.plots.series_hover import install_series_name_hover, item_at_canvas_point, name_at_canvas_point
from src.gui.plots.series_style import SELECT_TOLERANCE_PIXELS, series_line_width
from src.gui.plots.theme import apply_plot_palette
from src.gui.plots.marker_info import MarkerInfoEntry, marker_entries_from_samples
from src.gui.plots.markers import FIRST_MARKER, SECOND_MARKER
from src.gui.plots.value_boxes import ValueBoxItem, ValueBoxLayer


MAX_INITIAL_PLOT_POINTS = 12_000
MAX_MARKER_POINTS = 2_000
DEFAULT_SERIES_COLORS = SERIES_COLORS
@dataclass(frozen=True)
class PlotSeriesData:
    x: np.ndarray
    y: np.ndarray
    title: str
    unit: str
    lod_level: int | None = None


def _display_time_axis_metadata(metadata: TimeAxisMetadata) -> TimeAxisMetadata:
    """Describe the axis after its coordinates have been converted to Unix seconds."""
    if metadata.is_unix_time or metadata.log_start_unix_seconds is not None:
        # Elapsed ticks count from the log start (or, without one, from the
        # series' first Unix sample), not from the Unix epoch.
        origin = metadata.log_start_unix_seconds
        if origin is None:
            origin = metadata.raw_origin * metadata.seconds_per_unit
        return TimeAxisMetadata(
            raw_origin=origin,
            seconds_per_unit=1.0,
            log_start_unix_seconds=metadata.log_start_unix_seconds,
            is_unix_time=True,
        )
    return metadata


@dataclass
class AdditionalSeries:
    h5_path: Path
    series_name: str
    color: str
    curve: pg.PlotCurveItem
    markers: pg.ScatterPlotItem
    data: PlotSeriesData | None = None


@dataclass(frozen=True)
class MarkerSample:
    """All HDF5-backed data displayed for one series at a vertical marker."""

    series_name: str
    sample_time: float
    log_elapsed_ms: float
    date_time: str
    device_time_unit: str
    converted_value: float
    original_value: float | None
    unit: str
    # Position in the requested entries; samples can be missing for some series.
    series_index: int | None = None


@dataclass(frozen=True)
class SelectionStatistics:
    """Raw-value statistics for one series inside a selected time interval."""

    series_index: int
    series_name: str
    unit: str
    sample_count: int
    minimum: float
    maximum: float
    average: float
    median: float
    histogram_counts: tuple[int, ...]
    histogram_edges: tuple[float, ...]


class MarkerDataLoader(QThread):
    """Read marker samples off the GUI thread; HDF5 access can be slow on network disks."""

    loaded = Signal(int, float, object)

    def __init__(
        self,
        request_id: int,
        x_value: float,
        entries: list[tuple[Path, str]],
        parent: QWidget,
        *,
        allow_outside_series: bool = False,
        x_offsets: list[float] | None = None,
    ) -> None:
        super().__init__(parent)
        self.request_id = request_id
        self.x_value = x_value
        self.entries = entries
        self.allow_outside_series = allow_outside_series
        # Seconds each series is shown later than its data, as in Multi-series cards.
        self.x_offsets = x_offsets

    def run(self) -> None:
        samples: list[MarkerSample] = []
        for series_index, (h5_path, series_name) in enumerate(self.entries):
            if self.isInterruptionRequested():
                break
            try:
                samples.append(
                    load_marker_sample(
                        h5_path,
                        series_name,
                        self.x_value - (self.x_offsets[series_index] if self.x_offsets else 0.0),
                        canonical_x=True,
                        allow_outside_series=self.allow_outside_series,
                        series_index=series_index,
                    )
                )
            except (OSError, KeyError, ValueError):
                continue
        self.loaded.emit(self.request_id, self.x_value, samples)


class _LatestRequestCoordinator(QObject):
    """Run one background loader per plot at a time, keeping only the newest queued request.

    Subclasses define a ``loaded`` signal and create the QThread for a request.
    Results of superseded requests are still emitted with their request ID; the
    receiving plot compares that ID with its newest request.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._next_request_id = 0
        self._active_loader: QThread | None = None
        self._pending: tuple | None = None

    def _queue(self, *arguments) -> int:  # type: ignore[no-untyped-def]
        self._next_request_id += 1
        request = (self._next_request_id, *arguments)
        if self._active_loader is None:
            self._start(request)
        else:
            self._pending = request
        return self._next_request_id

    def invalidate(self) -> int:
        """Discard a queued request and make an active result obsolete."""
        self._next_request_id += 1
        self._pending = None
        return self._next_request_id

    def shutdown(self) -> None:
        """Prevent a plot's destruction from deleting a running QThread."""
        self.invalidate()
        loader = self._active_loader
        if loader is None:
            return
        loader.requestInterruption()
        application = QCoreApplication.instance()
        if application is not None:
            loader.setParent(application)
        self._active_loader = None

    def _create_loader(self, request: tuple, parent: QWidget):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def _start(self, request: tuple) -> None:
        parent = self.parent()
        if not isinstance(parent, QWidget):
            return
        loader = self._create_loader(request, parent)
        self._active_loader = loader
        loader.loaded.connect(self._on_loaded)
        loader.finished.connect(self._on_finished)
        loader.finished.connect(loader.deleteLater)
        loader.start()

    def _on_loaded(self, *result) -> None:  # type: ignore[no-untyped-def]
        self.loaded.emit(*result)  # type: ignore[attr-defined]

    def _on_finished(self) -> None:
        self._active_loader = None
        pending = self._pending
        self._pending = None
        if pending is not None:
            self._start(pending)


class MarkerDataLoadCoordinator(_LatestRequestCoordinator):
    """Serialize marker reads for one plot, retaining only its newest request."""

    loaded = Signal(int, float, object)

    def request(
        self,
        x_value: float,
        entries: list[tuple[Path, str]],
        *,
        allow_outside_series: bool = False,
        x_offsets: list[float] | None = None,
    ) -> int:
        """Queue a read, keeping only the newest request while one is active."""
        return self._queue(x_value, entries, allow_outside_series, x_offsets)

    def _create_loader(self, request: tuple, parent: QWidget) -> MarkerDataLoader:
        request_id, x_value, entries, allow_outside_series, *rest = request
        x_offsets = rest[0] if rest else None
        return MarkerDataLoader(
            request_id, x_value, entries, parent, allow_outside_series=allow_outside_series, x_offsets=x_offsets
        )


class MarkerValueLoads:
    """The two markers' value reads for one card.

    ``loaded(index, x_value, samples)`` is called only for each marker's newest read; a newer
    request or ``invalidate`` makes earlier reads obsolete.
    """

    def __init__(self, owner: QWidget, loaded) -> None:  # type: ignore[no-untyped-def]
        self._request_ids = {FIRST_MARKER: 0, SECOND_MARKER: 0}
        self._loads = {index: MarkerDataLoadCoordinator(owner) for index in (FIRST_MARKER, SECOND_MARKER)}
        for index, loads in self._loads.items():
            loads.loaded.connect(lambda request_id, x_value, samples, index=index: (
                request_id == self._request_ids[index] and loaded(index, x_value, samples)))

    def request(self, index: int, x_value: float, entries: list[tuple[Path, str]], **options) -> None:  # type: ignore[no-untyped-def]
        self._request_ids[index] = self._loads[index].request(x_value, entries, **options)

    def invalidate(self, index: int | None = None) -> None:
        for marker in (FIRST_MARKER, SECOND_MARKER) if index is None else (index,):
            self._request_ids[marker] = self._loads[marker].invalidate()

    def shutdown(self) -> None:
        for loads in self._loads.values():
            loads.shutdown()


class SelectionStatisticsLoader(QThread):
    """Read complete selected ranges off the GUI thread for exact statistics."""

    loaded = Signal(int, float, float, object)

    def __init__(
        self,
        request_id: int,
        left: float,
        right: float,
        entries: list[tuple[Path, str]],
        parent: QWidget,
        *,
        quantity: str = "value",
        x_offsets: list[float] | None = None,
    ) -> None:
        super().__init__(parent)
        self.request_id = request_id
        self.quantity = quantity
        self.x_offsets = x_offsets
        self.left = left
        self.right = right
        self.entries = entries

    def run(self) -> None:
        statistics: list[SelectionStatistics] = []
        for series_index, (h5_path, series_name) in enumerate(self.entries):
            if self.isInterruptionRequested():
                break
            shift = self.x_offsets[series_index] if self.x_offsets else 0.0
            try:
                statistics.append(
                    load_selection_statistics(
                        h5_path, series_name, self.left - shift, self.right - shift, series_index=series_index,
                        quantity=self.quantity,
                    )
                )
            except (OSError, KeyError, ValueError):
                continue
        self.loaded.emit(self.request_id, self.left, self.right, statistics)


class SelectionStatisticsLoadCoordinator(_LatestRequestCoordinator):
    """Serialize range-statistics reads; a moved selection replaces its queued read."""

    loaded = Signal(int, float, float, object)

    def request(self, left: float, right: float, entries: list[tuple[Path, str]],
                x_offsets: list[float] | None = None) -> int:
        return self._queue(left, right, entries, x_offsets)

    def _create_loader(self, request: tuple, parent: QWidget) -> SelectionStatisticsLoader:
        request_id, left, right, entries, *rest = request
        x_offsets = rest[0] if rest else None
        quantity = getattr(parent, "statistics_quantity", "value")
        options = {} if quantity == "value" else {"quantity": quantity}
        if x_offsets:
            options["x_offsets"] = x_offsets
        return SelectionStatisticsLoader(request_id, left, right, entries, parent, **options)


class BackgroundCallsLoader(QThread):
    """Run thread-safe, zero-argument HDF5 reads; failures are returned, not raised."""

    loaded = Signal(int, object)

    def __init__(self, request_id: int, calls: list, parent: QWidget) -> None:  # type: ignore[type-arg]
        super().__init__(parent)
        self.request_id = request_id
        self.calls = calls

    def run(self) -> None:
        results: list[object] = []
        for call in self.calls:
            if self.isInterruptionRequested():
                return
            try:
                results.append(call())
            except Exception as error:  # noqa: BLE001 - reported to the GUI thread
                results.append(error)
        self.loaded.emit(self.request_id, results)


class VisibleDataLoadCoordinator(_LatestRequestCoordinator):
    """Load a plot's visible window off the GUI thread, keeping only the latest pan/zoom."""

    loaded = Signal(int, object)

    def request(self, calls: list) -> int:  # type: ignore[type-arg]
        return self._queue(list(calls))

    def _create_loader(self, request: tuple, parent: QWidget) -> BackgroundCallsLoader:
        request_id, calls = request
        return BackgroundCallsLoader(request_id, calls, parent)


def load_selection_statistics(
    h5_path: str | Path, series_name: str, left: float, right: float, *, series_index: int = 0,
    quantity: str = "value",
) -> SelectionStatistics:
    """Calculate exact raw-sample statistics for a canonical time range.

    ``quantity="interval"`` describes the intervals between consecutive samples, in seconds.
    """
    with h5py.File(h5_path, "r") as h5_file:
        group = _series_group_for_navigator_name(h5_file, series_name)
        time_dataset_name = _attribute_text(group.attrs.get("time_dataset", "time_unix"))
        time_dataset = group[time_dataset_name]
        metadata = _time_axis_metadata_from_group(h5_file, group)
        native_range = _canonical_to_native_range((left, right), metadata)
        start, stop = _visible_sample_bounds(time_dataset, native_range)
        unit = _attribute_text(group.attrs.get("unit", ""))
        if quantity == "interval":
            # Include the interval that ends at the first selected sample.
            times = np.asarray(time_dataset[max(0, start - 1):stop], dtype=np.float64)
            values = np.diff(times) * metadata.seconds_per_unit
            unit = "s"
        else:
            values = np.asarray(group["value"][start:stop], dtype=np.float64)
        values = values[np.isfinite(values)]
        if not len(values):
            raise ValueError("Selection contains no finite samples")
        counts, edges = np.histogram(values, bins=50)
        return SelectionStatistics(
            series_index=series_index,
            series_name=_attribute_text(group.attrs.get("navigator_name", series_name)),
            unit=unit,
            sample_count=int(len(values)),
            minimum=float(np.min(values)),
            maximum=float(np.max(values)),
            average=float(np.mean(values)),
            median=float(np.median(values)),
            histogram_counts=tuple(int(value) for value in counts),
            histogram_edges=tuple(float(value) for value in edges),
        )


def _selection_statistics_entry(statistics: SelectionStatistics, color: str):
    """Format a compact, expandable selection summary for the shared popup."""
    unit = f" {statistics.unit}" if statistics.unit else ""
    peak_count = max(statistics.histogram_counts, default=0)
    histogram_levels = "▁▂▃▄▅▆▇█"
    histogram = "".join(
        histogram_levels[0]
        if peak_count <= 0
        else histogram_levels[round((len(histogram_levels) - 1) * count / peak_count)]
        for count in statistics.histogram_counts
    )
    return MarkerInfoEntry(
        statistics.series_name,
        color,
        f"avg {statistics.average:.9g}{unit}",
        (
            f"Samples: {statistics.sample_count:,}",
            f"Min: {statistics.minimum:.9g}{unit}",
            f"Max: {statistics.maximum:.9g}{unit}",
            f"Average: {statistics.average:.9g}{unit}",
            f"Median: {statistics.median:.9g}{unit}",
            (
                f"Histogram (50 bins; {statistics.histogram_edges[0]:.6g}"
                f" to {statistics.histogram_edges[-1]:.6g}{unit}):\n{histogram}"
            ),
        ),
    )


def load_plot_series(
    h5_path: str | Path,
    series_name: str,
    *,
    maximum_points: int = MAX_INITIAL_PLOT_POINTS,
    x_range: tuple[float, float] | None = None,
) -> PlotSeriesData:
    """Load the finest raw or LOD data fitting the visible plotting budget."""

    with h5py.File(h5_path, "r") as h5_file:
        group = _series_group_for_navigator_name(h5_file, series_name)
        time_dataset_name = _attribute_text(group.attrs.get("time_dataset", "time_unix"))
        title = _attribute_text(group.attrs.get("navigator_name", series_name))
        unit = _attribute_text(group.attrs.get("unit", ""))
        value_dataset = group["value"]
        time_dataset = group[time_dataset_name]
        metadata = _time_axis_metadata_from_group(h5_file, group)
        native_range = _canonical_to_native_range(x_range, metadata)
        start, stop = _visible_sample_bounds(time_dataset, native_range)
        visible_count = max(0, stop - start)

        lod_level = _lod_level_for_point_budget(
            group,
            maximum_points,
            visible_count=visible_count,
            sample_count=len(value_dataset),
        )
        if lod_level is None:
            if native_range is not None:
                # Like the LOD path, include one sample beyond each view edge
                # so a zoomed-in curve reaches both sides of the plot.
                start = max(0, start - 1)
                stop = min(len(time_dataset), stop + 1)
            x = _canonical_time_values(np.asarray(time_dataset[start:stop]), metadata)
            y = np.asarray(value_dataset[start:stop])
            return PlotSeriesData(x, y, title, unit)

        lod_group = group["lod"][f"level_{lod_level:03d}"]
        lod_start, lod_stop = _visible_lod_bounds(lod_group, time_dataset_name, native_range)
        lod_x, lod_y = _lod_envelope(lod_group, time_dataset_name, lod_start, lod_stop)
        return PlotSeriesData(_canonical_time_values(lod_x, metadata), lod_y, title, unit, lod_level)


def load_timing_delta_series(
    h5_path: str | Path,
    series_name: str,
    *,
    maximum_points: int = MAX_INITIAL_PLOT_POINTS,
    x_range: tuple[float, float] | None = None,
) -> PlotSeriesData:
    """Load consecutive raw sample intervals for a series' time axis.

    Each rendered point is the actual interval between two adjacent samples.
    For a very wide view, samples are uniformly thinned only for rendering;
    every displayed interval still uses adjacent source timestamps.
    """

    with h5py.File(h5_path, "r") as h5_file:
        group = _series_group_for_navigator_name(h5_file, series_name)
        time_dataset_name = _attribute_text(group.attrs.get("time_dataset", "time_unix"))
        title = _attribute_text(group.attrs.get("navigator_name", series_name))
        time_unit = _attribute_text(group.attrs.get("time_unit", time_dataset_name.removeprefix("time_")))
        time_dataset = group[time_dataset_name]
        metadata = _time_axis_metadata_from_group(h5_file, group)
        native_range = _canonical_to_native_range(x_range, metadata)
        start, stop = _visible_sample_bounds(time_dataset, native_range)
        start = max(0, start - 1)
        if stop - start < 2:
            return PlotSeriesData(
                np.empty(0, dtype=np.float64),
                np.empty(0, dtype=np.float64),
                f"{title} timing delta",
                time_unit,
            )

        interval_count = stop - start - 1
        step = max(1, int(np.ceil(interval_count / max(1, maximum_points))))
        if step == 1:
            times = np.asarray(time_dataset[start:stop], dtype=np.float64)
            return PlotSeriesData(
                _canonical_time_values(times[1:], metadata),
                np.diff(times),
                f"{title} timing delta",
                time_unit,
            )

        indices = np.arange(start + 1, stop, step, dtype=np.intp)
        current_times = np.asarray(time_dataset[indices], dtype=np.float64)
        previous_times = np.asarray(time_dataset[indices - 1], dtype=np.float64)
        return PlotSeriesData(
            _canonical_time_values(current_times, metadata),
            current_times - previous_times,
            f"{title} timing delta",
            time_unit,
        )


def load_sample_interval_series(
    h5_path: str | Path,
    series_name: str,
    *,
    maximum_points: int = MAX_INITIAL_PLOT_POINTS,
    x_range: tuple[float, float] | None = None,
) -> PlotSeriesData:
    """Load timing-delta intervals converted to seconds, so mixed time units share an axis."""
    data = load_timing_delta_series(h5_path, series_name, maximum_points=maximum_points, x_range=x_range)
    title = data.title.removesuffix(" timing delta")
    return PlotSeriesData(data.x, data.y * _time_unit_seconds(data.unit), f"{title} interval", "s")


def load_plot_value_at_x(h5_path: str | Path, series_name: str, x_value: float) -> tuple[float, float]:
    """Return the original cached sample closest to an X coordinate."""

    with h5py.File(h5_path, "r") as h5_file:
        group = _series_group_for_navigator_name(h5_file, series_name)
        time_dataset_name = _attribute_text(group.attrs.get("time_dataset", "time_unix"))
        times = group[time_dataset_name]
        values = group["value"]
        if not len(times):
            raise ValueError("Series has no samples")
        metadata = _time_axis_metadata_from_group(h5_file, group)
        native_x_value = _canonical_to_native_value(x_value, metadata)
        index = _h5_searchsorted(times, native_x_value, side="left")
        candidates = [candidate for candidate in (index - 1, index) if 0 <= candidate < len(times)]
        nearest_index = min(candidates, key=lambda candidate: abs(float(times[candidate]) - native_x_value))
        return float(_canonical_time_values(np.asarray([times[nearest_index]]), metadata)[0]), float(values[nearest_index])


def load_marker_sample(
    h5_path: str | Path,
    series_name: str,
    x_value: float,
    *,
    canonical_x: bool = False,
    allow_outside_series: bool = False,
    series_index: int | None = None,
) -> MarkerSample:
    """Load the nearest raw sample and its untransformed value for a marker."""

    with h5py.File(h5_path, "r") as h5_file:
        group = _series_group_for_navigator_name(h5_file, series_name)
        time_dataset_name = _attribute_text(group.attrs.get("time_dataset", "time_unix"))
        times = group[time_dataset_name]
        if not len(times):
            raise ValueError("Series has no samples")
        metadata = _time_axis_metadata_from_group(h5_file, group)
        native_x_value = (
            _canonical_to_native_value(x_value, metadata) if canonical_x else float(x_value)
        )
        first_time = float(times[0])
        last_time = float(times[len(times) - 1])
        if (
            not allow_outside_series
            and not min(first_time, last_time) <= native_x_value <= max(first_time, last_time)
        ):
            raise ValueError("Marker time is outside the series sample window")
        index = _h5_searchsorted(times, native_x_value, side="left")
        candidates = [candidate for candidate in (index - 1, index) if 0 <= candidate < len(times)]
        nearest_index = min(candidates, key=lambda candidate: abs(float(times[candidate]) - native_x_value))
        sample_time = float(times[nearest_index])
        time_unit = _attribute_text(
            group.attrs.get("time_unit", time_dataset_name.removeprefix("time_"))
        )
        canonical_time = float(_canonical_time_values(np.asarray([sample_time]), metadata)[0])
        date_time_seconds = canonical_time if (
            metadata.is_unix_time or metadata.log_start_unix_seconds is not None
        ) else None
        if metadata.is_unix_time and metadata.log_start_unix_seconds is not None:
            # A Unix-time series' raw origin is its own first sample. Measure
            # from the common log start so all series of a log agree.
            log_elapsed_ms = (canonical_time - metadata.log_start_unix_seconds) * 1000.0
        else:
            # Keep this in native coordinates; subtracting two large Unix values
            # would introduce avoidable floating-point noise in millisecond data.
            log_elapsed_ms = (sample_time - metadata.raw_origin) * metadata.seconds_per_unit * 1000.0
        original_dataset = group.get("value_raw")
        original_value = (
            float(original_dataset[nearest_index])
            if isinstance(original_dataset, h5py.Dataset)
            else None
        )
        return MarkerSample(
            series_name=_attribute_text(group.attrs.get("navigator_name", series_name)),
            sample_time=sample_time,
            log_elapsed_ms=log_elapsed_ms,
            date_time=(
                datetime.fromtimestamp(date_time_seconds).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                if date_time_seconds is not None
                else "Unavailable (cache has no log start time)"
            ),
            device_time_unit=time_unit or "raw units",
            converted_value=float(group["value"][nearest_index]),
            original_value=original_value,
            unit=_attribute_text(group.attrs.get("unit", "")),
            series_index=series_index,
        )


def load_time_axis_metadata(h5_path: str | Path, series_name: str) -> TimeAxisMetadata:
    """Read the H5 metadata used to place a series on the common time axis."""
    with h5py.File(h5_path, "r") as h5_file:
        group = _series_group_for_navigator_name(h5_file, series_name)
        return _time_axis_metadata_from_group(h5_file, group)


def load_sample_loss_ranges(h5_path: str | Path, series_name: str) -> np.ndarray:
    """Load cached sample-loss ranges on the common real-time axis."""

    ranges = _load_failure_ranges(
        h5_path,
        series_name,
        list_names=("sample_loss_list", "failure_list"),
        range_name="sample_loss",
    )
    return _canonical_time_values(ranges, load_time_axis_metadata(h5_path, series_name))


def load_clipping_ranges(h5_path: str | Path, series_name: str) -> np.ndarray:
    """Load cached clipping ranges on the common real-time axis."""

    ranges = _load_failure_ranges(
        h5_path,
        series_name,
        list_names=("clipping_list",),
        range_name="clipping",
    )
    return _canonical_time_values(ranges, load_time_axis_metadata(h5_path, series_name))


def _load_failure_ranges(
    h5_path: str | Path,
    series_name: str,
    *,
    list_names: tuple[str, ...],
    range_name: str,
) -> np.ndarray:

    with h5py.File(h5_path, "r") as h5_file:
        group = _series_group_for_navigator_name(h5_file, series_name)
        failures = next(
            (
                candidate
                for list_name in list_names
                if isinstance((candidate := group.get(list_name)), h5py.Group)
            ),
            None,
        )
        if not isinstance(failures, h5py.Group):
            return np.empty((0, 2), dtype=np.float64)
        time_dataset_name = _attribute_text(group.attrs.get("time_dataset", "time_unix"))
        suffix = time_dataset_name.removeprefix("time_")
        start_name = f"{range_name}_start_{suffix}" if suffix else f"{range_name}_start"
        stop_name = f"{range_name}_stop_{suffix}" if suffix else f"{range_name}_stop"
        if start_name not in failures or stop_name not in failures:
            return np.empty((0, 2), dtype=np.float64)
        starts = np.asarray(failures[start_name], dtype=np.float64)
        stops = np.asarray(failures[stop_name], dtype=np.float64)
        return np.column_stack((starts, stops))


def _lod_level_for_point_budget(
    group: h5py.Group,
    maximum_points: int,
    *,
    visible_count: int,
    sample_count: int,
) -> int | None:
    if visible_count <= maximum_points:
        return None
    lod_root = group.get("lod")
    if not isinstance(lod_root, h5py.Group):
        return None
    levels: list[tuple[int, int]] = []
    for name, level_group in lod_root.items():
        if not isinstance(level_group, h5py.Group) or not name.startswith("level_"):
            continue
        try:
            level = int(name.removeprefix("level_"))
            bucket_count = int(level_group.attrs["bucket_count"])
        except (KeyError, TypeError, ValueError):
            continue
        levels.append((level, bucket_count))
    for level, bucket_count in sorted(levels):
        estimated_visible_points = max(1, int(np.ceil(bucket_count * visible_count / sample_count))) * 4
        if estimated_visible_points <= maximum_points:
            return level
    return max(levels, default=(None, 0))[0]


def _visible_lod_bounds(
    level_group: h5py.Group,
    time_dataset_name: str,
    x_range: tuple[float, float] | None,
) -> tuple[int, int]:
    bucket_count = len(level_group["value_first"])
    if x_range is None:
        return 0, bucket_count
    suffix = time_dataset_name.removeprefix("time_")
    start_name = f"time_start_{suffix}" if suffix else "time_start"
    end_name = f"time_end_{suffix}" if suffix else "time_end"
    lower, upper = sorted(x_range)
    start = _h5_searchsorted(level_group[end_name], lower, side="left")
    stop = _h5_searchsorted(level_group[start_name], upper, side="right")
    return max(0, start - 1), min(bucket_count, stop + 1)


def _lod_envelope(
    level_group: h5py.Group,
    time_dataset_name: str,
    start: int,
    stop: int,
) -> tuple[np.ndarray, np.ndarray]:
    suffix = time_dataset_name.removeprefix("time_")
    time_names = {
        name: f"time_{name}_{suffix}" if suffix else f"time_{name}"
        for name in ("start", "end", "min", "max")
    }
    section = slice(start, stop)
    start_time = np.asarray(level_group[time_names["start"]][section])
    end_time = np.asarray(level_group[time_names["end"]][section])
    min_time = np.asarray(level_group[time_names["min"]][section])
    max_time = np.asarray(level_group[time_names["max"]][section])
    first_value = np.asarray(level_group["value_first"][section])
    last_value = np.asarray(level_group["value_last"][section])
    min_value = np.asarray(level_group["value_min"][section])
    max_value = np.asarray(level_group["value_max"][section])

    min_before_max = min_time <= max_time
    middle_time_first = np.where(min_before_max, min_time, max_time)
    middle_time_last = np.where(min_before_max, max_time, min_time)
    middle_value_first = np.where(min_before_max, min_value, max_value)
    middle_value_last = np.where(min_before_max, max_value, min_value)
    return (
        np.column_stack((start_time, middle_time_first, middle_time_last, end_time)).ravel(),
        np.column_stack((first_value, middle_value_first, middle_value_last, last_value)).ravel(),
    )


class SeriesPlotWidget(QWidget):
    """A plot panel designed to be embedded in a larger layout later."""

    value_axis_label = "Value"
    # "value" or "interval": what range-selection statistics describe.
    statistics_quantity = "value"
    shows_marker_values = True

    status_changed = Signal(str)
    # An explicit (series or data range) load failed; the previous plot is kept.
    load_failed = Signal(str)
    interactive_plot_class = InteractivePlotWidget
    load_data = staticmethod(load_plot_series)
    # The primary series' plot mode when assigned with `show_series_entries`.
    primary_plot_mode = "data"

    def __init__(
        self, parent: QWidget | None = None, *, settings_path: str | Path | None = None
    ) -> None:
        super().__init__(parent)
        self.settings_path = Path(settings_path) if settings_path is not None else None
        self.plot_widget = self.interactive_plot_class()
        self.plot_item = self.plot_widget.getPlotItem()
        self.plot_item.showGrid(x=True, y=True, alpha=0.2)
        apply_plot_palette(self.plot_widget)
        # Tick labels carry the selected time representation; an axis title
        # would only repeat that information and consume vertical space.
        self.plot_item.setLabel("bottom", "")
        self.primary_color = DEFAULT_SERIES_COLORS[0]
        # The selected series' source, drawn at the selected width.
        self.selected_entry: tuple[Path, str] | None = None
        self.curve = pg.PlotCurveItem(pen=pg.mkPen(self.primary_color, width=series_line_width()))
        self.curve.setZValue(10)
        self.markers = pg.ScatterPlotItem(
            symbol="o",
            size=5,
            pen=pg.mkPen(self.primary_color),
            brush=pg.mkBrush(self.primary_color),
        )
        self.markers.setZValue(11)
        self.plot_item.addItem(self.curve)
        self.plot_item.addItem(self.markers)
        # "auto": lines, and points while few raw samples are in view; "lines" or "points" only.
        self.draw_style = "auto"
        self._series_entries: list[tuple[Path, str]] = []
        self.additional_series: list[AdditionalSeries] = []
        # Sample loss and Clipping areas of every series, by series index (`diagnostic_items`).
        self._diagnostic_items: list[DiagnosticAreas] = []
        self.minimum_diagnostic_width_pixels = 5
        self.show_sample_loss_areas = True
        self.show_clipping_areas = True
        self.reload_plot_settings()
        self.value_boxes = ValueBoxLayer(self.plot_widget)
        # The series name appears while the pointer rests on its line or points.
        install_series_name_hover(self.plot_widget.viewport(), self.series_name_at)
        # The selection's Δt and statistics are in the expanded values boxes.
        self.selection_info = SelectionDurationInfo(
            self.plot_widget, self.value_boxes, lambda: self.time_axis_metadata, connect=False,
        )
        self._statistics: list[SelectionStatistics] = []
        self._marker_x: dict[int, float | None] = {FIRST_MARKER: None, SECOND_MARKER: None}
        self._marker_loads = MarkerValueLoads(self, self._show_loaded_marker_values)
        self._selection_request_id = 0
        self._selection_range: tuple[float, float] | None = None
        self._selection_loads = SelectionStatisticsLoadCoordinator(self)
        self._selection_loads.loaded.connect(self._show_loaded_selection_statistics)
        self._visible_request_id = 0
        self._visible_request_keys: tuple[tuple[object, ...], ...] = ()
        # A queued explicit load keeps its auto range and error reporting when
        # a newer request replaces it; a view change by anyone else drops the auto range.
        self._visible_request_explicit = False
        self._visible_request_auto_range = False
        # None loads explicitly requested data in the background while an event
        # loop runs and synchronously otherwise (scripts and scripted tests).
        self.background_loads: bool | None = None
        self._visible_loads = VisibleDataLoadCoordinator(self)
        self._visible_loads.loaded.connect(self._show_loaded_visible_data)
        # Colors stay attached to their sources when series are inserted,
        # removed or reordered.
        self._entry_colors: dict[tuple[Path, str], str] = {}
        self.h5_path: Path | None = None
        self.series_name = ""
        # Keep the series' native time conversion separately from the axis.
        # A workspace may temporarily use another line plot's conversion when
        # their X views are linked, but this remains the authoritative value
        # to restore when linking is disabled.
        self.time_axis_metadata: TimeAxisMetadata | None = None
        self.plot_mode = "data"
        self._current_data: PlotSeriesData | None = None
        # A Plot Navigator selection is a data boundary, deliberately
        # independent of the ViewBox's zoom/pan range.
        self.data_time_range: tuple[float, float] | None = None
        self._updating_plot = False
        self._view_update_timer = QTimer(self)
        self._view_update_timer.setSingleShot(True)
        # Wait until the user pauses zooming/panning before replacing data.
        # Updating each wheel event causes a range/data feedback loop.
        self._view_update_timer.setInterval(150)
        self._view_update_timer.timeout.connect(self._request_visible_data)
        self.plot_item.getViewBox().sigXRangeChanged.connect(self.schedule_visible_data_update)
        self.plot_widget.marker_cleared.connect(lambda: self.hide_marker_values(FIRST_MARKER))
        self.plot_widget.second_marker_cleared.connect(lambda: self.hide_marker_values(SECOND_MARKER))
        self.plot_widget.selection_changed.connect(self.show_selection_statistics)
        self.plot_widget.selection_cleared.connect(self.hide_selection_statistics)
        self.plot_widget.marker_snap_resolver = self.nearest_visible_x

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot_widget, stretch=1)

    def apply_theme(self) -> None:
        """Refresh PyQtGraph colours after the application palette changes."""
        apply_plot_palette(self.plot_widget)
        self.value_boxes.apply_style()

    def show_series_entries(self, entries: list[tuple[Path, str]]) -> list[PlotSeriesData]:
        """Render all assigned series in one ViewBox, selecting LOD per series."""
        self._clear_additional_series()
        normalized_entries = [(Path(path), str(name)) for path, name in entries]
        self._series_entries = normalized_entries
        if not normalized_entries:
            self.clear_series()
            return []

        colors = assign_series_colors(normalized_entries, self._entry_colors, DEFAULT_SERIES_COLORS)
        self._entry_colors = dict(zip(normalized_entries, colors))
        self._apply_primary_color(colors[0])
        primary_path, primary_name = normalized_entries[0]
        # The visible window of every entry is loaded once, below.
        self._set_primary_series(primary_path, primary_name, self.primary_plot_mode, reload_diagnostics=False)
        for (h5_path, series_name), color in zip(normalized_entries[1:], colors[1:]):
            curve = pg.PlotCurveItem(pen=pg.mkPen(color, width=self._line_width((h5_path, series_name))))
            markers = pg.ScatterPlotItem(
                symbol="o", size=5, pen=pg.mkPen(color), brush=pg.mkBrush(color)
            )
            curve.setZValue(10)
            markers.setZValue(11)
            self.plot_item.addItem(curve)
            self.plot_item.addItem(markers)
            self.additional_series.append(AdditionalSeries(h5_path, series_name, color, curve, markers))
        self._series_entries = normalized_entries
        self.apply_line_widths()
        self._reload_failure_regions()
        self.update_visible_data(auto_range=True)
        return self.all_current_data()

    def series_colors(self) -> list[str]:
        return [self.primary_color, *(entry.color for entry in self.additional_series)]

    def _fit_time_axis_to_data(self, loaded) -> None:  # type: ignore[no-untyped-def]
        """Show the time axis from the first to the last sample, without padding, so the
        series reach the plot area's edges where the Plot navigator's full range ends."""
        times = [np.asarray(data.x, dtype=float) for data in loaded if data is not None and len(data.x)]
        times = [values[np.isfinite(values)] for values in times]
        times = [values for values in times if len(values)]
        if not times:
            return
        start, stop = min(float(values.min()) for values in times), max(float(values.max()) for values in times)
        if stop > start:
            self.plot_item.getViewBox().setXRange(start, stop, padding=0)

    def series_name_at(self, position) -> str | None:  # type: ignore[no-untyped-def]
        """The name of the series drawn under a canvas position, for the hover name."""
        items = []
        if self._series_entries:
            name = series_legend_name(*self._series_entries[0])
            items += [(self.curve, name), (self.markers, name)]
        for entry in self.additional_series:
            name = series_legend_name(entry.h5_path, entry.series_name)
            items += [(entry.curve, name), (entry.markers, name)]
        return name_at_canvas_point(self.plot_widget, position, items)

    def entry_at(self, position) -> tuple[Path, str] | None:  # type: ignore[no-untyped-def]
        """The source of the series drawn within SELECT_TOLERANCE_PIXELS of a canvas position."""
        items = []
        if self._series_entries:
            items += [(self.curve, self._series_entries[0]), (self.markers, self._series_entries[0])]
        for entry in self.additional_series:
            source = (entry.h5_path, entry.series_name)
            items += [(entry.curve, source), (entry.markers, source)]
        return item_at_canvas_point(self.plot_widget, position, items, SELECT_TOLERANCE_PIXELS)

    def _line_width(self, source) -> int:  # type: ignore[no-untyped-def]
        return series_line_width(selected=source is not None and source == self.selected_entry)

    def set_selected_entry(self, source) -> None:  # type: ignore[no-untyped-def]
        """Draw this series (its source) at the selected width, or none."""
        self.selected_entry = source
        self.apply_line_widths()

    def apply_line_widths(self) -> None:
        """Every line at the series width, the selected one wider and in front."""
        primary = self._series_entries[0] if self._series_entries else None
        self.curve.setPen(pg.mkPen(self.primary_color, width=self._line_width(primary)))
        self.curve.setZValue(15 if primary is not None and primary == self.selected_entry else 10)
        for entry in self.additional_series:
            source = (entry.h5_path, entry.series_name)
            entry.curve.setPen(pg.mkPen(entry.color, width=self._line_width(source)))
            entry.curve.setZValue(15 if source == self.selected_entry else 10)

    def set_entry_colors(self, colors: dict[tuple[Path, str], str]) -> None:
        """Use these colors for the sources when they are next shown."""
        self._entry_colors.update({(Path(path), str(name)): str(color) for (path, name), color in colors.items()})

    def set_data_time_range(self, time_range: tuple[float, float] | None) -> None:
        """Restrict loaded samples without changing the current ViewBox."""
        self.data_time_range = tuple(sorted(time_range)) if time_range is not None else None
        self._view_update_timer.stop()
        self.update_visible_data(auto_range=False)

    def _apply_primary_color(self, color: str) -> None:
        self.primary_color = color
        primary = self._series_entries[0] if self._series_entries else None
        self.curve.setPen(pg.mkPen(color, width=self._line_width(primary)))
        self.markers.setPen(pg.mkPen(color))
        self.markers.setBrush(pg.mkBrush(color))

    def set_series_color(self, index: int, color: str) -> None:
        if 0 <= index < len(self._series_entries):
            self._entry_colors[self._series_entries[index]] = color
        if index == 0:
            self._apply_primary_color(color)
        elif 0 <= index - 1 < len(self.additional_series):
            entry = self.additional_series[index - 1]
            entry.color = color
            entry.curve.setPen(pg.mkPen(color, width=self._line_width((entry.h5_path, entry.series_name))))
            entry.markers.setPen(pg.mkPen(color))
            entry.markers.setBrush(pg.mkBrush(color))
        self._apply_diagnostic_colors()

    def all_current_data(self) -> list[PlotSeriesData]:
        return [data for data in [self._current_data, *(entry.data for entry in self.additional_series)] if data is not None]

    def _clear_additional_series(self) -> None:
        for entry in self.additional_series:
            self.plot_item.removeItem(entry.curve)
            self.plot_item.removeItem(entry.markers)
        self.additional_series.clear()

    def clear_series(self) -> None:
        """Reset every rendered item when a plot card becomes intentionally empty."""
        self._view_update_timer.stop()
        self._visible_request_id = self._visible_loads.invalidate()
        self._visible_request_explicit = self._visible_request_auto_range = False
        self.curve.setData(np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64))
        self.markers.setData([], [])
        self._clear_additional_series()
        self._remove_diagnostic_items()
        self._series_entries = []
        self.h5_path = None
        self.series_name = ""
        self.time_axis_metadata = None
        self._current_data = None
        self.plot_item.setTitle(None)
        self.plot_item.setLabel("left", "")
        self.hide_marker_values()
        self.hide_selection_statistics()

    def hide_marker_values(self, index: int | None = None) -> None:
        """Hide a marker's values boxes, or both, and make outstanding reads obsolete."""
        for marker in (FIRST_MARKER, SECOND_MARKER) if index is None else (index,):
            self._marker_loads.invalidate(marker)
            self._marker_x[marker] = None
            self.value_boxes.clear(marker)

    def hide_selection_statistics(self) -> None:
        """Hide the selected-range summary and invalidate an in-flight read."""
        self._selection_request_id = self._selection_loads.invalidate()
        self._selection_range = None
        self._statistics = []
        self.selection_info.clear()
        if not self.shows_marker_values:
            self._show_statistics_boxes()

    def dispose(self) -> None:
        """Release UI callbacks while background HDF5 reads finish safely."""
        self._view_update_timer.stop()
        self._marker_loads.shutdown()
        self._visible_loads.shutdown()
        self.hide_selection_statistics()
        self._selection_loads.shutdown()

    def show_selection_statistics(self, left: float, right: float) -> None:
        """Calculate exact statistics for every series in the selected area."""
        if not self._series_entries or right <= left:
            self.hide_selection_statistics()
            return
        self._selection_range = (left, right)
        self._statistics = []
        self.value_boxes.set_statistics({})
        self.selection_info.show(left, right)
        # Samples outside the Plot navigator data range are not drawn and
        # must not contribute to the statistics.
        load_left, load_right = self._intersect_time_ranges((left, right), self.data_time_range)
        if load_right <= load_left:
            self._selection_request_id = self._selection_loads.invalidate()
            return
        self._selection_request_id = self._selection_loads.request(
            load_left, load_right, list(self._series_entries), self.entry_time_offsets()
        )

    def _show_loaded_selection_statistics(
        self, request_id: int, _left: float, _right: float, statistics: list[SelectionStatistics]
    ) -> None:
        if request_id != self._selection_request_id or self._selection_range is None:
            return
        self._statistics = list(statistics)
        colors = self.series_colors()
        self.value_boxes.set_statistics({
            self._series_entries[item.series_index]: _selection_statistics_entry(
                item, colors[item.series_index % len(colors)]
            ).details
            for item in statistics if item.series_index < len(self._series_entries)
        })
        if not self.shows_marker_values:
            self._show_statistics_boxes()

    def set_marker_snap_enabled(self, enabled: bool) -> None:
        self.plot_widget.snap_marker_to_nearest_point = bool(enabled)

    def nearest_visible_x(self, x_value: float) -> float:
        """The rendered sample closest in time among all the card's series, without an HDF5 read."""
        candidates = []
        for data in self.all_current_data():
            if not len(data.x):
                continue
            index = int(np.searchsorted(data.x, x_value, side="left"))
            candidates.extend(float(data.x[candidate]) for candidate in (index - 1, index)
                              if 0 <= candidate < len(data.x) and np.isfinite(data.x[candidate]))
        return min(candidates, key=lambda candidate: abs(candidate - x_value)) if candidates else x_value

    def show_marker_values(self, x_value: float, index: int = FIRST_MARKER) -> None:
        """Fetch each series' values at a marker in the background, then show its boxes."""
        if not self._series_entries:
            # Only lanes without H5 series, such as filmstrips: nothing to read.
            extra = self.extra_marker_box_items(x_value)
            if extra:
                self._marker_x[index] = x_value
                self.value_boxes.set_items(index, x_value, extra)
            else:
                self.hide_marker_values(index)
            return
        if self.data_time_range is not None and not (self.data_time_range[0] <= x_value <= self.data_time_range[1]):
            self.hide_marker_values(index)
            return
        self._marker_x[index] = x_value
        if not self.shows_marker_values:
            # Values here describe intervals, not samples: show only selection statistics.
            self._show_statistics_boxes()
            return
        self._marker_loads.request(index, x_value, list(self._series_entries), x_offsets=self.entry_time_offsets())

    def _show_loaded_marker_values(self, index: int, x_value: float, samples: list[MarkerSample]) -> None:
        entries = marker_entries_from_samples(samples, self.series_colors())
        items = []
        for sample, entry in zip(samples, entries):
            series_index = sample.series_index if sample.series_index is not None else 0
            if series_index < len(self._series_entries):
                items.append(ValueBoxItem(self._series_entries[series_index], entry,
                                          self.value_box_y(series_index, sample.converted_value)))
        self.value_boxes.set_items(index, x_value, items + self.extra_marker_box_items(x_value))

    def entry_time_offsets(self) -> list[float] | None:
        """Seconds each series is shown later than its data; None when none is moved."""
        return None

    def refresh_marker_data(self) -> None:
        """Read the values at the markers and the selection statistics again."""
        for index, x_value in list(self._marker_x.items()):
            if x_value is not None:
                self.show_marker_values(x_value, index)
        if self._selection_range is not None:
            self.show_selection_statistics(*self._selection_range)

    def extra_marker_box_items(self, x_value: float) -> list[ValueBoxItem]:
        """Values boxes for lanes that are not H5 series, such as Multi-series filmstrips."""
        return []

    def value_box_y(self, series_index: int, value: float) -> float:
        """Where a series' value is drawn on the Y axis; cards that rescale series override this."""
        return value

    def _show_statistics_boxes(self) -> None:
        """Show selection statistics at both markers for cards without marker values."""
        colors = self.series_colors()
        for index, x_value in self._marker_x.items():
            items = [
                ValueBoxItem(self._series_entries[item.series_index],
                             _selection_statistics_entry(item, colors[item.series_index % len(colors)]),
                             self.value_box_y(item.series_index, item.average))
                for item in self._statistics if item.series_index < len(self._series_entries)
            ]
            if x_value is None or not items:
                self.value_boxes.clear(index)
            else:
                self.value_boxes.set_items(index, x_value, items)

    def _set_primary_series(self, h5_path: str | Path, series_name: str, plot_mode: str, *,
                            reload_diagnostics: bool = True) -> None:
        self._clear_additional_series()
        self._series_entries = [(Path(h5_path), series_name)]
        self.h5_path = Path(h5_path)
        self.series_name = series_name
        self.plot_mode = plot_mode
        self._configure_time_axis()
        if reload_diagnostics:
            self._reload_failure_regions()

    def show_series(self, h5_path: str | Path, series_name: str) -> PlotSeriesData | None:
        """Show one series; None while its data loads in the background."""
        self._set_primary_series(h5_path, series_name, "data")
        return self.update_visible_data(auto_range=True)

    def show_timing_delta(self, h5_path: str | Path, series_name: str) -> PlotSeriesData | None:
        self._set_primary_series(h5_path, series_name, "timing_delta")
        return self.update_visible_data(auto_range=True)

    def schedule_visible_data_update(self, *_args) -> None:  # type: ignore[no-untyped-def]
        if self.h5_path is not None and not self._updating_plot:
            # The view was set by the user or a workspace; keep it when a load arrives.
            self._visible_request_auto_range = False
            self._view_update_timer.start()

    def _uses_background_loads(self) -> bool:
        if self.background_loads is not None:
            return self.background_loads
        return QThread.currentThread().loopLevel() > 0

    def reload_plot_settings(self) -> None:
        settings = load_plot_settings(self.settings_path) if self.settings_path is not None else DEFAULT_PLOT_SETTINGS
        self.minimum_diagnostic_width_pixels = int(settings["minimum_diagnostic_width_pixels"])
        show_sample_loss = bool(settings["show_sample_loss_areas"])
        show_clipping = bool(settings["show_clipping_areas"])
        changed = (
            show_sample_loss != self.show_sample_loss_areas
            or show_clipping != self.show_clipping_areas
        )
        self.show_sample_loss_areas = show_sample_loss
        self.show_clipping_areas = show_clipping
        if changed and getattr(self, "h5_path", None) is not None and self.series_name:
            self._reload_failure_regions()
        for item in getattr(self, "_diagnostic_items", ()):
            item.set_minimum_pixels(self.minimum_diagnostic_width_pixels)

    def _reload_failure_regions(self) -> None:
        """Every series' Sample loss and Clipping areas; datasets of a hidden kind are not opened."""
        self._remove_diagnostic_items()
        loaders = ((SAMPLE_LOSS, self.show_sample_loss_areas, load_sample_loss_ranges),
                   (CLIPPING, self.show_clipping_areas, load_clipping_ranges))
        for index, (h5_path, series_name) in enumerate(self._series_entries):
            for kind, shown, load in loaders:
                if not shown:
                    continue
                try:
                    ranges = load(h5_path, series_name)
                except (OSError, KeyError, ValueError):
                    continue
                if not len(ranges):
                    continue
                item = DiagnosticAreas(kind, lambda index=index: self.diagnostic_value_range(index))
                item.series_index = index
                item.set_ranges(ranges)
                item.set_minimum_pixels(self.minimum_diagnostic_width_pixels)
                # These are visual diagnostics, not data bounds: auto-range ignores them.
                self.plot_item.addItem(item, ignoreBounds=True)
                self._diagnostic_items.append(item)
        self._apply_diagnostic_colors()

    def diagnostic_items(self, kind: str | None = None) -> list[DiagnosticAreas]:
        """The Sample loss (``SAMPLE_LOSS``) or Clipping (``CLIPPING``) areas, of every series."""
        return [item for item in self._diagnostic_items if kind is None or item.kind == kind]

    def _remove_diagnostic_items(self) -> None:
        for item in self._diagnostic_items:
            self.plot_item.removeItem(item)
        self._diagnostic_items.clear()

    def _apply_diagnostic_colors(self) -> None:
        """With several series, each area has a band in its series' color."""
        colors = self.series_colors()
        several = len(self._series_entries) > 1
        for item in self._diagnostic_items:
            index = item.series_index
            item.set_series_color(colors[index] if several and index < len(colors) else None)

    def _entry_loader(self, *, primary: bool):  # type: ignore[no-untyped-def]
        """Return a thread-safe reader; its result is accepted by `_accept_entry_payload`."""
        if primary and self.plot_mode == "timing_delta":
            return load_timing_delta_series
        return self.load_data

    def _accept_entry_payload(self, h5_path: Path, series_name: str, payload: object) -> PlotSeriesData:
        """Convert a reader result into plotted data on the GUI thread."""
        return payload  # type: ignore[return-value]

    def _visible_load_calls(self, auto_range: bool) -> list:  # type: ignore[type-arg]
        view_x_range = None if auto_range else tuple(self.plot_item.getViewBox().viewRange()[0])
        x_range = self._intersect_time_ranges(view_x_range, self.data_time_range)
        maximum_points = max(1_000, self.plot_widget.viewport().width() * 4)
        entries = [(self.h5_path, self.series_name), *((entry.h5_path, entry.series_name) for entry in self.additional_series)]
        return [
            partial(
                self._entry_loader(primary=index == 0),
                h5_path,
                series_name,
                maximum_points=maximum_points,
                x_range=x_range,
            )
            for index, (h5_path, series_name) in enumerate(entries)
        ]

    def _visible_keys(self) -> tuple[tuple[object, ...], ...]:
        return (
            (self.h5_path, self.series_name, self.plot_mode),
            *((entry.h5_path, entry.series_name) for entry in self.additional_series),
        )

    def update_visible_data(self, *, auto_range: bool = False) -> PlotSeriesData | None:
        """Load the visible window after a series or data range change.

        While an event loop runs the load is queued in the background, its
        result is drawn later and None is returned; `load_failed` reports a
        primary-series error. Otherwise the data is loaded at once and the
        error is raised.
        """
        if self.h5_path is None or not self.series_name:
            return None
        if self._uses_background_loads():
            self._request_visible_data(explicit=True, auto_range=auto_range)
            return None
        # This load supersedes a background pan/zoom result.
        self._visible_request_id = self._visible_loads.invalidate()
        self._visible_request_explicit = self._visible_request_auto_range = False
        calls = self._visible_load_calls(auto_range)
        results: list[object] = [calls[0]()]
        for call in calls[1:]:
            try:
                results.append(call())
            except (OSError, KeyError, ValueError) as error:
                results.append(error)
        return self._apply_visible_data(results, auto_range=auto_range)

    def _request_visible_data(self, *, explicit: bool = False, auto_range: bool = False) -> None:
        """Load the visible window off the GUI thread, keeping only the newest request."""
        if self.h5_path is None or not self.series_name:
            return
        self._visible_request_explicit = explicit = explicit or self._visible_request_explicit
        self._visible_request_auto_range = auto_range = auto_range or self._visible_request_auto_range
        self._visible_request_keys = self._visible_keys()
        self._visible_request_id = self._visible_loads.request(self._visible_load_calls(auto_range))

    def _show_loaded_visible_data(self, request_id: int, results: list[object]) -> None:
        if (
            request_id != self._visible_request_id
            or self.h5_path is None
            or self._visible_request_keys != self._visible_keys()
            or len(results) != len(self._visible_request_keys)
        ):
            return
        explicit, auto_range = self._visible_request_explicit, self._visible_request_auto_range
        self._visible_request_explicit = self._visible_request_auto_range = False
        error: BaseException | None = results[0] if isinstance(results[0], BaseException) else None
        if error is None:
            try:
                self._apply_visible_data(results, auto_range=auto_range)
            except (OSError, KeyError, ValueError) as apply_error:
                error = apply_error
        if error is not None:
            # A cache can disappear or be regenerated while a card is open.
            # Keep the current plot and report the failure.
            self.status_changed.emit(f"Could not load the visible data for {self.series_name}: {error}")
            if explicit:
                self.load_failed.emit(str(error))

    def _apply_visible_data(self, results: list[object], *, auto_range: bool) -> PlotSeriesData:
        """Render loaded primary/additional results; failed additional series are cleared."""
        data = self._accept_entry_payload(self.h5_path, self.series_name, results[0])
        self._updating_plot = True
        try:
            self._set_plot_data(data)
            self._update_additional_series(results[1:])
            if auto_range:
                self.plot_item.enableAutoRange()
                self.plot_item.autoRange()
                self.plot_item.disableAutoRange()
                self._fit_time_axis_to_data([data, *(entry.data for entry in self.additional_series)])
            self._current_data = data
            self._update_multi_series_status(data)
            return data
        finally:
            self._updating_plot = False

    @staticmethod
    def _intersect_time_ranges(
        first: tuple[float, float] | None, second: tuple[float, float] | None
    ) -> tuple[float, float] | None:
        if first is None:
            return second
        if second is None:
            return first
        lower = max(min(first), min(second))
        upper = min(max(first), max(second))
        # An inverted data request makes HDF5 bounds awkward.  Return a
        # zero-width range so the loader safely yields no out-of-range data.
        return (lower, max(lower, upper))

    def set_draw_style(self, style: str) -> None:
        """Draw lines and points as ``style`` says ("auto", "lines" or "points") and redraw."""
        self.draw_style = style
        for _key, curve, markers, data in self._drawn_items():
            curve.setVisible(style != "points")
            self._show_points(curve, markers, data)

    def _drawn_items(self):  # type: ignore[no-untyped-def]
        yield None, self.curve, self.markers, self._current_data
        for entry in self.additional_series:
            yield (entry.h5_path, entry.series_name), entry.curve, entry.markers, entry.data

    def markers_shown(self, data: PlotSeriesData) -> bool:
        if self.draw_style == "points":
            return True
        return self.draw_style == "auto" and data.lod_level is None and len(data.x) <= MAX_MARKER_POINTS

    def _show_points(self, curve, markers, data: PlotSeriesData | None) -> None:  # type: ignore[no-untyped-def]
        """No line when drawing only points (a style change shows it again); the points as the style says."""
        if self.draw_style == "points":
            curve.setVisible(False)
        if data is not None and self.markers_shown(data):
            # Many points are drawn smaller, so they stay apart.
            markers.setData(data.x, data.y, size=5 if len(data.x) <= MAX_MARKER_POINTS else 3)
        else:
            markers.clear()

    def _set_plot_data(self, data: PlotSeriesData) -> None:
        self.curve.setData(data.x, data.y)
        self._show_points(self.curve, self.markers, data)
        # The card drag handle already provides a top edge.  A duplicated
        # series title consumes valuable plot height without adding context.
        # An empty string still reserves PyQtGraph's 30-pixel title row.
        self.plot_item.setTitle(None)
        self.plot_item.setLabel("left", self.value_axis_label, units=data.unit or None)

    def _configure_time_axis(self) -> None:
        if self.h5_path is None or not self.series_name:
            return
        metadata = load_time_axis_metadata(self.h5_path, self.series_name)
        self.time_axis_metadata = metadata
        self.set_time_axis_display_metadata(metadata)

    def set_time_axis_display_metadata(self, metadata: TimeAxisMetadata) -> None:
        """Use a shared display conversion without changing series data."""
        metadata = _display_time_axis_metadata(metadata)
        self.plot_widget.configure_time_axis(
            raw_origin=metadata.raw_origin,
            seconds_per_unit=metadata.seconds_per_unit,
            log_start_unix_seconds=metadata.log_start_unix_seconds,
            is_unix_time=metadata.is_unix_time,
        )

    def restore_own_time_axis_metadata(self) -> None:
        """Return the axis labels to this series' own H5 time metadata."""
        if self.time_axis_metadata is not None:
            self.set_time_axis_display_metadata(self.time_axis_metadata)
    def _update_additional_series(self, results: list[object]) -> None:
        for entry, payload in zip(self.additional_series, results):
            if isinstance(payload, BaseException):
                entry.curve.clear()
                entry.markers.clear()
                entry.data = None
                continue
            data = self._accept_entry_payload(entry.h5_path, entry.series_name, payload)
            entry.data = data
            entry.curve.setData(data.x, data.y)
            self._show_points(entry.curve, entry.markers, data)

    def _update_multi_series_status(self, primary_data: PlotSeriesData) -> None:
        all_data = [primary_data, *(entry.data for entry in self.additional_series if entry.data is not None)]
        point_count = sum(len(data.x) for data in all_data)
        lod_text = ", ".join(
            "raw" if data.lod_level is None else f"LOD {data.lod_level}"
            for data in all_data
        )
        marker_text = "markers shown" if all(self.markers_shown(data) for data in all_data) else "markers hidden"
        self.status_changed.emit(
            f"{point_count:,} plotted points ({len(all_data)} series: {lod_text}; {marker_text})"
        )

    def diagnostic_value_range(self, series_index: int = 0) -> tuple[float, float] | None:
        """Y range a series' diagnostic areas cover, in view coordinates; None for the full height."""
        return None

    def _update_diagnostic_region_spans(self) -> None:
        """Repaint the areas after the series' lanes moved (Multi-series)."""
        for item in self._diagnostic_items:
            item.update()
