"""Filesystem and HDF5 metadata model used by the data navigator."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import h5py

from src.data.folder_info import (
    FolderInfoData,
    ensure_info_file,
    is_info_file,
    normalise_info_key,
    normalise_info_mapping,
)
from src.data.media import AUDIO_KIND, MEDIA_KIND_ATTR

@dataclass(frozen=True)
class H5Series:
    name: str
    display_name: str
    navigator_name: str
    navigator_group: str
    unit: str
    sample_count: int
    sample_rate_hz: float | None
    visible: bool
    metadata: list["SeriesMetadata"] = field(default_factory=list)


@dataclass(frozen=True)
class SeriesMetadata:
    column_id: str
    title: str
    value: str


@dataclass
class FolderInfoIndex:
    keys: set[str]
    editable_keys: set[str]
    values_by_folder: dict[Path, dict[str, str]]
    editable_values_by_folder: dict[Path, dict[str, str]]


@dataclass(frozen=True)
class NavigatorFolderScan:
    folders: list[Path]
    h5_files: list[Path]


@dataclass(frozen=True)
class H5NavigatorData:
    """All navigator metadata read from one HDF5 open operation."""

    series: list[H5Series]
    error: str | None
    time_range: tuple[float, float] | None


def add_default_info_keys(info_index: FolderInfoIndex, keys: Iterable[str]) -> FolderInfoIndex:
    """Expose configured metadata keys without persisting blank values."""
    default_keys = {normalise_info_key(key) for key in keys if normalise_info_key(key)}
    if not default_keys:
        return info_index
    info_index.keys.update(default_keys)
    info_index.editable_keys.update(default_keys)
    for folder, values in info_index.values_by_folder.items():
        editable_values = info_index.editable_values_by_folder.setdefault(folder, {})
        for key in default_keys:
            # A key that already holds a read-only value stays read-only.
            if key in values and key not in editable_values:
                continue
            values.setdefault(key, "")
            editable_values.setdefault(key, "")
    return info_index


SERIES_INDEX_ATTR = "series_index_json"


def scan_navigator_folder(data_folder: Path) -> NavigatorFolderScan:
    """Collect folders and HDF5 files in one filesystem traversal."""

    if not data_folder.is_dir():
        return NavigatorFolderScan([], [])
    folders: list[Path] = []
    h5_files: list[Path] = []
    for current_text, directory_names, file_names in os.walk(data_folder):
        current = Path(current_text)
        folders.extend(current / name for name in directory_names)
        h5_files.extend(
            current / name
            for name in file_names
            if Path(name).suffix.lower() in {".h5", ".hdf5"}
        )
    folders.sort(key=lambda path: (len(path.relative_to(data_folder).parts), str(path).lower()))
    h5_files.sort(key=lambda path: str(path).lower())
    return NavigatorFolderScan(folders, h5_files)


def folder_has_h5_files(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    return any(path.is_file() and path.suffix.lower() in {".h5", ".hdf5"} for path in folder.iterdir())


def ensure_folder_info_files(
    folders: list[Path], *, on_error: Callable[[Path, str], None] | None = None
) -> None:
    for folder in folders:
        if not folder.is_dir():
            # Never recreate a folder that was deleted or renamed meanwhile.
            continue
        try:
            # Do not parse every JSON here: refresh uses the parsed result
            # immediately afterwards to build the snapshot.
            ensure_info_file(folder)
        except OSError as error:
            if on_error is not None:
                on_error(folder, str(error))
            continue


def read_h5_navigator_data(h5_path: Path) -> H5NavigatorData:
    """Read series and duration together, without repeatedly reopening a file."""
    try:
        with h5py.File(h5_path, "r") as h5_file:
            series, error = read_h5_series_from_file(h5_file)
            # An audio file's times count from its video's start, so they say nothing
            # about when the folder's recording ran.
            audio = str(h5_file.attrs.get(MEDIA_KIND_ATTR, "")) == AUDIO_KIND
            return H5NavigatorData(series, error, None if audio else h5_time_range_seconds_from_file(h5_file))
    except (OSError, ValueError) as error:
        return H5NavigatorData([], str(error), None)


def read_h5_series_from_file(h5_file: h5py.File) -> tuple[list[H5Series], str | None]:
    indexed_series = read_h5_series_index(h5_file)
    if indexed_series is not None:
        return indexed_series, None

    series_root = h5_file.get("series")
    if series_root is None:
        return [], "No /series group"

    series: list[H5Series] = []

    def collect(name: str, group: h5py.Group) -> None:
        if not isinstance(group, h5py.Group) or "value" not in group:
            return
        display_name = attribute_text(group.attrs.get("display_name", name))
        navigator_name = attribute_text(group.attrs.get("navigator_name", display_name))
        navigator_group = attribute_text(group.attrs.get("navigator_group", "Ungrouped"))
        unit = attribute_text(group.attrs.get("unit", ""))
        sample_count = int(group.attrs.get("sample_count", len(group["value"])))
        series.append(H5Series(
            name,
            display_name,
            navigator_name,
            navigator_group,
            unit,
            sample_count,
            calculate_sample_rate_hz(group, sample_count),
            bool(group.attrs.get("visible", True)),
            warning_metadata_for_group(group),
        ))

    series_root.visititems(collect)
    return sorted(series, key=lambda item: (item.navigator_group.lower(), item.navigator_name.lower())), None


def read_h5_series_index(h5_file: h5py.File) -> list[H5Series] | None:
    raw_index = h5_file.attrs.get(SERIES_INDEX_ATTR)
    if raw_index is None:
        return None
    try:
        payload = json.loads(attribute_text(raw_index))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list):
        return None

    warnings_by_series = diagnostic_warnings_by_series(h5_file)
    series: list[H5Series] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        navigator_name = attribute_text(item.get("navigator_name", item.get("display_name", item.get("name", ""))))
        display_name = attribute_text(item.get("display_name", navigator_name))
        navigator_group = attribute_text(item.get("navigator_group", "Ungrouped"))
        metadata = merge_warning_metadata(
            parse_series_metadata(item.get("navigator_metadata", [])),
            warnings_by_series.get((navigator_group, navigator_name), ""),
        )
        series.append(H5Series(
            attribute_text(item.get("name", navigator_name)),
            display_name,
            navigator_name,
            navigator_group,
            attribute_text(item.get("unit", "")),
            int(item.get("sample_count", 0) or 0),
                optional_float(item.get("sample_rate_hz")),
                bool(item.get("visible", True)),
                metadata,
            ))
    return sorted(series, key=lambda entry: (entry.navigator_group.lower(), entry.navigator_name.lower()))


def diagnostic_warnings_by_series(h5_file: h5py.File) -> dict[tuple[str, str], str]:
    """Scan the HDF5 series tree once and index its diagnostic warnings."""

    series_root = h5_file.get("series")
    if not isinstance(series_root, h5py.Group):
        return {}
    warnings: dict[tuple[str, str], str] = {}

    def collect(_name: str, group: h5py.Group) -> None:
        if not isinstance(group, h5py.Group) or "value" not in group:
            return
        key = (
            attribute_text(group.attrs.get("navigator_group", "Ungrouped")),
            attribute_text(group.attrs.get("navigator_name", "")),
        )
        warnings[key] = diagnostic_warning_for_group(group)

    series_root.visititems(collect)
    return warnings


def warning_metadata_for_group(group: h5py.Group | None) -> list[SeriesMetadata]:
    warning = diagnostic_warning_for_group(group)
    return [SeriesMetadata("warning", "Warning", warning)] if warning else []


def merge_warning_metadata(
    metadata: list[SeriesMetadata],
    warning: str,
) -> list[SeriesMetadata]:
    existing_warning = next((entry for entry in metadata if entry.column_id == "warning"), None)
    without_warning = [entry for entry in metadata if entry.column_id != "warning"]
    if warning:
        return [*without_warning, SeriesMetadata("warning", existing_warning.title if existing_warning else "Warning", warning)]
    if existing_warning is not None:
        return [*without_warning, existing_warning]
    return metadata


def diagnostic_warning_for_group(group: h5py.Group | None) -> str:
    if group is None:
        return ""
    has_sample_loss = any(
        diagnostic_list_has_ranges(group.get(name), "sample_loss")
        for name in ("sample_loss_list", "failure_list")
    )
    has_clipping = diagnostic_list_has_ranges(group.get("clipping_list"), "clipping")
    if has_sample_loss and has_clipping:
        return "Sampleloss and Clipping detected"
    if has_sample_loss:
        return "Sampleloss detected"
    if has_clipping:
        return "Clipping detected"
    return ""


def diagnostic_list_has_ranges(candidate: object, range_name: str) -> bool:
    if not isinstance(candidate, h5py.Group):
        return False
    count_name = f"{range_name}_count"
    try:
        if int(candidate.attrs.get(count_name, 0)) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return any(name.startswith(f"{range_name}_start") for name in candidate)


def parse_series_metadata(raw_metadata: object) -> list[SeriesMetadata]:
    if not isinstance(raw_metadata, list):
        return []
    metadata: list[SeriesMetadata] = []
    for item in raw_metadata:
        if not isinstance(item, dict):
            continue
        column_id = attribute_text(item.get("id", "")).strip()
        title = attribute_text(item.get("title", column_id)).strip()
        if not column_id or not title:
            continue
        metadata.append(SeriesMetadata(
            column_id,
            title,
            format_series_metadata_value(item.get("value"), attribute_text(item.get("format", "text"))),
        ))
    return metadata


def format_series_metadata_value(value: object, display_format: str) -> str:
    if value is None:
        return ""
    if display_format == "integer":
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return ""
    if display_format == "rate":
        return format_sample_rate(optional_float(value))
    if display_format == "number":
        number = optional_float(value)
        if number is None:
            return ""
        return f"{number:.6g}"
    return attribute_text(value)


def scan_folder_info_files(data_folder: Path) -> FolderInfoIndex:
    return build_folder_info_index(data_folder.rglob("*_info.json") if data_folder.is_dir() else [])


def folder_info_values(info: FolderInfoData) -> tuple[dict[str, str], dict[str, str]]:
    """Return a folder's editable values and all of its values for the index."""
    editable_values = {key.strip(): value for key, value in info.editable.items() if key.strip()}
    readonly_values = {key.strip(): value for key, value in info.readonly.items() if key.strip()}
    return editable_values, {**editable_values, **readonly_values}


