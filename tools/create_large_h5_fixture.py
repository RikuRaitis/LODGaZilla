"""Create a large synthetic LODGaZilla H5 cache from a compact binary fixture.

The binary contains interleaved float32 samples ``a, b, c`` at 1000 Hz.
Each H5 series is composed from two deterministic sine waves and white noise.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.jobs.import_data import create_lod_levels


SAMPLE_RATE_HZ = 1000
SERIES = ("a", "b", "c")
RECORD_DTYPE = np.dtype([(name, "<f4") for name in SERIES])
CHUNK_SAMPLES = 1_000_000
LOD_TARGET_BUCKETS = 2_000


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("size_mib", type=int, help="Binary fixture size in MiB, for example 1024.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Folder that receives the .bin and .h5 files.")
    parser.add_argument("--name", default="synthetic_3axis_1000hz", help="Base name for generated files.")
    parser.add_argument("--seed", type=int, default=20260908, help="Deterministic random-noise seed.")
    parser.add_argument("--compression", choices=("gzip", "lzf", "none"), default="gzip")
    parser.add_argument("--reuse-binary", action="store_true", help="Reuse an existing matching binary fixture.")
    return parser.parse_args()


def sample_count_for_size(size_mib: int) -> int:
    if size_mib <= 0:
        raise ValueError("size_mib must be positive")
    return size_mib * 1024 * 1024 // RECORD_DTYPE.itemsize


def samples(start: int, count: int, rng: np.random.Generator) -> np.ndarray:
    index = np.arange(start, start + count, dtype=np.float64)
    seconds = index / SAMPLE_RATE_HZ
    output = np.empty(count, dtype=RECORD_DTYPE)
    # Distinct but realistic signals: two sine components plus low-amplitude noise.
    definitions = ((1.7, 0.35, 8.3, 0.10), (0.9, 0.55, 5.1, 0.17), (2.4, 0.28, 11.7, 0.07))
    for name, (first_hz, first_amplitude, second_hz, second_amplitude) in zip(SERIES, definitions):
        signal = first_amplitude * np.sin(2 * np.pi * first_hz * seconds)
        signal += second_amplitude * np.sin(2 * np.pi * second_hz * seconds + 0.4)
        signal += rng.normal(0.0, 0.012, count)
        output[name] = signal.astype(np.float32)
    return output


def create_binary(path: Path, sample_count: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    with path.open("wb") as binary_file:
        for start in range(0, sample_count, CHUNK_SAMPLES):
            chunk = samples(start, min(CHUNK_SAMPLES, sample_count - start), rng)
            binary_file.write(chunk.tobytes())


def dataset_kwargs(compression: str) -> dict[str, object]:
    if compression == "none":
        return {}
    return {"compression": compression, "compression_opts": 1} if compression == "gzip" else {"compression": compression}


def write_top_lod(group: h5py.Group, time_data: h5py.Dataset, value_data: h5py.Dataset, sample_count: int, kwargs: dict[str, object]) -> None:
    """Write the compact top LOD required for responsive first rendering."""
    bucket_size = math.ceil(sample_count / LOD_TARGET_BUCKETS)
    bucket_count = math.ceil(sample_count / bucket_size)
    lod = group.create_group("lod")
    level = lod.create_group("level_001")
    level.attrs.update(source_sample_count=sample_count, bucket_size_samples=bucket_size, bucket_count=bucket_count)
    starts = np.arange(bucket_count, dtype=np.int64) * bucket_size
    ends = np.minimum(starts + bucket_size, sample_count)
    last = ends - 1
    arrays: dict[str, np.ndarray] = {
        "time_start_ms": np.asarray(time_data[starts]), "time_end_ms": np.asarray(time_data[last]),
        "time_min_ms": np.empty(bucket_count, dtype=np.uint32), "time_max_ms": np.empty(bucket_count, dtype=np.uint32),
        "value_first": np.asarray(value_data[starts]), "value_last": np.asarray(value_data[last]),
        "value_min": np.empty(bucket_count, dtype=np.float32), "value_max": np.empty(bucket_count, dtype=np.float32),
        "sample_index_min": np.empty(bucket_count, dtype=np.int64), "sample_index_max": np.empty(bucket_count, dtype=np.int64),
        "count": (ends - starts).astype(np.int64), "finite_count": (ends - starts).astype(np.int64),
    }
    for bucket, (start, stop) in enumerate(zip(starts, ends)):
        values = np.asarray(value_data[start:stop])
        minimum, maximum = int(np.argmin(values)), int(np.argmax(values))
        arrays["value_min"][bucket], arrays["value_max"][bucket] = values[minimum], values[maximum]
        arrays["sample_index_min"][bucket], arrays["sample_index_max"][bucket] = start + minimum, start + maximum
        arrays["time_min_ms"][bucket], arrays["time_max_ms"][bucket] = time_data[start + minimum], time_data[start + maximum]
    for name, values in arrays.items():
        level.create_dataset(name, data=values, chunks=True, **kwargs)
    lod.attrs.update(levels=1, top_level="level_001")


def create_h5(binary_path: Path, h5_path: Path, sample_count: int, compression: str) -> None:
    kwargs = dataset_kwargs(compression)
    with h5py.File(h5_path, "w") as h5_file, binary_path.open("rb") as binary_file:
        h5_file.attrs.update(
            schema_version=1, tool="LODGaZilla synthetic large-file fixture", created_utc=datetime.now(timezone.utc).isoformat(),
            source_file=str(binary_path), source_size_bytes=binary_path.stat().st_size,
            log_start_unix_seconds=1_788_912_000.0, time_start_seconds=1_788_912_000.0,
            time_end_seconds=1_788_912_000.0 + (sample_count - 1) / SAMPLE_RATE_HZ,
        )
        root = h5_file.create_group("series")
        index: list[dict[str, object]] = []
        for name in SERIES:
            group = root.create_group(name)
            group.attrs.update(display_name=name, navigator_name=name, navigator_group="Synthetic", unit="a.u.", original_unit="a.u.", description="Two sine waves plus noise", source=binary_path.name, visible=True, state_labels_json="{}", time_dataset="time_ms", time_unit="ms", time_reference="device_relative", sample_count=sample_count, sample_rate_hz=float(SAMPLE_RATE_HZ), time_start_seconds=1_788_912_000.0, time_end_seconds=1_788_912_000.0 + (sample_count - 1) / SAMPLE_RATE_HZ, duration_seconds=(sample_count - 1) / SAMPLE_RATE_HZ)
            time_data = group.create_dataset("time_ms", shape=(sample_count,), dtype=np.uint32, chunks=(min(CHUNK_SAMPLES, sample_count),), **kwargs)
            values = group.create_dataset("value", shape=(sample_count,), dtype=np.float32, chunks=(min(CHUNK_SAMPLES, sample_count),), **kwargs)
            binary_file.seek(0)
            minimum, maximum = math.inf, -math.inf
            for start in range(0, sample_count, CHUNK_SAMPLES):
                count = min(CHUNK_SAMPLES, sample_count - start)
                records = np.fromfile(binary_file, dtype=RECORD_DTYPE, count=count)
                time_data[start:start + len(records)] = np.arange(start, start + len(records), dtype=np.uint32)
                values[start:start + len(records)] = records[name]
                minimum = min(minimum, float(np.min(records[name])))
                maximum = max(maximum, float(np.max(records[name])))
            group.attrs["min_value"] = minimum
            group.attrs["max_value"] = maximum
            # Use the same complete multi-resolution pyramid as production
            # imports: 9, 81, 729, ... samples per bucket until the top fits.
            create_lod_levels(
                group,
                np.asarray(time_data),
                np.asarray(values),
                time_dataset_name="time_ms",
                target_bucket_count=LOD_TARGET_BUCKETS,
                bucket_growth=9,
                chunk_rows=65_536,
                compression=None if compression == "none" else compression,
                compression_level=1 if compression == "gzip" else None,
            )
            index.append({"name": name, "display_name": name, "navigator_name": name, "navigator_group": "Synthetic", "unit": "a.u.", "sample_count": sample_count, "sample_rate_hz": float(SAMPLE_RATE_HZ), "visible": True, "time_start_seconds": 1_788_912_000.0, "time_end_seconds": 1_788_912_000.0 + (sample_count - 1) / SAMPLE_RATE_HZ, "duration_seconds": (sample_count - 1) / SAMPLE_RATE_HZ, "navigator_metadata": []})
        h5_file.attrs["series_index_json"] = json.dumps(index)


def main() -> None:
    args = parse_arguments()
    count = sample_count_for_size(args.size_mib)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    binary_path = args.output_dir / f"{args.name}_{args.size_mib}MiB.bin"
    h5_path = args.output_dir / f"{args.name}_{args.size_mib}MiB.h5"
    print(f"Writing {count:,} samples ({args.size_mib} MiB binary) to {binary_path}")
    if not args.reuse_binary:
        create_binary(binary_path, count, args.seed)
    print(f"Converting to LODGaZilla H5 cache: {h5_path}")
    create_h5(binary_path, h5_path, count, args.compression)
    print(f"Created {binary_path.stat().st_size / 1024**2:.1f} MiB binary and {h5_path.stat().st_size / 1024**2:.1f} MiB H5")


if __name__ == "__main__":
    main()
