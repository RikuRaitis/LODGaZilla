"""Generic data import entry point.

This module selects a format-specific importer from human-readable JSON
configuration files and writes a plotting-oriented HDF5 cache. The HDF5 layout
keeps raw series and multiresolution LOD buckets together so the GUI can choose
the cheapest representation for the visible time span.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Iterable

import numpy as np

from src.data.time_units import seconds_per_time_unit

from src.app.settings import PLUGINS_SECTION, load_hdf5_settings, load_settings_section
from src.data.parser_base import DataParser, read_json_file
from src.plugins.catalog import PLUGIN_ROOT
from src.plugins.loader import load_plugin_class
from src.version import APP_VERSION


SCHEMA_VERSION = "0.1"
CACHE_TOOL_NAME = "universal import and plotting tool"
DEFAULT_IMPORTERS_DIR = PLUGIN_ROOT
DEFAULT_CONFIG_DIR = DEFAULT_IMPORTERS_DIR
SERIES_INDEX_ATTR = "series_index_json"
LOG_START_FILENAME_PATTERN = re.compile(r"(?<!\d)(20\d{6})[_-](\d{6})(?!\d)")


@dataclass(frozen=True)
class ImportConfiguration:
    """One importer-plugin configuration and its Python importer instance."""

    path: Path
    parser: dict[str, Any]
    parser_instance: DataParser | None = None
    hdf5_config: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        if self.parser_instance is not None:
            return self.parser_instance.name or self.path.stem
        return str(self.parser.get("name", self.path.stem))

    @property
    def module_name(self) -> str:
        return self.path.with_suffix(".py").name

    @property
    def priority(self) -> int:
        if self.parser_instance is not None:
            return self.parser_instance.priority
        return int(self.parser.get("priority", 1000))

    @property
    def extensions(self) -> set[str]:
        if self.parser_instance is not None:
            return set(self.parser_instance.extensions)
        raw_extensions = self.parser.get("extensions", [])
        if not isinstance(raw_extensions, list):
            return set()
        return {str(ext).strip().lower() for ext in raw_extensions if str(ext).strip()}

    @property
    def hdf5_settings(self) -> Any:
        if self.hdf5_config is not None:
            if isinstance(self.hdf5_config.get("hdf5"), dict):
                return self.hdf5_config["hdf5"]
            return self.hdf5_config
        return {}


def load_import_configurations(
    config_dir: Path = DEFAULT_IMPORTERS_DIR,
    *,
    enabled_plugins: dict[str, bool] | None = None,
    hdf5_settings: dict[str, Any] | None = None,
) -> list[ImportConfiguration]:
    """Load plugins using the caller's selection, or saved settings for CLI use."""

    from src.plugins.catalog import discover_plugins
    hdf5_config = dict(hdf5_settings) if hdf5_settings is not None else load_hdf5_settings()
    if enabled_plugins is None:
        plugin_settings = load_settings_section(PLUGINS_SECTION)
        enabled = plugin_settings.get("enabled", {}) if isinstance(plugin_settings, dict) else {}
    else:
        enabled = enabled_plugins
    configurations = []
    for descriptor in discover_plugins(config_dir):
        if descriptor.error or "importer" not in descriptor.capabilities or not enabled.get(descriptor.plugin_id, False):
            continue
        try:
            configurations.append(load_import_configuration(
                descriptor.path.parent / "importer.json", hdf5_config=hdf5_config,
                parser_module_path=descriptor.entry_point,
            ))
        except Exception as error:  # noqa: BLE001 - one broken plugin must not hide other importers.
            import logging
            logging.getLogger(__name__).warning("Importer %s unavailable: %s", descriptor.plugin_id, error)

    return sorted(configurations, key=lambda item: item.priority)


def load_import_configuration(
    config_path: Path,
    *,
    hdf5_config: dict[str, Any] | None = None,
    parser_module_path: Path | None = None,
) -> ImportConfiguration:
    """Load one compact importer configuration and its implementation beside it."""

    config_path = config_path.resolve()
    parser_config = read_json_file(config_path)
    if parser_module_path is None and (config_path.parent / "plugin.json").is_file():
        # Spawned workers reload from the config path; preserve the manifest's
        # entry point even when it is not named importer.py.
        from src.plugins.catalog import local_file
        manifest = read_json_file(config_path.parent / "plugin.json")
        parser_module_path = local_file(config_path.parent, manifest["entry_point"])
    parser_module_path = parser_module_path or config_path.with_suffix(".py")
    parser_class = load_parser_class(parser_module_path)
    parser_instance = parser_class(config_path, parser_config)
    return ImportConfiguration(
        config_path,
        parser_config,
        parser_instance=parser_instance,
        hdf5_config=hdf5_config or load_hdf5_settings(),
    )


def load_parser_class(parser_module_path: Path) -> type[DataParser]:
    return load_plugin_class(
        parser_module_path,
        class_name="Parser",
        base_class=DataParser,
        module_namespace="universal_parser",
    )


