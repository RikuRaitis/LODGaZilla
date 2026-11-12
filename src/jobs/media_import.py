"""Convert audio files and video soundtracks to audio H5 files in the process queue.

Audio is long and dense (48,000 samples per second), so it is decoded and
written in blocks: memory stays bounded whatever the file length. Each LOD
level is built per block; levels are powers of the bucket growth no larger
than a block, so their buckets never cross a block boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

import h5py
import numpy as np

from src.data.media import AUDIO_KIND, FOLDER_LOG_START_ATTR, MEDIA_GROUP, MEDIA_KIND_ATTR, OFFSET_ATTR
from src.jobs.import_data import (
    SCHEMA_VERSION, SERIES_INDEX_ATTR, _safe_hdf5_name, build_lod_arrays,
)
from src.jobs.policy import PROGRESS_TOTAL
from src.version import APP_VERSION

LOG = logging.getLogger("lodgazilla.media_import")
TIME_DATASET = "time_s"
# Largest LOD bucket built while streaming, and the block size written at once.
MAX_BLOCK_SAMPLES = 1 << 20
# Finest LOD bucket for audio.
MIN_BUCKET_SAMPLES = 64


@dataclass(frozen=True)
class MediaItem:
    source: str
    output: str
    # The data folder's log start; None leaves the audio on its own relative time.
    folder_log_start: float | None
    offset_seconds: float = 0.0


@dataclass(frozen=True)
class MediaJobRequest:
    """A host-owned job in the shared process queue, alongside H5 imports and plugin jobs."""

    folder: Path
    items: tuple[MediaItem, ...]
    recursive: bool = False
    process_slots: int = 1
    operation: str = "Audio conversion"


class Cancelled(Exception):
    pass


def block_and_levels(growth: int) -> tuple[int, list[int]]:
    """LOD bucket sizes from MIN_BUCKET_SAMPLES up to the largest that fits a block, and that block size.

    Finer levels would store more than the audio itself (each bucket keeps
    several times and indices); the reader simply uses the levels present.
    """
    sizes = []
    size = growth
    while size <= MAX_BLOCK_SAMPLES:
        if size >= MIN_BUCKET_SAMPLES:
            sizes.append(size)
        size *= growth
    block = sizes[-1] if sizes else MAX_BLOCK_SAMPLES
    return block, sizes


class _ChannelWriter:
    """Appends one channel's samples, time and LOD levels to resizable datasets."""

    def __init__(self, group, rate, bucket_sizes, dataset_options):  # type: ignore[no-untyped-def]
        self.group, self.rate, self.bucket_sizes = group, float(rate), bucket_sizes
        self.options = dataset_options
        self.count = 0
        self.time = self._dataset(group, TIME_DATASET, np.float64)
        self.value = self._dataset(group, "value", np.int16)
        self.lod = group.create_group("lod")
        self.levels: list[tuple[h5py.Group, int]] = []
        # LOD arrays are written in whole chunks: appending small pieces to a
        # compressed dataset would recompress its last chunk every block.
        self.chunk_rows = int(dataset_options["chunks"][0])
        self._lod_pending: dict[tuple[int, str], list[np.ndarray]] = {}
        for level, size in enumerate(bucket_sizes, start=1):
            level_group = self.lod.create_group(f"level_{level:03d}")
            level_group.attrs["bucket_size_samples"] = size
            self.levels.append((level_group, size))

    def _dataset(self, group, name, dtype):  # type: ignore[no-untyped-def]
        return group.create_dataset(name, shape=(0,), maxshape=(None,), dtype=dtype, **self.options)

    @staticmethod
    def _append(dataset, values) -> None:  # type: ignore[no-untyped-def]
        start = dataset.shape[0]
        dataset.resize((start + len(values),))
        dataset[start:] = values

    def write(self, values: np.ndarray) -> None:
        times = (self.count + np.arange(len(values), dtype=np.float64)) / self.rate
        self._append(self.time, times)
        self._append(self.value, values)
        for level, (level_group, size) in enumerate(self.levels):
            arrays = build_lod_arrays(times, values, size, time_dataset_name=TIME_DATASET)
            # Sample indices are relative to this block.
            for name in ("sample_index_min", "sample_index_max"):
                arrays[name] = np.where(arrays[name] >= 0, arrays[name] + self.count, arrays[name])
            for name, array in arrays.items():
                if name not in level_group:
                    self._dataset(level_group, name, array.dtype)
                self._lod_pending.setdefault((level, name), []).append(array)
            self._write_lod(level, final=False)
        self.count += len(values)

    def _write_lod(self, level: int, *, final: bool) -> None:
        level_group = self.levels[level][0]
        for (pending_level, name), parts in self._lod_pending.items():
            if pending_level != level:
                continue
            merged = np.concatenate(parts) if len(parts) > 1 else parts[0]
            size = len(merged) if final else len(merged) // self.chunk_rows * self.chunk_rows
            if size:
                self._append(level_group[name], merged[:size])
            parts[:] = [merged[size:]] if size < len(merged) else []

    def finish(self, target_buckets: int) -> None:
        """Keep levels up to the first with at most ``target_buckets`` buckets, like other caches."""
        for level in range(len(self.levels)):
            self._write_lod(level, final=True)
        top = None
        for level, (level_group, size) in enumerate(self.levels, start=1):
            if top is not None or self.count <= target_buckets:
                del self.lod[level_group.name.rsplit("/", 1)[-1]]
                continue
            level_group.attrs["source_sample_count"] = self.count
            level_group.attrs["bucket_count"] = math.ceil(self.count / size)
            if math.ceil(self.count / size) <= target_buckets:
                top = level
        kept = [name for name in self.lod if name.startswith("level_")]
        self.lod.attrs["levels"] = len(kept)
        if kept:
            self.lod.attrs["top_level"] = sorted(kept)[-1]