def folder_info_index_from_data(infos_by_folder: dict[Path, FolderInfoData | None]) -> FolderInfoIndex:
    """Build the filter/grouping index from already parsed folder info."""
    keys: set[str] = set()
    editable_keys: set[str] = set()
    values_by_folder: dict[Path, dict[str, str]] = {}
    editable_values_by_folder: dict[Path, dict[str, str]] = {}
    for folder, info in infos_by_folder.items():
        if info is None:
            continue
        editable_values, values = folder_info_values(info)
        if not values:
            continue
        resolved_folder = folder.resolve()
        editable_values_by_folder[resolved_folder] = editable_values
        values_by_folder[resolved_folder] = values
        editable_keys.update(editable_values)
        keys.update(values)
    return FolderInfoIndex(keys, editable_keys, values_by_folder, editable_values_by_folder)


def update_folder_info_index(
    info_index: FolderInfoIndex,
    folder: Path,
    info: FolderInfoData | None,
    default_keys: Iterable[str] = (),
) -> FolderInfoIndex:
    """Replace one folder's index entry after an edit, without rescanning disk."""
    folder = folder.resolve()
    info_index.values_by_folder.pop(folder, None)
    info_index.editable_values_by_folder.pop(folder, None)
    if info is not None:
        editable_values, values = folder_info_values(info)
        if values:
            info_index.values_by_folder[folder] = values
            info_index.editable_values_by_folder[folder] = editable_values
    info_index.keys = {key for values in info_index.values_by_folder.values() for key in values}
    info_index.editable_keys = {
        key for values in info_index.editable_values_by_folder.values() for key in values
    }
    return add_default_info_keys(info_index, default_keys)