def select_importer(
    input_path: Path,
    configurations: Iterable[ImportConfiguration],
) -> tuple[ImportConfiguration, Any]:
    """Find the first parser that accepts the input file.

    Parser extensions are only a detection hint. They move likely parsers
    earlier in the probing order, but every enabled importer still gets a chance
    to inspect the file contents with can_parse().
    """

    suffix = input_path.suffix.lower()
    checked: list[str] = []
    ordered_configurations = sorted(
        list(configurations),
        key=lambda configuration: (
            0 if not configuration.extensions or suffix in configuration.extensions else 1,
            configuration.priority,
        ),
    )

    for configuration in ordered_configurations:
        parser_instance = configuration.parser_instance
        if parser_instance is None:
            checked.append(f"{configuration.name}: missing parser instance")
            continue

        try:
            accepted = parser_instance.can_parse(input_path)
        except Exception as error:  # noqa: BLE001 - one plugin's error must not stop the others.
            checked.append(f"{configuration.name}: can_parse() raised {type(error).__name__}: {error}")
            continue
        if accepted:
            return configuration, parser_instance

        extension_note = ""
        if configuration.extensions and suffix not in configuration.extensions:
            extension_note = f" after extension hint mismatch {suffix!r}"
        checked.append(f"{configuration.name}: can_parse() returned false{extension_note}")

    details = "; ".join(checked) if checked else "no enabled import configurations found"
    raise ValueError(f"No configured importer accepts {input_path}. Checked: {details}")


def default_output_path(input_path: Path) -> Path:
    """Return the default same-folder HDF5 output path for an input file."""

    return input_path.with_suffix(".h5")


def ensure_replaceable_cache(output_path: Path, input_path: Path) -> None:
    """Refuse to replace an existing H5 unless it is this tool's cache of ``input_path``.

    Folders can hold original H5 files from other tools next to sources with
    the same stem, so an existing target must name this tool and the source
    file. The stored source is compared by file name so moved folders still match.
    """
    if not output_path.exists():
        return
    try:
        import h5py

        with h5py.File(output_path, "r") as h5_file:
            tool = _attribute_text(h5_file.attrs.get("tool"))
            source = _attribute_text(h5_file.attrs.get("source_file"))
    except Exception as error:  # noqa: BLE001 - any unreadable target is not ours to replace.
        raise FileExistsError(
            f"Not replacing {output_path}: it is not a readable H5 cache ({error}). "
            f"Move or rename it to generate the cache for {input_path.name}."
        ) from error
    if tool != CACHE_TOOL_NAME or PureWindowsPath(source).name.casefold() != input_path.name.casefold():
        raise FileExistsError(
            f"Not replacing {output_path}: it was not generated from {input_path.name} by this application. "
            f"Move or rename it to generate the cache."
        )


def _attribute_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return "" if value is None else str(value)


def log_start_from_source_filename(input_path: Path) -> datetime | None:
    """Return the local log-start time encoded as YYYYMMDD_HHMMSS in a name."""
    match = LOG_START_FILENAME_PATTERN.search(input_path.stem)
    if match is None:
        return None
    try:
        parsed = datetime.strptime("_".join(match.groups()), "%Y%m%d_%H%M%S")
    except ValueError:
        return None
    # Device log names are local wall-clock values. Store the UTC offset that
    # applied on the log's own date (not today's), so the H5 cache remains
    # unambiguous when moved elsewhere and across DST changes.
    return parsed.astimezone()


