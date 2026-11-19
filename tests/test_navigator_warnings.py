"""The Navigator reads each series' Sample loss and Clipping warning without walking its contents."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import h5py

from src.data import navigator_data
from src.data.navigator_data import diagnostic_warnings_by_series, read_h5_navigator_data


def add_series(h5, path, group, name, *, sample_loss=False, clipping=False, lod_levels=0):
    series = h5.create_group(f"series/{path}")
    series.attrs.update(navigator_group=group, navigator_name=name)
    series.create_dataset("value", data=[1.0, 2.0])
    if sample_loss:
        series.create_group("sample_loss_list").attrs["sample_loss_count"] = 1
    if clipping:
        clip = series.create_group("clipping_list")
        clip.create_dataset("clipping_start_unix", data=[1.0])
    lod = series.create_group("lod")
    for level in range(lod_levels):
        lod.create_group(f"level_{level:03d}").create_dataset("value_min", data=[0.0])


class NavigatorWarningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "data.h5"
        with h5py.File(self.path, "w") as h5:
            add_series(h5, "IMU/Accelerometer/X", "IMU/Accelerometer", "X", sample_loss=True, lod_levels=50)
            add_series(h5, "IMU/Accelerometer/Y", "IMU/Accelerometer", "Y", clipping=True)
            add_series(h5, "IMU/Accelerometer/Z", "IMU/Accelerometer", "Z", sample_loss=True, clipping=True)
            add_series(h5, "Temperature", "", "Temperature")
            # An index without the warning text: the warnings are read from the series groups.
            h5.attrs["series_index_json"] = json.dumps([
                {"name": f"IMU/Accelerometer/{axis}", "navigator_group": "IMU/Accelerometer",
                 "navigator_name": axis, "unit": "m/s^2", "sample_count": 2} for axis in "XYZ"
            ] + [{"name": "Temperature", "navigator_group": "", "navigator_name": "Temperature", "sample_count": 2}])

    def test_each_series_warning_is_found(self):
        with h5py.File(self.path, "r") as h5:
            self.assertEqual(diagnostic_warnings_by_series(h5), {
                ("IMU/Accelerometer", "X"): "Sampleloss detected",
                ("IMU/Accelerometer", "Y"): "Clipping detected",
                ("IMU/Accelerometer", "Z"): "Sampleloss and Clipping detected",
                ("", "Temperature"): "",
            })
        warnings = {series.navigator_name: next((entry.value for entry in series.metadata
                                                 if entry.column_id == "warning"), "")
                    for series in read_h5_navigator_data(self.path).series}
        self.assertEqual(warnings, {"X": "Sampleloss detected", "Y": "Clipping detected",
                                    "Z": "Sampleloss and Clipping detected", "Temperature": ""})

    def test_series_contents_are_not_walked(self):
        """A series' LOD levels and diagnostic lists are never entered (slow on big files)."""
        visited = []
        original = navigator_data.visit_series_groups

        def recording(root, visit):  # type: ignore[no-untyped-def]
            original(root, lambda path, group: (visited.append(path), visit(path, group)))

        with mock.patch.object(navigator_data, "visit_series_groups", recording), \
                mock.patch.object(h5py.Group, "visititems", side_effect=AssertionError("walked every object")):
            with h5py.File(self.path, "r") as h5:
                diagnostic_warnings_by_series(h5)
        self.assertEqual(sorted(visited), ["IMU/Accelerometer/X", "IMU/Accelerometer/Y", "IMU/Accelerometer/Z",
                                           "Temperature"])

    def test_a_file_without_an_index_lists_its_series(self):
        with h5py.File(self.path, "a") as h5:
            del h5.attrs["series_index_json"]
        names = sorted(series.name for series in read_h5_navigator_data(self.path).series)
        self.assertEqual(names, ["IMU/Accelerometer/X", "IMU/Accelerometer/Y", "IMU/Accelerometer/Z", "Temperature"])


if __name__ == "__main__":
    unittest.main()