def convert_audio(
    item: MediaItem,
    hdf5_settings: dict[str, Any],
    progress: Callable[[float], None],
    cancelled: Callable[[], bool],
) -> list[str]:
    """Write the item's audio to its output H5 atomically; return the series names."""
    import av

    source, output = Path(item.source), Path(item.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Not ".tmp": an H5 cache job in the same folder may run meanwhile and removes its stale temp files.
    descriptor, temporary = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".partial", dir=output.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        with av.open(str(source)) as container:
            if not container.streams.audio:
                raise ValueError(f"{source.name} has no audio track")
            stream = container.streams.audio[0]
            rate = int(stream.rate or stream.codec_context.sample_rate)
            stereo = (stream.codec_context.channels or 1) > 1
            names = ["Audio left", "Audio right"] if stereo else ["Audio"]
            duration = (float(stream.duration * stream.time_base) if stream.duration
                        else float(container.duration or 0) / av.time_base)
            resampler = av.AudioResampler(format="s16p", layout="stereo" if stereo else "mono", rate=rate)
            growth = max(2, int(hdf5_settings.get("lod_bucket_growth", 4)))
            target = max(1, int(hdf5_settings.get("lod_target_buckets", 2000)))
            block, bucket_sizes = block_and_levels(growth)
            options: dict[str, Any] = {"chunks": (int(hdf5_settings.get("chunk_rows", 65536)),), "shuffle": True}
            compression = str(hdf5_settings.get("compression", "gzip") or "").strip().lower()
            if compression not in {"", "none", "false", "off"}:
                options["compression"] = compression
                if compression == "gzip":
                    options["compression_opts"] = int(hdf5_settings.get("compression_level", 4))
            with h5py.File(temporary_path, "w") as h5_file:
                writers = _write_file_header(h5_file, item, names, rate, bucket_sizes, options)
                pending = [[] for _ in names]
                pending_count = 0

                def flush(final: bool) -> None:
                    nonlocal pending_count
                    while pending_count >= block or (final and pending_count):
                        merged = [np.concatenate(parts) for parts in pending]
                        size = min(block, pending_count)
                        for writer, samples in zip(writers, merged):
                            writer.write(samples[:size])
                        for index, samples in enumerate(merged):
                            pending[index] = [samples[size:]]
                        pending_count -= size

                for frame in container.decode(stream):
                    if cancelled():
                        raise Cancelled()
                    for converted in resampler.resample(frame):
                        samples = converted.to_ndarray().reshape(len(names), -1)
                        for index in range(len(names)):
                            pending[index].append(samples[index])
                        pending_count += samples.shape[1]
                    flush(False)
                    if duration > 0:
                        progress(min(0.99, writers[0].count / rate / duration))
                for converted in resampler.resample(None):
                    samples = converted.to_ndarray().reshape(len(names), -1)
                    for index in range(len(names)):
                        pending[index].append(samples[index])
                    pending_count += samples.shape[1]
                flush(True)
                if writers[0].count == 0:
                    raise ValueError(f"{source.name} has no audio samples")
                _write_file_footer(h5_file, writers, names, rate, target)
        os.replace(temporary_path, output)
        progress(1.0)
        return [f"{MEDIA_GROUP}/{name}" for name in names]
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_file_header(h5_file, item, names, rate, bucket_sizes, options):  # type: ignore[no-untyped-def]
    h5_file.attrs["schema_version"] = SCHEMA_VERSION
    h5_file.attrs["tool"] = "universal import and plotting tool"
    h5_file.attrs["tool_version"] = APP_VERSION
    h5_file.attrs["created_utc"] = datetime.now(timezone.utc).isoformat()
    h5_file.attrs["source_file"] = str(item.source)
    h5_file.attrs["importer"] = "audio"
    h5_file.attrs[MEDIA_KIND_ATTR] = AUDIO_KIND
    h5_file.attrs[OFFSET_ATTR] = float(item.offset_seconds)
    h5_file.attrs["time_start_seconds"] = 0.0
    if item.folder_log_start is not None:
        h5_file.attrs[FOLDER_LOG_START_ATTR] = float(item.folder_log_start)
        h5_file.attrs["log_start_unix_seconds"] = float(item.folder_log_start) + float(item.offset_seconds)
    media_group = h5_file.create_group("series").create_group(MEDIA_GROUP)
    writers = []
    for name in names:
        group = media_group.create_group(_safe_hdf5_name(name))
        group.attrs.update({
            "display_name": name, "navigator_name": name, "navigator_group": MEDIA_GROUP,
            "unit": "counts", "original_unit": "counts", "description": "16-bit PCM audio",
            "source": str(item.source), "visible": True, "state_labels_json": "{}",
            "time_unit": "s", "time_reference": "device_relative", "time_dataset": TIME_DATASET,
            "sample_rate_hz": float(rate),
        })
        writers.append(_ChannelWriter(group, rate, bucket_sizes, options))
    return writers


def _write_file_footer(h5_file, writers, names, rate, target) -> None:  # type: ignore[no-untyped-def]
    index = []
    for writer, name in zip(writers, names):
        writer.finish(target)
        duration = (writer.count - 1) / rate if writer.count > 1 else 0.0
        writer.group.attrs.update({"sample_count": writer.count, "time_start_seconds": 0.0,
                                   "time_end_seconds": duration, "duration_seconds": duration})
        index.append({
            "name": f"{MEDIA_GROUP}/{name}", "display_name": name, "navigator_name": name,
            "navigator_group": MEDIA_GROUP, "unit": "counts", "sample_count": writer.count,
            "sample_rate_hz": float(rate), "visible": True, "time_start_seconds": 0.0,
            "time_end_seconds": duration, "duration_seconds": duration, "navigator_metadata": [],
        })
    h5_file.attrs["series_count"] = len(writers)
    h5_file.attrs[SERIES_INDEX_ATTR] = json.dumps(index, sort_keys=True)
    h5_file.attrs["time_end_seconds"] = max(entry["time_end_seconds"] for entry in index)
    h5_file.attrs["duration_seconds"] = h5_file.attrs["time_end_seconds"]


def run_media_process(request: MediaJobRequest, events, cancel_event=None, start_event=None,
                      hdf5_settings: dict[str, Any] | None = None) -> None:
    """Convert each item in turn, reporting progress with the shared event protocol."""
    if start_event is not None and not start_event.wait(30):
        return
    completed = failed = 0
    errors = []
    for number, item in enumerate(request.items):
        source_name = Path(item.source).name
        process_id = f"audio-{os.getpid()}-{number}"
        events.put(("process_started", str(request.folder), process_id, source_name, PROGRESS_TOTAL))
        last = [-1]

        def progress(fraction: float) -> None:
            step = int(fraction * PROGRESS_TOTAL)
            if step != last[0]:
                last[0] = step
                events.put(("process_progress", str(request.folder), process_id, source_name, step, PROGRESS_TOTAL))

        try:
            convert_audio(item, hdf5_settings or {}, progress,
                          lambda: cancel_event is not None and cancel_event.is_set())
            completed += 1
            events.put(("message", f"Audio saved: {item.output}"))
            events.put(("process_finished", str(request.folder), process_id, source_name, True))
        except Cancelled:
            events.put(("process_finished", str(request.folder), process_id, source_name, False))
            break
        except Exception as error:  # noqa: BLE001 - report every failure to the log
            failed += 1
            errors.append(f"{source_name}: {error}")
            events.put(("message", f"Audio conversion failed for {source_name}: {error}"))
            events.put(("process_finished", str(request.folder), process_id, source_name, False))
    events.put(("finished", completed, failed, "; ".join(errors)))
