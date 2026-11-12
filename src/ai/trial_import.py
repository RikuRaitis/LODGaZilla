"""Import one file with a draft importer and print a JSON summary of the H5.

``python -m src.ai.trial_import <draft folder> <input file> <output.h5>``

Magic drop runs this in its own process, because the draft's parser is code
Claude wrote. Claude runs it too, to test its parser. The last output line is the
summary: the series with their units and sample counts, and the time range.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

from src.app.settings import load_hdf5_settings
from src.jobs.import_data import load_import_configuration, select_importer, write_hdf5_cache


def trial_import(draft: Path, source: Path, output: Path) -> dict:
    configuration = load_import_configuration(draft / "importer.json", hdf5_config=load_hdf5_settings(),
                                              parser_module_path=draft / "importer.py")
    selected, importer = select_importer(source.resolve(), [configuration])
    result = importer.parse(source.resolve())
    write_hdf5_cache(output_path=output.resolve(), input_path=source.resolve(), selected_config=selected,
                     import_result=result, replace_any=True)
    return h5_summary(output)


def h5_summary(path: Path) -> dict:
    series = []
    with h5py.File(path, "r") as handle:
        def visit(name, item):  # type: ignore[no-untyped-def]
            if isinstance(item, h5py.Group) and "value" in item:
                entry = {"name": name, "unit": str(item.attrs.get("unit", "")),
                         "original_unit": str(item.attrs.get("original_unit", "")),
                         "samples": int(item["value"].shape[0])}
                # The range lets Claude check that the values fit their unit.
                values = np.asarray(item["value"][()], dtype=np.float64)
                finite = values[np.isfinite(values)]
                if finite.size:
                    entry["min"], entry["max"] = float(finite.min()), float(finite.max())
                series.append(entry)
        if "series" in handle:
            handle["series"].visititems(visit)
        start, end = handle.attrs.get("time_start_seconds"), handle.attrs.get("time_end_seconds")
    summary: dict = {"output": str(path), "series": series}
    if start is not None and end is not None:
        summary["time_start"] = _time_text(float(start))
        summary["time_end"] = _time_text(float(end))
    return summary


def _time_text(seconds: float) -> str:
    # Device-relative files have small values; show those as seconds.
    if seconds > 1e8:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat(timespec="seconds")
    return f"{seconds:g} s"


def main(argv: list[str] | None = None) -> int:
    arguments = argv if argv is not None else sys.argv[1:]
    if len(arguments) != 3:
        print("usage: python -m src.ai.trial_import <draft folder> <input file> <output.h5>", file=sys.stderr)
        return 2
    draft, source, output = (Path(item) for item in arguments)
    try:
        summary = trial_import(draft, source, output)
    except Exception as error:  # noqa: BLE001 - report any parser failure as one line.
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