def metadata_values_for_key(info_index: FolderInfoIndex, key: str) -> list[str]:
    key = normalise_info_key(key)
    return sorted(
        {values[key] for values in info_index.values_by_folder.values() if key in values},
        key=str.casefold,
    )


def filter_folders_by_metadata(
    folders: Iterable[Path],
    info_index: FolderInfoIndex,
    key: str,
    query: str,
    *,
    empty_label: str = "(empty)",
) -> list[Path]:
    key = normalise_info_key(key)
    query = query.strip()
    if not key or not query:
        return list(folders)

    match_empty = query.casefold() == empty_label.casefold()
    needle = query.casefold()
    # A value picked from the list matches exactly; other text is a substring search.
    exact = any(value.strip().casefold() == needle for value in metadata_values_for_key(info_index, key))
    matches: list[Path] = []
    for folder in folders:
        values = info_index.values_by_folder.get(folder.resolve(), {})
        if key not in values:
            continue
        value = values[key]
        if match_empty:
            matched = not value.strip()
        elif exact:
            matched = value.strip().casefold() == needle
        else:
            matched = needle in value.casefold()
        if matched:
            matches.append(folder)
    return matches


def build_folder_info_index(info_paths: Iterable[Path]) -> FolderInfoIndex:
    keys: set[str] = set()
    editable_keys: set[str] = set()
    values_by_folder: dict[Path, dict[str, str]] = {}
    editable_values_by_folder: dict[Path, dict[str, str]] = {}
    for info_path in sorted(info_paths, key=lambda path: str(path).lower()):
        if not info_path.is_file() or not is_info_file(info_path):
            continue
        try:
            with info_path.open("r", encoding="utf-8") as file:
                info = normalise_info_mapping(json.load(file))
        except (OSError, ValueError, json.JSONDecodeError):
            continue

        editable_values = {key.strip(): value for key, value in info.editable.items() if key.strip()}
        readonly_values = {key.strip(): value for key, value in info.readonly.items() if key.strip()}
        values = {**editable_values, **readonly_values}
        if not values:
            continue
        folder = info_path.parent.resolve()
        editable_values_by_folder[folder] = editable_values
        values_by_folder[folder] = values
        editable_keys.update(editable_values)
        keys.update(values)

    return FolderInfoIndex(keys, editable_keys, values_by_folder, editable_values_by_folder)


