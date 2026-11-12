"""Bounded, timestamp-aligned samples for Cartesian 3D traces."""

from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event

import h5py
import numpy as np

from src.data.sampling import SAMPLE_LIMIT_ALL, decimation_step
from src.data.h5_series import (
    _attribute_text, _canonical_time_values, _canonical_to_native_range,
    _series_group_for_navigator_name, _time_axis_metadata_from_group, _visible_sample_bounds,
)


# Default points per 3D trace; the card's Sample limit selector can change it.
XYZ_MAXIMUM_POINTS = 50_000


@dataclass(frozen=True)
class AxisSource:
    path: Path
    name: str
    component: str = "value"

    def __post_init__(self):
        object.__setattr__(self, "path", Path(self.path))
        if self.component not in {"value", "time"}:
            raise ValueError("An axis must use values or time")


@dataclass
class XYZData:
    time: np.ndarray
    positions: np.ndarray
    labels: tuple[str, str, str]
    sampled: bool
    # Samples in the shared time range before every-Nth sampling.
    available_points: int | None = None

    @property
    def shown_points(self) -> int:
        return len(self.time)


def nearest_sample_indices(times, queries) -> np.ndarray:
    """Return the index of the ascending ``times`` sample nearest each query."""
    times = np.asarray(times, dtype=float)
    queries = np.asarray(queries, dtype=float)
    upper = np.clip(np.searchsorted(times, queries, side="left"), 0, len(times) - 1)
    lower = np.maximum(0, upper - 1)
    return np.where(np.abs(queries - times[lower]) <= np.abs(times[upper] - queries), lower, upper)


def _check_cancelled(cancelled):
    if cancelled is not None and cancelled.is_set():
        raise InterruptedError("3D data loading cancelled")


def _aligned_values(times, values, metadata, queries, cancelled):
    """Find nearest samples using bounded chunks, including chunk boundaries."""
    start, stop = _visible_sample_bounds(times, _canonical_to_native_range(
        (float(queries[0]), float(queries[-1])), metadata))
    start, stop = max(0, start - 1), min(len(times), stop + 1)
    result = np.full(len(queries), np.nan)
    previous_time = previous_value = None
    for offset in range(start, stop, 65_536):
        _check_cancelled(cancelled)
        end = min(stop, offset + 65_536)
        chunk = _canonical_time_values(np.asarray(times[offset:end], dtype=float), metadata)
        data = np.asarray(values[offset:end], dtype=float) if values is not None else chunk.copy()
        if previous_time is not None:
            chunk = np.concatenate(([previous_time], chunk))
            data = np.concatenate(([previous_value], data))
        if not np.all(np.isfinite(chunk)) or np.any(np.diff(chunk) < 0):
            raise ValueError("3D plots require finite timestamps in ascending order")
        left = np.searchsorted(queries, chunk[0], side="left")
        right = np.searchsorted(queries, chunk[-1], side="right")
        query = queries[left:right]
        upper = np.clip(np.searchsorted(chunk, query, side="left"), 0, len(chunk) - 1)
        lower = np.maximum(0, upper - 1)
        nearest = np.where(np.abs(query - chunk[lower]) <= np.abs(chunk[upper] - query), lower, upper)
        result[left:right] = data[nearest]
        previous_time, previous_value = chunk[-1], data[-1]
    return result


def load_xyz_data(axes: tuple[AxisSource, AxisSource, AxisSource], *, time_range=None,
                  maximum_points=XYZ_MAXIMUM_POINTS, cancelled: Event | None = None) -> XYZData:
    """Use X timestamps within the shared coverage; never extrapolate Y or Z."""
    if len(axes) != 3 or (maximum_points != SAMPLE_LIMIT_ALL and maximum_points < 2):
        raise ValueError("A 3D trace requires three axes and a point budget of at least two")
    with ExitStack() as stack:
        sources, labels, absolute_axes = [], [], []
        for axis in axes:
            _check_cancelled(cancelled)
            cache = stack.enter_context(h5py.File(axis.path, "r"))
            group = _series_group_for_navigator_name(cache, axis.name)
            times = group[_attribute_text(group.attrs.get("time_dataset", "time_unix"))]
            values = group["value"]
            if (times.ndim != 1 or values.ndim != 1 or len(times) != len(values)
                    or values.dtype.kind not in "biuf" or not len(times)):
                raise ValueError(f"{axis.name}: expected nonempty scalar numeric samples")
            metadata = _time_axis_metadata_from_group(cache, group)
            absolute = metadata.is_unix_time or metadata.log_start_unix_seconds is not None
            absolute_axes.append(absolute)
            # Relative timestamps also need a common unit for mixed-rate/mixed-unit inputs.
            if not absolute:
                metadata = replace(metadata, is_unix_time=True)
            bounds = _canonical_time_values(np.asarray([times[0], times[-1]], dtype=float), metadata)
            if not np.all(np.isfinite(bounds)) or bounds[0] > bounds[1]:
                raise ValueError(f"{axis.name}: invalid time range")
            sources.append((times, values if axis.component == "value" else None, metadata, bounds))
            unit = _attribute_text(group.attrs.get("unit", ""))
            label = f"{axis.path.parent.name}/{axis.name}"
            if axis.component == "time":
                unit = "Unix s" if absolute else "s"
                label = f"Time of {label}"
            labels.append(f"{label} ({unit})" if unit else label)
        if any(absolute_axes) and not all(absolute_axes):
            raise ValueError("Cannot align absolute timestamps with series lacking a log start time")
        lower = max(source[3][0] for source in sources)
        upper = min(source[3][1] for source in sources)
        if time_range is not None:
            lower, upper = max(lower, min(time_range)), min(upper, max(time_range))
        if lower > upper:
            raise ValueError("The three series have no overlapping time range")
        times, _, metadata, _ = sources[0]
        start, stop = _visible_sample_bounds(times, _canonical_to_native_range((lower, upper), metadata))
        if stop <= start:
            raise ValueError("The shared time range contains no X samples")
        step = decimation_step(stop - start, maximum_points)
        query = _canonical_time_values(np.asarray(times[start:stop:step], dtype=float), metadata)
        if not np.all(np.isfinite(query)) or np.any(np.diff(query) < 0):
            raise ValueError("3D plots require finite timestamps in ascending order")
        positions = np.column_stack([
            _aligned_values(time, values, meta, query, cancelled)
            for time, values, meta, _ in sources
        ])
        if not np.any(np.all(np.isfinite(positions), axis=1)):
            raise ValueError("The shared time range contains no finite 3D points")
        return XYZData(query, positions, tuple(labels), step > 1, stop - start)