def import_file(
    input_file: str | Path,
    *,
    config_dir: str | Path = DEFAULT_CONFIG_DIR,
    output_file: str | Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Import one file to HDF5 and return the output path."""

    input_path = Path(input_file).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")

    configurations = load_import_configurations(Path(config_dir))
    selected_config, importer_module = select_importer(input_path, configurations)

    output_path = Path(output_file).resolve() if output_file else default_output_path(input_path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Output file already exists: {output_path}. Use --force to overwrite it."
        )

    result = importer_module.parse(input_path)
    write_hdf5_cache(
        output_path=output_path,
        input_path=input_path,
        selected_config=selected_config,
        import_result=result,
        replace_any=overwrite,
    )
    return output_path


def write_hdf5_cache(
    *,
    output_path: Path,
    input_path: Path,
    selected_config: ImportConfiguration,
    import_result: dict[str, Any],
    progress_callback: Callable[[float], None] | None = None,
    replace_any: bool = False,
) -> None:
    """Write a complete cache beside the destination, then atomically replace it.

    An existing destination is replaced only when it is this tool's cache of
    ``input_path``, unless ``replace_any`` is set (the CLI's ``--force``).
    """

    if not replace_any:
        ensure_replaceable_cache(output_path, input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    try:
        temporary_progress = None
        if progress_callback is not None:
            temporary_progress = lambda fraction: progress_callback(min(0.99, max(0.0, fraction)))
        _write_hdf5_cache_file(
            output_path=temporary_path,
            input_path=input_path,
            selected_config=selected_config,
            import_result=import_result,
            progress_callback=temporary_progress,
        )
        if not replace_any:
            # The target may have appeared while the cache was being written.
            ensure_replaceable_cache(output_path, input_path)
        os.replace(temporary_path, output_path)
        if progress_callback is not None:
            progress_callback(1.0)
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_hdf5_cache_file(
    *,
    output_path: Path,
    input_path: Path,
    selected_config: ImportConfiguration,
    import_result: dict[str, Any],
    progress_callback: Callable[[float], None] | None = None,
) -> None:
    """Write one plotting-oriented HDF5 file at the supplied path."""

    try:
        import h5py
    except ImportError as exc:
        raise RuntimeError(
            "h5py is required to write HDF5 import caches. "
            "Install dependencies with: python -m pip install -r requirements.txt"
        ) from exc

    hdf5_settings = selected_config.hdf5_settings
    compression = _none_if_blank(_get_mapping_value(hdf5_settings, "compression", "gzip"))
    compression_level = _optional_int(_get_mapping_value(hdf5_settings, "compression_level", "4"))
    chunk_rows = max(1, int(_get_mapping_value(hdf5_settings, "chunk_rows", "65536")))
    lod_target_buckets = max(1, int(_get_mapping_value(hdf5_settings, "lod_target_buckets", "2000")))
    lod_bucket_growth = max(2, int(_get_mapping_value(hdf5_settings, "lod_bucket_growth", "4")))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    series_by_name = import_result.get("series", {})
    if not series_by_name:
        raise ValueError("Importer did not produce any numeric series.")

    ordered_series = sorted(series_by_name.items())
    series_weights = {
        series_name: _estimate_series_write_weight(
            series_data,
            target_bucket_count=lod_target_buckets,
            bucket_growth=lod_bucket_growth,
        )
        for series_name, series_data in ordered_series
    }
    total_weight = max(1, sum(series_weights.values()))
    completed_weight = 0.0

    def report_progress(fraction: float) -> None:
        if progress_callback is not None:
            progress_callback(max(0.0, min(1.0, fraction)))

    report_progress(0.0)

    with h5py.File(output_path, "w") as h5_file:
        h5_file.attrs["schema_version"] = SCHEMA_VERSION
        h5_file.attrs["tool"] = CACHE_TOOL_NAME
        h5_file.attrs["tool_version"] = APP_VERSION
        h5_file.attrs["created_utc"] = datetime.now(timezone.utc).isoformat()
        h5_file.attrs["source_file"] = str(input_path)
        log_start = log_start_from_source_filename(input_path)
        if log_start is not None:
            h5_file.attrs["log_start_datetime"] = log_start.isoformat()
            h5_file.attrs["log_start_unix_seconds"] = log_start.timestamp()
        h5_file.attrs["source_size_bytes"] = input_path.stat().st_size
        h5_file.attrs["config_file"] = str(selected_config.path)
        h5_file.attrs["config_name"] = selected_config.name
        h5_file.attrs["importer"] = selected_config.module_name
        h5_file.attrs["importer_format"] = import_result.get("format", "unknown")
        h5_file.attrs["metadata_json"] = json.dumps(import_result.get("metadata", {}), sort_keys=True)
        series_root = h5_file.create_group("series")
        written_series = 0
        series_index: list[dict[str, Any]] = []
        file_time_start_seconds: float | None = None
        file_time_end_seconds: float | None = None
        for series_name, series_data in ordered_series:
            series_weight = series_weights[series_name]

            def report_series_progress(fraction: float) -> None:
                report_progress((completed_weight + series_weight * fraction) / total_weight)

            normalized = _normalise_series(series_data)
            if normalized is None:
                completed_weight += series_weight
                report_progress(completed_weight / total_weight)
                continue

            time_values, raw_values = normalized
            values, transform_metadata = transformed_series_values(
                series_name, series_data, series_by_name, raw_values
            )
            series_time_start_seconds, series_time_end_seconds = series_time_range_seconds(
                time_values,
                str(series_data.get("time_unit", "s")),
            )
            sample_count = len(values)
            sample_rate_hz = sample_rate_from_time_range(
                sample_count,
                series_time_start_seconds,
                series_time_end_seconds,
            )
            duration_seconds = duration_from_time_range(series_time_start_seconds, series_time_end_seconds)
            max_sample_interval_ms = optional_positive_float(series_data.get("max_sample_interval_ms"))
            sample_loss_ranges = detect_sample_loss_ranges(
                time_values,
                time_unit=str(series_data.get("time_unit", "s")),
                max_sample_interval_ms=max_sample_interval_ms,
            )
            min_value = optional_finite_float(series_data.get("min_value"))
            max_value = optional_finite_float(series_data.get("max_value"))
            min_value, max_value = transformed_clipping_limits(
                min_value, max_value, series_data.get("transform", {})
            )
            clipping_ranges = detect_clipping_ranges(
                time_values,
                values,
                min_value=min_value,
                max_value=max_value,
            )
            warning = warning_text(bool(sample_loss_ranges.size), bool(clipping_ranges.size))
            navigator_metadata = series_navigator_metadata(
                series_data.get("navigator_metadata", []),
                values,
                sample_count,
                sample_rate_hz,
                str(series_data.get("unit", "")),
                warning,
                transform_metadata,
            )
            lod_level_count = _estimate_lod_level_count(
                sample_count,
                target_bucket_count=lod_target_buckets,
                bucket_growth=lod_bucket_growth,
            )
            series_steps = max(1, 2 + lod_level_count)
            navigator_group = str(series_data.get("navigator_group", "Ungrouped"))
            navigator_name = str(series_data.get("navigator_name", series_name))
            group = series_root
            for path_part in (part for part in navigator_group.split("/") if part.strip()):
                group = group.require_group(_safe_hdf5_name(path_part))
            group = group.create_group(unique_hdf5_name(group, navigator_name))
            group.attrs["display_name"] = navigator_name
            group.attrs["unit"] = str(series_data.get("unit", ""))
            group.attrs["original_unit"] = str(series_data.get("original_unit", series_data.get("unit", "")))
            group.attrs["description"] = str(series_data.get("description", ""))
            group.attrs["source"] = str(series_data.get("source", ""))
            group.attrs["navigator_group"] = navigator_group
            group.attrs["navigator_name"] = navigator_name
            group.attrs["visible"] = bool(series_data.get("visible", True))
            group.attrs["state_labels_json"] = json.dumps(series_data.get("state_labels", {}), sort_keys=True)
            group.attrs["time_unit"] = str(series_data.get("time_unit", "s"))
            group.attrs["time_reference"] = str(series_data.get("time_reference", "unix"))
            group.attrs["sample_count"] = sample_count
            write_optional_float_attr(group.attrs, "sample_rate_hz", sample_rate_hz)
            write_optional_float_attr(group.attrs, "time_start_seconds", series_time_start_seconds)
            write_optional_float_attr(group.attrs, "time_end_seconds", series_time_end_seconds)
            write_optional_float_attr(group.attrs, "duration_seconds", duration_seconds)
            time_dataset_name = time_dataset_name_for_series(series_data)
            group.attrs["time_dataset"] = time_dataset_name
            if max_sample_interval_ms is not None:
                group.attrs["max_sample_interval_ms"] = max_sample_interval_ms
            write_optional_float_attr(group.attrs, "min_value", min_value)
            write_optional_float_attr(group.attrs, "max_value", max_value)
            for name, value in transform_metadata.items():
                group.attrs[f"transform_{name}"] = str(value)
            if series_time_start_seconds is not None and series_time_end_seconds is not None:
                file_time_start_seconds = (
                    series_time_start_seconds
                    if file_time_start_seconds is None
                    else min(file_time_start_seconds, series_time_start_seconds)
                )
                file_time_end_seconds = (
                    series_time_end_seconds
                    if file_time_end_seconds is None
                    else max(file_time_end_seconds, series_time_end_seconds)
                )
            series_index.append({
                "name": series_name,
                "display_name": navigator_name,
                "navigator_name": navigator_name,
                "navigator_group": navigator_group,
                "unit": str(series_data.get("unit", "")),
                "sample_count": sample_count,
                "sample_rate_hz": sample_rate_hz,
                "visible": bool(series_data.get("visible", True)),
                "time_start_seconds": series_time_start_seconds,
                "time_end_seconds": series_time_end_seconds,
                "duration_seconds": duration_seconds,
                "navigator_metadata": navigator_metadata,
                # The Navigator's warning column, so it need not walk the file for it.
                "diagnostic_warning": warning,
            })

            create_dataset(
                group,
                time_dataset_name,
                time_values,
                chunk_rows=chunk_rows,
                compression=compression,
                compression_level=compression_level,
            )
            report_series_progress(1 / series_steps)
            create_dataset(
                group,
                "value",
                values,
                chunk_rows=chunk_rows,
                compression=compression,
                compression_level=compression_level,
            )
            if values is raw_values:
                # No transform: the original values are the plotted ones. A hard link
                # (see H5_Format.md) saves a second copy and its compression time.
                group["value_raw"] = group["value"]
            else:
                create_dataset(
                    group,
                    "value_raw",
                    raw_values,
                    chunk_rows=chunk_rows,
                    compression=compression,
                    compression_level=compression_level,
                )
            write_sample_loss_list(
                group,
                sample_loss_ranges,
                time_dataset_name=time_dataset_name,
                chunk_rows=chunk_rows,
                compression=compression,
                compression_level=compression_level,
            )
            write_clipping_list(
                group,
                clipping_ranges,
                time_dataset_name=time_dataset_name,
                chunk_rows=chunk_rows,
                compression=compression,
                compression_level=compression_level,
            )
            report_series_progress(2 / series_steps)
            create_lod_levels(
                group,
                time_values,
                values,
                time_dataset_name=time_dataset_name,
                target_bucket_count=lod_target_buckets,
                bucket_growth=lod_bucket_growth,
                chunk_rows=chunk_rows,
                compression=compression,
                compression_level=compression_level,
                progress_callback=(
                    lambda fraction, steps=series_steps: report_series_progress(
                        (2 + fraction * max(0, steps - 2)) / steps
                    )
                ),
            )
            written_series += 1
            completed_weight += series_weight
            report_progress(completed_weight / total_weight)

        h5_file.attrs["series_count"] = written_series
        h5_file.attrs[SERIES_INDEX_ATTR] = json.dumps(series_index, sort_keys=True)
        write_optional_float_attr(h5_file.attrs, "time_start_seconds", file_time_start_seconds)
        write_optional_float_attr(h5_file.attrs, "time_end_seconds", file_time_end_seconds)
        write_optional_float_attr(
            h5_file.attrs,
            "duration_seconds",
            duration_from_time_range(file_time_start_seconds, file_time_end_seconds),
        )

    if written_series == 0:
        output_path.unlink(missing_ok=True)
        raise ValueError("Importer produced series, but none contained finite numeric samples.")

    report_progress(1.0)


def series_time_range_seconds(time_values: np.ndarray, time_unit: str) -> tuple[float | None, float | None]:
    if len(time_values) == 0:
        return None, None
    try:
        first_time = float(time_values[0]) * time_unit_seconds_multiplier(time_unit)
        last_time = float(time_values[len(time_values) - 1]) * time_unit_seconds_multiplier(time_unit)
    except (TypeError, ValueError, IndexError):
        return None, None
    if not math.isfinite(first_time) or not math.isfinite(last_time):
        return None, None
    start, end = sorted((first_time, last_time))
    return start, end


def sample_rate_from_time_range(
    sample_count: int,
    start_seconds: float | None,
    end_seconds: float | None,
) -> float | None:
    duration_seconds = duration_from_time_range(start_seconds, end_seconds)
    if sample_count < 2 or duration_seconds is None or duration_seconds <= 0:
        return None
    sample_rate = (sample_count - 1) / duration_seconds
    return sample_rate if math.isfinite(sample_rate) and sample_rate > 0 else None


def duration_from_time_range(start_seconds: float | None, end_seconds: float | None) -> float | None:
    if start_seconds is None or end_seconds is None:
        return None
    duration = end_seconds - start_seconds
    return duration if math.isfinite(duration) and duration >= 0 else None


def write_optional_float_attr(attrs: Any, name: str, value: float | None) -> None:
    if value is not None:
        attrs[name] = value


def series_navigator_metadata(
    definitions: Any,
    values: np.ndarray,
    sample_count: int,
    sample_rate_hz: float | None,
    unit: str,
    warning: str,
    transform_metadata: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(definitions, list):
        return []
    sources: dict[str, Any] = {
        "sample_count": sample_count,
        "sample_rate": sample_rate_hz,
        "min": finite_series_extreme(values, np.min),
        "max": finite_series_extreme(values, np.max),
        "unit": unit,
        "warning": warning,
        **(transform_metadata or {}),
    }
    metadata: list[dict[str, Any]] = []
    for definition in definitions:
        if not isinstance(definition, dict):
            continue
        column_id = str(definition.get("id", "")).strip()
        title = str(definition.get("title", column_id)).strip()
        source = str(definition.get("source", "")).strip()
        if not column_id or not title or source not in sources:
            continue
        metadata.append({
            "id": column_id,
            "title": title,
            "format": str(definition.get("format", "text")),
            "value": sources[source],
        })
    return metadata


def transformed_series_values(
    series_name: str,
    series_data: dict[str, Any],
    all_series: dict[str, Any],
    raw_values: np.ndarray,
) -> tuple[np.ndarray, dict[str, str]]:
    """Apply the JSON-defined affine transform without modifying raw data."""

    transform = series_data.get("transform", {})
    if not isinstance(transform, dict) or not transform:
        return raw_values, {"original_axis": str(series_data.get("navigator_name", series_name)), "formula": "raw"}
    source_name = str(transform.get("source", series_data.get("navigator_name", series_name))).strip()
    group = str(series_data.get("navigator_group", "")).strip()
    source_key = f"{group}/{source_name}" if group else source_name
    source_data = all_series.get(source_key, series_data)
    source_normalized = _normalise_series(source_data) if isinstance(source_data, dict) else None
    source_values = raw_values if source_normalized is None else source_normalized[1]
    if len(source_values) != len(raw_values):
        raise ValueError(f"Transform source {source_key!r} does not match {series_name!r} sample count.")
    scale = float(transform.get("scale", 1.0))
    offset = float(transform.get("offset", 0.0))
    values = np.asarray(source_values, dtype=np.float64) * scale + offset
    formula = str(transform.get("formula", f"{scale}*{source_name}+{offset}"))
    return values, {"original_axis": source_name, "formula": formula}


def transformed_clipping_limits(
    minimum: float | None, maximum: float | None, transform: Any
) -> tuple[float | None, float | None]:
    if minimum is None or maximum is None or not isinstance(transform, dict):
        return minimum, maximum
    scale = float(transform.get("scale", 1.0))
    offset = float(transform.get("offset", 0.0))
    return tuple(sorted((minimum * scale + offset, maximum * scale + offset)))  # type: ignore[return-value]


def finite_series_extreme(values: np.ndarray, operation: Callable[[np.ndarray], Any]) -> float | None:
    try:
        finite_values = values[np.isfinite(values)]
    except TypeError:
        return None
    if len(finite_values) == 0:
        return None
    value = float(operation(finite_values))
    return value if math.isfinite(value) else None


def time_unit_seconds_multiplier(time_unit: str) -> float:
    return seconds_per_time_unit(time_unit)


def optional_positive_float(value: Any) -> float | None:
    """Return a finite positive numeric configuration value, if supplied."""

    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def optional_finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def detect_sample_loss_ranges(
    time_values: np.ndarray,
    *,
    time_unit: str,
    max_sample_interval_ms: float | None,
) -> np.ndarray:
    """Return [last-before-gap, first-after-gap] ranges exceeding the limit."""

    if max_sample_interval_ms is None or len(time_values) < 2:
        return np.empty((0, 2), dtype=np.float64)
    native_limit = (max_sample_interval_ms / 1_000.0) / time_unit_seconds_multiplier(time_unit)
    differences = np.diff(time_values)
    gap_indices = np.flatnonzero(differences > native_limit)
    if len(gap_indices) == 0:
        return np.empty((0, 2), dtype=np.float64)
    return np.column_stack((time_values[gap_indices], time_values[gap_indices + 1]))


def write_sample_loss_list(
    series_group: Any,
    sample_loss_ranges: np.ndarray,
    *,
    time_dataset_name: str,
    chunk_rows: int,
    compression: str | None,
    compression_level: int | None,
) -> None:
    """Store sample-loss failures and native-time ranges beside raw data."""

    failures = series_group.create_group("sample_loss_list")
    failures.attrs["messages_json"] = json.dumps(
        ["Sampleloss detected"] if len(sample_loss_ranges) else []
    )
    failures.attrs["sample_loss_count"] = len(sample_loss_ranges)
    if len(sample_loss_ranges) == 0:
        return
    suffix = time_dataset_name.removeprefix("time_")
    create_dataset(
        failures,
        f"sample_loss_start_{suffix}" if suffix else "sample_loss_start",
        sample_loss_ranges[:, 0],
        chunk_rows=chunk_rows,
        compression=compression,
        compression_level=compression_level,
    )
    create_dataset(
        failures,
        f"sample_loss_stop_{suffix}" if suffix else "sample_loss_stop",
        sample_loss_ranges[:, 1],
        chunk_rows=chunk_rows,
        compression=compression,
        compression_level=compression_level,
    )


def detect_clipping_ranges(
    time_values: np.ndarray,
    values: np.ndarray,
    *,
    min_value: float | None,
    max_value: float | None,
) -> np.ndarray:
    """Return contiguous native-time ranges containing clipped samples."""

    if (min_value is None and max_value is None) or len(time_values) == 0:
        return np.empty((0, 2), dtype=np.float64)
    clipped = np.zeros(len(values), dtype=bool)
    if min_value is not None:
        clipped |= values <= min_value
    if max_value is not None:
        clipped |= values >= max_value
    clipped &= np.isfinite(values)
    indices = np.flatnonzero(clipped)
    if len(indices) == 0:
        return np.empty((0, 2), dtype=np.float64)
    range_starts = indices[np.r_[True, np.diff(indices) != 1]]
    range_stops = indices[np.r_[np.diff(indices) != 1, True]]
    return np.column_stack((time_values[range_starts], time_values[range_stops]))


def write_clipping_list(
    series_group: Any,
    clipping_ranges: np.ndarray,
    *,
    time_dataset_name: str,
    chunk_rows: int,
    compression: str | None,
    compression_level: int | None,
) -> None:
    """Store clipping failures in the same native-time format as sample loss."""

    clipping = series_group.create_group("clipping_list")
    clipping.attrs["messages_json"] = json.dumps(
        ["Clipping detected"] if len(clipping_ranges) else []
    )
    clipping.attrs["clipping_count"] = len(clipping_ranges)
    if len(clipping_ranges) == 0:
        return
    suffix = time_dataset_name.removeprefix("time_")
    create_dataset(
        clipping,
        f"clipping_start_{suffix}" if suffix else "clipping_start",
        clipping_ranges[:, 0],
        chunk_rows=chunk_rows,
        compression=compression,
        compression_level=compression_level,
    )
    create_dataset(
        clipping,
        f"clipping_stop_{suffix}" if suffix else "clipping_stop",
        clipping_ranges[:, 1],
        chunk_rows=chunk_rows,
        compression=compression,
        compression_level=compression_level,
    )


def warning_text(has_sample_loss: bool, has_clipping: bool) -> str:
    if has_sample_loss and has_clipping:
        return "Sampleloss and Clipping detected"
    if has_sample_loss:
        return "Sampleloss detected"
    if has_clipping:
        return "Clipping detected"
    return ""


def create_dataset(
    group: Any,
    name: str,
    values: np.ndarray,
    *,
    chunk_rows: int,
    compression: str | None,
    compression_level: int | None,
) -> Any:
    """Create one compressed, chunked one-dimensional HDF5 dataset."""

    chunks = (min(chunk_rows, max(1, len(values))),)
    kwargs: dict[str, Any] = {
        "data": values,
        "chunks": chunks,
        "shuffle": True,
    }
    if compression:
        kwargs["compression"] = compression
        if compression == "gzip" and compression_level is not None:
            kwargs["compression_opts"] = compression_level

    return group.create_dataset(name, **kwargs)


def create_lod_levels(
    series_group: Any,
    time_values: np.ndarray,
    values: np.ndarray,
    *,
    time_dataset_name: str = "time_unix",
    target_bucket_count: int,
    bucket_growth: int,
    chunk_rows: int,
    compression: str | None,
    compression_level: int | None,
    progress_callback: Callable[[float], None] | None = None,
) -> None:
    """Create min/max LOD buckets until the top level has <= target buckets."""

    sample_count = len(values)
    lod_group = series_group.create_group("lod")
    if sample_count <= target_bucket_count:
        lod_group.attrs["levels"] = 0
        if progress_callback is not None:
            progress_callback(1.0)
        return

    total_levels = _estimate_lod_level_count(
        sample_count,
        target_bucket_count=target_bucket_count,
        bucket_growth=bucket_growth,
    )
    level = 0
    bucket_size = 1
    lod_arrays: dict[str, np.ndarray] | None = None
    while True:
        bucket_size *= bucket_growth
        bucket_count = math.ceil(sample_count / bucket_size)
        level += 1
        if progress_callback is not None:
            progress_callback((level - 1) / total_levels)

        level_group = lod_group.create_group(f"level_{level:03d}")
        level_group.attrs["source_sample_count"] = sample_count
        level_group.attrs["bucket_size_samples"] = bucket_size
        level_group.attrs["bucket_count"] = bucket_count

        # Only the first level reads the raw samples (in blocks); each higher level
        # combines `bucket_growth` buckets of the level below, with the same result.
        lod_arrays = (
            build_lod_arrays_in_blocks(time_values, values, bucket_size, time_dataset_name=time_dataset_name)
            if lod_arrays is None
            else combine_lod_arrays(lod_arrays, bucket_growth, time_dataset_name=time_dataset_name)
        )
        if progress_callback is not None:
            progress_callback((level - 0.5) / total_levels)
        for dataset_name, dataset_values in lod_arrays.items():
            create_dataset(
                level_group,
                dataset_name,
                dataset_values,
                chunk_rows=chunk_rows,
                compression=compression,
                compression_level=compression_level,
            )
        if progress_callback is not None:
            progress_callback(level / total_levels)

        if bucket_count <= target_bucket_count:
            lod_group.attrs["levels"] = level
            lod_group.attrs["top_level"] = f"level_{level:03d}"
            break


def build_lod_arrays(
    time_values: np.ndarray,
    values: np.ndarray,
    bucket_size: int,
    *,
    time_dataset_name: str = "time_unix",
) -> dict[str, np.ndarray]:
    """Build chronological min/max plotting buckets for one LOD level."""

    starts = np.arange(0, len(values), bucket_size, dtype=np.int64)
    ends = np.minimum(starts + bucket_size, len(values))
    last_indices = ends - 1
    bucket_count = len(starts)
    padded_length = bucket_count * bucket_size
    pad_count = padded_length - len(values)

    finite_values = np.isfinite(values) if values.dtype.kind == "f" else np.ones(len(values), dtype=bool)
    padded_finite = np.pad(finite_values, (0, pad_count), constant_values=False)
    finite_count = padded_finite.reshape(bucket_count, bucket_size).sum(axis=1).astype(np.int64)

    if values.dtype.kind == "f":
        min_fill = np.inf
        max_fill = -np.inf
        min_source = np.where(finite_values, values, min_fill)
        max_source = np.where(finite_values, values, max_fill)
    elif values.dtype.kind == "b":
        min_fill = True
        max_fill = False
        min_source = values
        max_source = values
    else:
        info = np.iinfo(values.dtype)
        min_fill = info.max
        max_fill = info.min
        min_source = values
        max_source = values

    padded_min_source = np.pad(min_source, (0, pad_count), constant_values=min_fill)
    padded_max_source = np.pad(max_source, (0, pad_count), constant_values=max_fill)
    min_matrix = padded_min_source.reshape(bucket_count, bucket_size)
    max_matrix = padded_max_source.reshape(bucket_count, bucket_size)

    min_offsets = np.argmin(min_matrix, axis=1).astype(np.int64)
    max_offsets = np.argmax(max_matrix, axis=1).astype(np.int64)
    min_indices = starts + min_offsets
    max_indices = starts + max_offsets
    value_min = min_matrix[np.arange(bucket_count), min_offsets]
    value_max = max_matrix[np.arange(bucket_count), max_offsets]
    time_min = time_values[np.minimum(min_indices, len(time_values) - 1)]
    time_max = time_values[np.minimum(max_indices, len(time_values) - 1)]

    empty_buckets = finite_count == 0
    if values.dtype.kind == "f" and np.any(empty_buckets):
        value_min[empty_buckets] = np.nan
        value_max[empty_buckets] = np.nan
        time_min[empty_buckets] = np.nan
        time_max[empty_buckets] = np.nan
        min_indices[empty_buckets] = -1
        max_indices[empty_buckets] = -1

    return {
        lod_time_dataset_name("start", time_dataset_name): time_values[starts],
        lod_time_dataset_name("end", time_dataset_name): time_values[last_indices],
        lod_time_dataset_name("min", time_dataset_name): time_min,
        lod_time_dataset_name("max", time_dataset_name): time_max,
        "value_min": value_min,
        "value_max": value_max,
        "value_first": values[starts],
        "value_last": values[last_indices],
        "sample_index_min": min_indices,
        "sample_index_max": max_indices,
        "count": (ends - starts).astype(np.int64),
        "finite_count": finite_count,
    }


# Raw samples per block when building the first LOD level, so its temporaries stay bounded.
LOD_BLOCK_SAMPLES = 4_194_304


def build_lod_arrays_in_blocks(
    time_values: np.ndarray,
    values: np.ndarray,
    bucket_size: int,
    *,
    time_dataset_name: str = "time_unix",
    block_samples: int = LOD_BLOCK_SAMPLES,
) -> dict[str, np.ndarray]:
    """``build_lod_arrays`` over blocks of whole buckets, joined; the same arrays, less memory."""
    block = max(1, block_samples // bucket_size) * bucket_size
    if len(values) <= block:
        return build_lod_arrays(time_values, values, bucket_size, time_dataset_name=time_dataset_name)
    parts = []
    for offset in range(0, len(values), block):
        part = build_lod_arrays(time_values[offset:offset + block], values[offset:offset + block], bucket_size,
                                time_dataset_name=time_dataset_name)
        for name in ("sample_index_min", "sample_index_max"):
            indices = part[name]
            part[name] = np.where(indices >= 0, indices + offset, indices)
        parts.append(part)
    return {name: np.concatenate([part[name] for part in parts]) for name in parts[0]}


def combine_lod_arrays(
    lower: dict[str, np.ndarray],
    growth: int,
    *,
    time_dataset_name: str = "time_unix",
) -> dict[str, np.ndarray]:
    """The next LOD level: each bucket combines ``growth`` consecutive buckets of ``lower``.

    Identical to ``build_lod_arrays`` from the raw samples at ``growth`` times the bucket
    size, since buckets of every level start at multiples of their size; empty buckets
    (no finite values) are NaN with index -1, as there.
    """
    names = {kind: lod_time_dataset_name(kind, time_dataset_name) for kind in ("start", "end", "min", "max")}
    lower_count = len(lower["count"])
    starts = np.arange(0, lower_count, growth, dtype=np.int64)
    lasts = np.minimum(starts + growth, lower_count) - 1
    finite_count = np.add.reduceat(lower["finite_count"], starts)
    value_min, value_max = lower["value_min"], lower["value_max"]
    floating = value_min.dtype.kind == "f"
    bucket_count = len(starts)
    pad = bucket_count * growth - lower_count

    def pick(source: np.ndarray, fill, choose) -> np.ndarray:  # type: ignore[no-untyped-def]
        """Each new bucket's index into ``lower`` of its extreme (first one on ties)."""
        if floating:
            source = np.where(np.isnan(source), fill, source)
        matrix = np.pad(source, (0, pad), constant_values=fill).reshape(bucket_count, growth)
        return starts + choose(matrix, axis=1)

    if floating:
        min_fill, max_fill = np.inf, -np.inf
    elif value_min.dtype.kind == "b":
        min_fill, max_fill = True, False
    else:
        info = np.iinfo(value_min.dtype)
        min_fill, max_fill = info.max, info.min
    min_from = np.minimum(pick(value_min, min_fill, np.argmin), lower_count - 1)
    max_from = np.minimum(pick(value_max, max_fill, np.argmax), lower_count - 1)
    combined = {
        names["start"]: lower[names["start"]][starts],
        names["end"]: lower[names["end"]][lasts],
        names["min"]: lower[names["min"]][min_from],
        names["max"]: lower[names["max"]][max_from],
        "value_min": value_min[min_from],
        "value_max": value_max[max_from],
        "value_first": lower["value_first"][starts],
        "value_last": lower["value_last"][lasts],
        "sample_index_min": lower["sample_index_min"][min_from],
        "sample_index_max": lower["sample_index_max"][max_from],
        "count": np.add.reduceat(lower["count"], starts).astype(np.int64),
        "finite_count": finite_count.astype(np.int64),
    }
    empty = finite_count == 0
    if floating and np.any(empty):
        for name in ("value_min", "value_max", names["min"], names["max"]):
            combined[name] = combined[name].copy()
            combined[name][empty] = np.nan
        for name in ("sample_index_min", "sample_index_max"):
            combined[name] = combined[name].copy()
            combined[name][empty] = -1
    return combined


def time_dataset_name_for_series(series_data: dict[str, Any]) -> str:
    """Choose an explicit HDF5 time-axis name from configured time metadata."""

    configured_name = str(series_data.get("time_dataset", "")).strip()
    if configured_name:
        return configured_name

    time_unit = str(series_data.get("time_unit", "s")).strip().lower()
    time_reference = str(series_data.get("time_reference", "unix")).strip().lower()
    if time_unit == "ms":
        return "time_ms"
    if time_reference == "unix":
        return "time_unix"
    return "time"


def lod_time_dataset_name(kind: str, time_dataset_name: str) -> str:
    """Return the matching LOD time dataset name for a raw time dataset."""

    suffix = time_dataset_name.removeprefix("time_")
    return f"time_{kind}_{suffix}" if suffix else f"time_{kind}"


def _estimate_series_write_weight(
    series_data: dict[str, Any],
    *,
    target_bucket_count: int,
    bucket_growth: int,
) -> int:
    sample_count = _series_sample_count_hint(series_data)
    lod_level_count = _estimate_lod_level_count(
        sample_count,
        target_bucket_count=target_bucket_count,
        bucket_growth=bucket_growth,
    )
    return max(1, sample_count * max(1, 2 + lod_level_count))


def _series_sample_count_hint(series_data: dict[str, Any]) -> int:
    try:
        return max(1, len(series_data.get("values", [])))
    except TypeError:
        return 1


def _estimate_lod_level_count(
    sample_count: int,
    *,
    target_bucket_count: int,
    bucket_growth: int,
) -> int:
    if sample_count <= target_bucket_count:
        return 0

    level_count = 0
    bucket_size = 1
    while True:
        bucket_size *= bucket_growth
        bucket_count = math.ceil(sample_count / bucket_size)
        level_count += 1
        if bucket_count <= target_bucket_count:
            return level_count


def _normalise_series(series_data: dict[str, Any]) -> tuple[np.ndarray, np.ndarray] | None:
    time_values = np.asarray(series_data.get("time", []), dtype=np.float64)
    values = np.asarray(series_data.get("values", []))
    if values.dtype.kind not in {"b", "u", "i", "f"}:
        values = values.astype(np.float64)

    if len(time_values) != len(values):
        raise ValueError(
            f"Series {series_data.get('name', '<unnamed>')} has mismatched time/value lengths "
            f"({len(time_values)} != {len(values)})."
        )
    if len(values) == 0:
        return None

    finite = np.isfinite(time_values)
    if not np.any(finite):
        return None

    time_values = time_values[finite]
    values = values[finite]
    # Checked even when the parser says "time_sorted": a clock reset would otherwise give
    # wrong time ranges and lookups without notice. The check is one pass; sorting only if needed.
    if len(time_values) < 2 or not np.any(time_values[1:] < time_values[:-1]):
        return time_values, values

    order = np.argsort(time_values, kind="stable")
    return time_values[order], values[order]


def _get_mapping_value(mapping: Any, key: str, fallback: str) -> str:
    if hasattr(mapping, "get"):
        return str(mapping.get(key, fallback))
    return fallback


def _none_if_blank(value: str) -> str | None:
    value = value.strip()
    if not value or value.lower() in {"none", "false", "off"}:
        return None
    return value


def _optional_int(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    return int(value)


def _safe_hdf5_name(name: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return safe_name.strip("._") or "series"


def unique_hdf5_name(parent: Any, name: str) -> str:
    """A safe H5 name not yet used in ``parent``: names such as "Speed (km/h)" and
    "Speed [km/h]" both become ``Speed_km_h``, so the second gets ``Speed_km_h_2``.
    The Navigator shows a series by its ``navigator_name``, not this H5 name."""
    base = _safe_hdf5_name(name)
    candidate, number = base, 2
    while candidate in parent:
        candidate, number = f"{base}_{number}", number + 1
    return candidate


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import a data file to an HDF5 plotting cache.")
    parser.add_argument("input_file", nargs="?", type=Path, help="Input data file to import.")
    parser.add_argument(
        "-c",
        "--config-dir",
        type=Path,
        default=DEFAULT_CONFIG_DIR,
        help=f"Plugin tree containing importer.json/importer.py pairs. Default: {DEFAULT_CONFIG_DIR}",
    )
    parser.add_argument("-o", "--output", type=Path, help="Output HDF5 file. Default: same folder/name.h5")
    parser.add_argument("-f", "--force", action="store_true", help="Overwrite an existing output file.")
    parser.add_argument(
        "--list-importers",
        action="store_true",
        help="List enabled import configurations before importing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.list_importers:
        for configuration in load_import_configurations(args.config_dir):
            print(f"{configuration.priority:4d}  {configuration.name}  ({configuration.module_name})")
        if args.input_file is None:
            return 0

    if args.input_file is None:
        parser.error("input_file is required unless --list-importers is used.")

    output_path = import_file(
        args.input_file,
        config_dir=args.config_dir,
        output_file=args.output,
        overwrite=args.force,
    )
    print(f"Imported HDF5 cache: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