def h5_time_range_seconds_from_file(h5_file: h5py.File) -> tuple[float, float] | None:
    indexed_range = h5_root_time_range_seconds(h5_file)
    if indexed_range is not None:
        return indexed_range

    series_root = h5_file.get("series")
    if series_root is None:
        return None
    minimum: float | None = None
    maximum: float | None = None

    def collect(_name: str, group: h5py.Group) -> None:
        nonlocal minimum, maximum
        if not isinstance(group, h5py.Group) or "value" not in group:
            return
        time_dataset_name = h5_time_dataset_name(group)
        if time_dataset_name is None:
            return
        time_dataset = group[time_dataset_name]
        if len(time_dataset) == 0:
            return
        multiplier = time_unit_seconds_multiplier(
            attribute_text(group.attrs.get("time_unit", "")),
            time_dataset_name,
        )
        try:
            first_time = float(time_dataset[0]) * multiplier
            last_time = float(time_dataset[len(time_dataset) - 1]) * multiplier
        except (TypeError, ValueError, IndexError):
            return
        if not math.isfinite(first_time) or not math.isfinite(last_time):
            return
        start, end = sorted((first_time, last_time))
        minimum = start if minimum is None else min(minimum, start)
        maximum = end if maximum is None else max(maximum, end)

    series_root.visititems(collect)
    return None if minimum is None or maximum is None else (minimum, maximum)


