import os
import sys
import tempfile
import unittest
from pathlib import Path

import h5py


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from src.gui.plots.video import folder_start_unix_seconds


class VideoPlotSupportTests(unittest.TestCase):
    def test_reads_the_first_valid_h5_log_start_and_skips_unreadable_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "broken.h5").write_text("not an HDF5 file", encoding="utf-8")
            with h5py.File(folder / "data.h5", "w") as h5_file:
                h5_file.attrs["log_start_unix_seconds"] = 1_700_000_000.25

            value = folder_start_unix_seconds(folder)

        self.assertEqual(value, 1_700_000_000.25)


if __name__ == "__main__":
    unittest.main()
