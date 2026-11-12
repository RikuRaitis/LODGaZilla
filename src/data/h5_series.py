"""HDF5 series lookup and time-axis helpers shared by data, jobs and GUI code."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

import h5py
import numpy as np


SERIES_PATH_CACHE_LIMIT = 64


@dataclass(frozen=True)
class _SeriesPathIndex:
    """Navigator identifiers resolved to physical H5 group paths."""

    paths: dict[str, str]
    full_navigator_names: tuple[str, ...]


_SERIES_PATH_CACHE: OrderedDict[tuple[str, int, int], _SeriesPathIndex] = OrderedDict()
_SERIES_PATH_CACHE_LOCK = RLock()


def _series_path_cache_key(h5_file: h5py.File) -> tuple[str, int, int] | None:
    """Return a generation-aware cache key without retaining an HDF5 handle."""
    try:
        path = Path(h5_file.filename).resolve()
        stat = path.stat()
    except (OSError, TypeError, ValueError):
        return None
    return str(path), stat.st_mtime_ns, stat.st_size


def _build_series_path_index(h5_file: h5py.File) -> _SeriesPathIndex:
    series_root = h5_file.get("series")
    if not isinstance(series_root, h5py.Group):
        raise KeyError("HDF5 cache has no /series group")

    paths: dict[str, str] = {}
    full_names: list[str] = []

    def add_group(_path: str, candidate) -> None:  # type: ignore[no-untyped-def]
        if not isinstance(candidate, h5py.Group) or "value" not in candidate:
            return
        navigator_name = _attribute_text(candidate.attrs.get("navigator_name", ""))
        navigator_group = _attribute_text(candidate.attrs.get("navigator_group", ""))
        full_name = "/".join(part for part in (navigator_group, navigator_name) if part)
        physical_path = candidate.name
        if navigator_name:
            paths.setdefault(navigator_name, physical_path)
        if full_name:
            paths.setdefault(full_name, physical_path)
            full_names.append(full_name)

    series_root.visititems(add_group)
    return _SeriesPathIndex(paths, tuple(full_names))


def _series_path_index(h5_file: h5py.File, *, refresh: bool = False) -> _SeriesPathIndex:
    """Get an LRU-cached navigator index, invalidated by H5 replacement."""
    cache_key = _series_path_cache_key(h5_file)
    if cache_key is not None and not refresh:
        with _SERIES_PATH_CACHE_LOCK:
            cached = _SERIES_PATH_CACHE.get(cache_key)
            if cached is not None:
                _SERIES_PATH_CACHE.move_to_end(cache_key)
                return cached

    index = _build_series_path_index(h5_file)
    if cache_key is not None:
        with _SERIES_PATH_CACHE_LOCK:
            _SERIES_PATH_CACHE[cache_key] = index
            _SERIES_PATH_CACHE.move_to_end(cache_key)
            while len(_SERIES_PATH_CACHE) > SERIES_PATH_CACHE_LIMIT:
                _SERIES_PATH_CACHE.popitem(last=False)
    return index


def _navigator_series_names(h5_file: h5py.File) -> tuple[str, ...]:
    """Return full navigator identifiers without repeatedly walking /series."""
    return _series_path_index(h5_file).full_navigator_names


@dataclass(frozen=True)
class TimeAxisMetadata:
    raw_origin: float
    seconds_per_unit: float
    log_start_unix_seconds: float | None
    is_unix_time: bool


def _time_axis_metadata_from_group(h5_file: h5py.File, group: h5py.Group) -> TimeAxisMetadata:
    time_dataset_name = _attribute_text(group.attrs.get("time_dataset", "time_unix"))
    time_dataset = group[time_dataset_name]
    time_unit = _attribute_text(group.attrs.get("time_unit", time_dataset_name.removeprefix("time_")))
    time_reference = _attribute_text(group.attrs.get("time_reference", ""))
    seconds_per_unit = _time_unit_seconds(time_unit)
    is_unix_time = time_reference.casefold() == "unix" or time_dataset_name == "time_unix"
    raw_origin = float(time_dataset[0]) if len(time_dataset) else 0.0

    # Device-relative series can begin at different times in one cache (for
    # example, an event is first emitted minutes after the accelerometer
    # starts).  Their common H5 time boundary, not each series' first sample,
    # is the reference represented by `log_start_unix_seconds`.
    #
    # `time_start_seconds` is intentionally stored in seconds even when the
    # individual time datasets use milliseconds or microseconds.
    if not is_unix_time and seconds_per_unit > 0:
        file_start_seconds = _optional_float(h5_file.attrs.get("time_start_seconds"))
        if file_start_seconds is not None:
            raw_origin = file_start_seconds / seconds_per_unit
    return TimeAxisMetadata(
        raw_origin=raw_origin,
        seconds_per_unit=seconds_per_unit,
        log_start_unix_seconds=_optional_float(h5_file.attrs.get("log_start_unix_seconds")),
        is_unix_time=is_unix_time,
    )


def _canonical_time_values(values: np.ndarray, metadata: TimeAxisMetadata) -> np.ndarray:
    """Convert native H5 timestamps into Unix seconds when metadata allows it."""
    values = np.asarray(values, dtype=np.float64)
    if metadata.is_unix_time:
        return values * metadata.seconds_per_unit
    if metadata.log_start_unix_seconds is not None:
        return metadata.log_start_unix_seconds + (values - metadata.raw_origin) * metadata.seconds_per_unit
    # A cache without an absolute log start cannot be aligned with other logs.
    return values


def _canonical_to_native_value(value: float, metadata: TimeAxisMetadata) -> float:
    if metadata.seconds_per_unit <= 0:
        return float(value)
    if metadata.is_unix_time:
        return float(value) / metadata.seconds_per_unit
    if metadata.log_start_unix_seconds is not None:
        return metadata.raw_origin + (float(value) - metadata.log_start_unix_seconds) / metadata.seconds_per_unit
    return float(value)


def _canonical_to_native_range(
    x_range: tuple[float, float] | None, metadata: TimeAxisMetadata
) -> tuple[float, float] | None:
    if x_range is None:
        return None
    return tuple(_canonical_to_native_value(value, metadata) for value in x_range)  # type: ignore[return-value]


def _visible_sample_bounds(
    time_dataset: h5py.Dataset,
    x_range: tuple[float, float] | None,
) -> tuple[int, int]:
    if x_range is None:
        return 0, len(time_dataset)
    lower, upper = sorted(x_range)
    return (
        _h5_searchsorted(time_dataset, lower, side="left"),
        _h5_searchsorted(time_dataset, upper, side="right"),
    )


def _h5_searchsorted(dataset: h5py.Dataset, value: float, *, side: str) -> int:
    """Binary-search an ascending HDF5 dataset without reading it all."""
    lower = 0
    upper = len(dataset)
    while lower < upper:
        middle = (lower + upper) // 2
        current = float(dataset[middle])
        if current < value or (side == "right" and current <= value):
            lower = middle + 1
        else:
            upper = middle
    return lower


def _series_group_for_navigator_name(h5_file: h5py.File, series_name: str) -> h5py.Group:
    """Find a series by its navigator identifier, not its storage path."""

    direct_path = f"series/{series_name}"
    direct_group = h5_file.get(direct_path)
    if isinstance(direct_group, h5py.Group) and "value" in direct_group:
        return direct_group

    # Most navigator names include a logical group that differs from the H5
    # storage path.  Resolve that mapping once per H5 generation instead of
    # rewalking every descendant for each plot/marker request.
    index = _series_path_index(h5_file)
    group_path = index.paths.get(series_name)
    matched_group = h5_file.get(group_path) if group_path else None
    if isinstance(matched_group, h5py.Group) and "value" in matched_group:
        return matched_group

    # A file can be regenerated between opening it and statting it. Rebuild
    # against the handle we actually own before reporting a missing series.
    index = _series_path_index(h5_file, refresh=True)
    group_path = index.paths.get(series_name)
    matched_group = h5_file.get(group_path) if group_path else None
    if isinstance(matched_group, h5py.Group) and "value" in matched_group:
        return matched_group
    raise KeyError(f"Series not found: {series_name}")


def _attribute_text(value: object) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


def is_event_state_group(group) -> bool:  # type: ignore[no-untyped-def]
    """Whether an H5 series group holds events or states, drawn as a timeline rather than a line.

    The unit ``bool`` or ``index`` marks it, as does an ``event_state_kind`` attribute or
    boolean values. Card selection and the Multi-series card share this rule.
    """
    if _attribute_text(group.attrs.get("unit", "")).strip().casefold() in {"bool", "index"}:
        return True
    if _attribute_text(group.attrs.get("event_state_kind", "")).strip():
        return True
    return "value" in group and group["value"].dtype.kind == "b"


def _optional_float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def _time_unit_seconds(unit: str) -> float:
    # The same names the H5 writer accepts (time_unit_seconds_multiplier), so a time
    # stored in "milliseconds" is not plotted 1000 times too large.
    return {
        "ns": 1e-9, "nanosecond": 1e-9, "nanoseconds": 1e-9,
        "us": 1e-6, "µs": 1e-6, "microsecond": 1e-6, "microseconds": 1e-6,
        "ms": 1e-3, "millisecond": 1e-3, "milliseconds": 1e-3,
        "s": 1.0, "sec": 1.0, "secs": 1.0, "second": 1.0, "seconds": 1.0,
    }.get(unit.strip().casefold(), 1.0)


def series_value_range(h5_file: h5py.File, series_name: str, *, scan_raw: bool = False) -> tuple[float, float] | None:
    """Return full finite bounds from LOD, optionally scanning raw chunks off the GUI thread."""
    group = _series_group_for_navigator_name(h5_file, series_name)
    lod = group.get("lod")
    if isinstance(lod, h5py.Group) and "top_level" in lod.attrs:
        level = lod.get(_attribute_text(lod.attrs["top_level"]))
        if isinstance(level, h5py.Group) and "value_min" in level and "value_max" in level:
            lows = np.asarray(level["value_min"], dtype=np.float64)
            highs = np.asarray(level["value_max"], dtype=np.float64)
            lows, highs = lows[np.isfinite(lows)], highs[np.isfinite(highs)]
            if len(lows) and len(highs):
                return float(lows.min()), float(highs.max())
    if not scan_raw:
        return None
    values = group["value"]
    low, high = np.inf, -np.inf
    for start in range(0, len(values), 65536):
        chunk = np.asarray(values[start:start + 65536], dtype=np.float64)
        finite = chunk[np.isfinite(chunk)]
        if len(finite):
            low, high = min(low, float(finite.min())), max(high, float(finite.max()))
    return (low, high) if low <= high else None
