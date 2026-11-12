"""Synthetic plugins for core tests; no installed importer is required."""
from contextlib import contextmanager
from functools import wraps
import json
from pathlib import Path
import tempfile
from unittest import mock


def write_manifest(folder, *, plugin_id="sample", capabilities=None, entry_point="plugin.py", **extra):
    manifest = {"id": plugin_id, "title": "Sample plugin", "version": "0.1.0",
                "api_version": 1, "capabilities": capabilities or ["ui"], "entry_point": entry_point, **extra}
    (Path(folder) / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")


@contextmanager
def sample_importer():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory) / "sample"
        folder.mkdir()
        write_manifest(folder, capabilities=["importer"], entry_point="importer.py")
        (folder / "importer.json").write_text('{"sections": []}')
        (folder / "importer.py").write_text('''import numpy as np
from src.contracts.importer import DataParser
class Parser(DataParser):
    name = "Sample"
    extensions = frozenset({".csv"})
    def can_parse(self, path): return path.suffix == ".csv"
    def parse(self, path, progress_callback=None):
        data = np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2)
        return {"format": "sample", "series": {"Test/signal": {
            "time": data[:, 0], "values": data[:, 1], "time_unit": "s",
            "time_reference": "device_relative", "navigator_group": "Test",
            "navigator_name": "signal", "unit": "V", "time_sorted": True}}}
''', encoding="utf-8")
        with mock.patch("src.jobs.import_batch.DEFAULT_CONFIG_DIR", Path(directory)):
            yield Path(directory)


def with_sample_importer(test):
    @wraps(test)
    def wrapped(*args, **kwargs):
        with sample_importer():
            return test(*args, **kwargs)
    return wrapped