def h5_root_time_range_seconds(h5_file: h5py.File) -> tuple[float, float] | None:
    start = optional_float(h5_file.attrs.get("time_start_seconds"))
    end = optional_float(h5_file.attrs.get("time_end_seconds"))
    if start is None or end is None:
        return None
    return (start, end) if end >= start else None


def format_log_length(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0:
        return ""
    if seconds >= 3600:
        whole_seconds = round(seconds)
        hours, remainder = divmod(whole_seconds, 3600)
        minutes, seconds_part = divmod(remainder, 60)
        return f"{hours}:{minutes:02d}:{seconds_part:02d}"
    if seconds >= 60:
        minutes, seconds_part = divmod(round(seconds), 60)
        return f"{minutes}:{seconds_part:02d}"
    precision = 1 if seconds >= 10 else 2
    return f"{seconds:.{precision}f}".rstrip("0").rstrip(".") + "s"


def folder_info_group_path(folder: Path, key: str, values_by_folder: dict[Path, dict[str, str]]) -> tuple[str, ...]:
    values = values_by_folder.get(folder.resolve(), {})
    canonical_key = normalise_info_key(key)
    value = next(
        (candidate_value for candidate_key, candidate_value in values.items()
         if normalise_info_key(candidate_key) == canonical_key),
        None,
    )
    if value is None:
        return ("(missing)",)
    return (" ".join(value.split()) or "(empty)",)


def h5_time_dataset_name(group: h5py.Group) -> str | None:
    configured_name = attribute_text(group.attrs.get("time_dataset", "")).strip()
    return configured_name if configured_name and configured_name in group else None


def calculate_sample_rate_hz(group: h5py.Group, sample_count: int) -> float | None:
    time_dataset_name = h5_time_dataset_name(group)
    if time_dataset_name is None:
        return None
    time_dataset = group[time_dataset_name]
    count = min(sample_count, len(time_dataset))
    if count < 2:
        return None
    try:
        first_time = float(time_dataset[0])
        last_time = float(time_dataset[count - 1])
    except (TypeError, ValueError, IndexError):
        return None
    duration_seconds = (last_time - first_time) * time_unit_seconds_multiplier(
        attribute_text(group.attrs.get("time_unit", "")),
        time_dataset_name,
    )
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        return None
    sample_rate = (count - 1) / duration_seconds
    return sample_rate if math.isfinite(sample_rate) and sample_rate > 0 else None


def time_unit_seconds_multiplier(time_unit: str, _time_dataset_name: str) -> float:
    normalized = time_unit.strip().lower()
    if normalized in {"s", "sec", "secs", "second", "seconds"}:
        return 1.0
    if normalized in {"ms", "millisecond", "milliseconds"}:
        return 0.001
    if normalized in {"us", "microsecond", "microseconds"}:
        return 0.000001
    if normalized in {"ns", "nanosecond", "nanoseconds"}:
        return 0.000000001
    return 1.0


def format_sample_rate(sample_rate_hz: float | None) -> str:
    if sample_rate_hz is None or not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        return "n/a Hz"
    rounded = round(sample_rate_hz)
    if abs(sample_rate_hz - rounded) < 0.01:
        return f"{rounded}Hz"
    if sample_rate_hz >= 100:
        return f"{sample_rate_hz:.0f}Hz"
    if sample_rate_hz >= 10:
        return f"{sample_rate_hz:.1f}".rstrip("0").rstrip(".") + "Hz"
    if sample_rate_hz >= 1:
        return f"{sample_rate_hz:.2f}".rstrip("0").rstrip(".") + "Hz"
    return f"{sample_rate_hz:.3g}Hz"


def attribute_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
