"""Write a device's series as a CSV file and the matching LODGaZilla H5 cache, and folder info."""
from __future__ import annotations

import json
import os
from pathlib import Path

import h5py
import numpy as np

from src.app.settings import DEFAULT_HDF5_SETTINGS
from src.data.folder_info import FolderInfoData, write_info_file
from src.data.media import audio_output_path
from src.jobs.import_data import ImportConfiguration, _safe_hdf5_name, write_hdf5_cache
from src.jobs.media_import import MediaItem, convert_audio
from tools.demo_data.sensors import Series

GENERATOR = Path(__file__).resolve().parents[1] / "create_demo_dataset.py"
CSV_COLUMNS = "time_unix_s,time_s,series,value,unit"
# The Navigator's value columns, as importer plugins configure them.
NAVIGATOR_METADATA = [
    {"id": "sample_count", "title": "Sample count", "source": "sample_count", "format": "integer"},
    {"id": "sample_rate", "title": "Sample rate", "source": "sample_rate", "format": "rate"},
    {"id": "min", "title": "Min", "source": "min", "format": "number"},
    {"id": "max", "title": "Max", "source": "max", "format": "number"},
    {"id": "unit", "title": "Unit", "source": "unit", "format": "text"},
    {"id": "warning", "title": "Warning", "source": "warning", "format": "text"},
]
CSV_CHUNK_ROWS = 200_000


def series_metadata(item: Series) -> dict[str, str]:
    """Everything known about a series besides its samples, as text."""
    metadata = {"unit": item.unit, "description": item.description}
    if item.kind:
        metadata["kind"] = item.kind
    if item.labels:
        metadata["labels"] = ", ".join(f"{key} = {value}" for key, value in item.labels.items())
    if item.value_range is not None:
        metadata["clipping_limits"] = f"{item.value_range[0]:g} to {item.value_range[1]:g} {item.unit}".strip()
    if item.max_interval_ms is not None:
        metadata["max_sample_interval_ms"] = f"{item.max_interval_ms:g}"
    if not item.visible:
        metadata["visibility"] = "hidden"
    metadata.update(item.attributes)
    return metadata


def write_csv(path: Path, series: list[Series], log_start: float, session: dict[str, str]) -> Path:
    """One long-format CSV per device: a commented metadata header, then one row per sample."""
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("# LODGaZilla demo data. Synthetic, not a real recording.\n")
        for key, value in session.items():
            handle.write(f"# {key}: {value}\n")
        handle.write(f"# log_start_unix_s: {log_start:.3f}\n")
        handle.write("# time_unix_s is UTC seconds since 1970; time_s is seconds from the log start.\n")
        for item in series:
            fields = "; ".join(f"{key}={value}" for key, value in series_metadata(item).items())
            handle.write(f"# series {item.key}: {fields}\n")
        handle.write(CSV_COLUMNS + "\n")
        for item in series:
            for start in range(0, len(item.time), CSV_CHUNK_ROWS):
                times = item.time[start:start + CSV_CHUNK_ROWS]
                values = item.values[start:start + CSV_CHUNK_ROWS]
                suffix = f",{item.key},"
                handle.write("".join(
                    f"{log_start + t:.6f},{t:.6f}{suffix}{v:.7g},{item.unit}\n" for t, v in zip(times.tolist(), values.tolist())
                ))
    return path


def write_h5(h5_path: Path, csv_path: Path, series: list[Series], log_start: float, session: dict[str, str]) -> Path:
    """Write the cache with the application's own writer, then add the attributes it does not know."""
    import_result = {
        "format": "LODGaZilla demo CSV",
        "metadata": {**session, "log_start_unix_s": f"{log_start:.3f}"},
        "series": {item.key: {
            "unit": item.unit, "original_unit": item.unit, "description": item.description,
            "source": csv_path.name, "navigator_group": item.group, "navigator_name": item.name,
            "visible": item.visible, "time": log_start + np.asarray(item.time, dtype=np.float64),
            "values": np.asarray(item.values), "time_unit": "s", "time_reference": "unix", "time_sorted": True,
            "state_labels": item.labels, "navigator_metadata": NAVIGATOR_METADATA,
            **({"max_sample_interval_ms": item.max_interval_ms} if item.max_interval_ms is not None else {}),
            **({"min_value": item.value_range[0], "max_value": item.value_range[1]}
               if item.value_range is not None else {}),
        } for item in series},
    }
    configuration = ImportConfiguration(GENERATOR, {"name": "LODGaZilla demo data"},
                                        hdf5_config=dict(DEFAULT_HDF5_SETTINGS))
    write_hdf5_cache(output_path=h5_path, input_path=csv_path, selected_config=configuration,
                     import_result=import_result)
    with h5py.File(h5_path, "r+") as h5_file:
        h5_file.attrs["synthetic"] = True
        for key, value in session.items():
            h5_file.attrs[f"session_{key.replace(' ', '_')}"] = value
        for item in series:
            group = h5_file["series"][_safe_hdf5_name(item.group)][_safe_hdf5_name(item.name)]
            if item.kind:
                group.attrs["event_state_kind"] = item.kind
            for key, value in series_metadata(item).items():
                if key not in {"unit", "description"}:
                    group.attrs[key] = value
    return h5_path


def write_device(folder: Path, stem: str, series: list[Series], log_start: float, session: dict[str, str]) -> Path:
    csv_path = write_csv(folder / f"{stem}.csv", series, log_start, session)
    return write_h5(folder / f"{stem}.h5", csv_path, series, log_start, session)


def relative_link(folder: Path, path: Path) -> str:
    return Path(os.path.relpath(path.resolve(), folder.resolve())).as_posix()


def write_folder_info(folder: Path, editable: dict[str, str], serial: str, *, videos=(), images=(), audio=()) -> None:  # type: ignore[no-untyped-def]
    """``videos`` holds (path, offset seconds); ``audio`` holds (audio H5, source video, offset seconds)."""
    write_info_file(folder, FolderInfoData(
        editable=dict(editable), readonly={"serial number": serial},
        videos=[{"path": relative_link(folder, path), "offset_seconds": f"{offset:g}"} for path, offset in videos],
        images=[{"path": relative_link(folder, path)} for path in images],
        audio=[{"path": relative_link(folder, output), "source": relative_link(folder, video),
                "offset_seconds": f"{offset:g}", "video": relative_link(folder, video)}
               for output, video, offset in audio],
    ))


def convert_soundtrack(folder: Path, video: Path, log_start: float, offset: float) -> Path:
    """Make the video's audio H5, exactly as the application's process queue would."""
    output = audio_output_path(video, folder)
    convert_audio(MediaItem(str(video), str(output), log_start, offset), dict(DEFAULT_HDF5_SETTINGS),
                  lambda _fraction: None, lambda: False)
    return output


def summary(h5_path: Path) -> list[str]:
    """One line per series with its sample count and detected diagnostics."""
    lines = []
    with h5py.File(h5_path, "r") as h5_file:
        for entry in json.loads(h5_file.attrs["series_index_json"]):
            metadata = {item["id"]: item["value"] for item in entry.get("navigator_metadata", [])}
            warning = metadata.get("warning") or ""
            lines.append(f"{entry['name']}: {entry['sample_count']} samples {warning}".rstrip())
    return lines
