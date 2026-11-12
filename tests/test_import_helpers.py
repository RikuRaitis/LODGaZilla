import sys
import unittest
import tempfile
import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.jobs.import_data import (  # noqa: E402
    ImportConfiguration,
    SERIES_INDEX_ATTR,
    build_lod_arrays,
    detect_clipping_ranges,
    detect_sample_loss_ranges,
    select_importer,
    write_hdf5_cache,
)
from src.data.parser_base import DataParser  # noqa: E402
from src.data.parser_base import SeriesDefinition, parse_series_definitions  # noqa: E402




class LodTests(unittest.TestCase):
    def test_levels_built_from_the_level_below_equal_levels_built_from_raw_samples(self):
        from src.jobs.import_data import build_lod_arrays, build_lod_arrays_in_blocks, combine_lod_arrays
        rng = np.random.default_rng(1)
        count = 20_011
        floats = rng.normal(size=count)
        floats[rng.random(count) < 0.2] = np.nan
        floats[3000:5000] = np.nan  # whole buckets without a finite value
        times = np.cumsum(rng.random(count))
        for values in (floats, floats.astype(np.float32), rng.integers(0, 9, count).astype(np.int16),
                       rng.random(count) > 0.5):
            lower = build_lod_arrays_in_blocks(times, values, 5, block_samples=700)
            size = 5
            for _level in range(4):
                expected = build_lod_arrays(times, values, size)
                for name, array in expected.items():
                    np.testing.assert_array_equal(lower[name], array, err_msg=f"{values.dtype} {size} {name}")
                size *= 5
                lower = combine_lod_arrays(lower, 5)

    def test_lod_buckets_preserve_min_and_max_values(self):
        time_values = np.asarray([1, 2, 3, 4, 5], dtype=np.float64)
        values = np.asarray([0, 5, -10, 2, 7], dtype=np.float64)

        lod = build_lod_arrays(time_values, values, bucket_size=2)

        np.testing.assert_array_equal(lod["value_min"], np.asarray([0, -10, 7]))
        np.testing.assert_array_equal(lod["value_max"], np.asarray([5, 2, 7]))
        np.testing.assert_array_equal(lod["time_min_unix"], np.asarray([1, 3, 5]))
        np.testing.assert_array_equal(lod["time_max_unix"], np.asarray([2, 4, 5]))
        np.testing.assert_array_equal(lod["sample_index_min"], np.asarray([0, 2, 4]))
        np.testing.assert_array_equal(lod["sample_index_max"], np.asarray([1, 3, 4]))
        np.testing.assert_array_equal(lod["count"], np.asarray([2, 2, 1]))


class SampleLossTests(unittest.TestCase):
    def test_parser_json_exposes_per_series_clipping_limits(self):
        definitions = parse_series_definitions({
            "sections": [{
                "name": "Sensors",
                "series": [{
                    "name": "signal",
                    "group": "IMU",
                    "unit": "g",
                    "min_value": -16,
                    "max_value": 16,
                }],
            }],
        })

        self.assertEqual(definitions[0].min_value, -16.0)
        self.assertEqual(definitions[0].max_value, 16.0)

    def test_detects_only_gaps_over_the_millisecond_limit(self):
        ranges = detect_sample_loss_ranges(
            np.asarray([0.0, 1.0, 2.0, 6.0, 7.0]),
            time_unit="ms",
            max_sample_interval_ms=2,
        )

        np.testing.assert_array_equal(ranges, np.asarray([[2.0, 6.0]]))

    def test_hdf5_sample_loss_list_stores_native_time_ranges(self):
        configuration = ImportConfiguration(Path("test.json"), {})
        result = {
            "series": {
                "signal": {
                    "time": np.asarray([1000.0, 1001.0, 1005.0]),
                    "values": np.asarray([1.0, 2.0, 3.0]),
                    "time_unit": "ms",
                    "time_sorted": True,
                    "max_sample_interval_ms": 2,
                }
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "sample.h5"
            input_path = Path(temp_dir) / "sample.bin"
            input_path.write_bytes(b"test")
            write_hdf5_cache(
                output_path=output_path,
                input_path=input_path,
                selected_config=configuration,
                import_result=result,
            )

            import h5py

            with h5py.File(output_path, "r") as h5_file:
                group = h5_file["series"]["Ungrouped"]["signal"]
                failures = group["sample_loss_list"]
                self.assertEqual(json.loads(failures.attrs["messages_json"]), ["Sampleloss detected"])
                self.assertEqual(failures.attrs["sample_loss_count"], 1)
                np.testing.assert_array_equal(failures["sample_loss_start_ms"], np.asarray([1001.0]))
                np.testing.assert_array_equal(failures["sample_loss_stop_ms"], np.asarray([1005.0]))

    def test_clipping_uses_inclusive_limits_and_groups_adjacent_samples(self):
        ranges = detect_clipping_ranges(
            np.asarray([0.0, 1.0, 2.0, 3.0, 4.0]),
            np.asarray([-1.0, 0.0, 0.1, 1.0, 2.0]),
            min_value=0.0,
            max_value=1.0,
        )

        np.testing.assert_array_equal(ranges, np.asarray([[0.0, 1.0], [3.0, 4.0]]))

    def test_hdf5_clipping_list_and_warning_metadata(self):
        configuration = ImportConfiguration(Path("test.json"), {})
        result = {
            "series": {
                "signal": {
                    "time": np.asarray([1000.0, 1001.0, 1005.0]),
                    "values": np.asarray([0.0, 0.5, 1.0]),
                    "time_unit": "ms",
                    "time_sorted": True,
                    "max_sample_interval_ms": 2,
                    "min_value": 0.0,
                    "max_value": 1.0,
                    "navigator_metadata": [
                        {"id": "warning", "title": "Warning", "source": "warning", "format": "text"},
                    ],
                }
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "sample.h5"
            input_path = Path(temp_dir) / "sample.bin"
            input_path.write_bytes(b"test")
            write_hdf5_cache(
                output_path=output_path,
                input_path=input_path,
                selected_config=configuration,
                import_result=result,
            )
            import h5py

            with h5py.File(output_path, "r") as h5_file:
                group = h5_file["series"]["Ungrouped"]["signal"]
                clipping = group["clipping_list"]
                self.assertEqual(json.loads(clipping.attrs["messages_json"]), ["Clipping detected"])
                np.testing.assert_array_equal(clipping["clipping_start_ms"], np.asarray([1000.0, 1005.0]))
                np.testing.assert_array_equal(clipping["clipping_stop_ms"], np.asarray([1000.0, 1005.0]))
                index = json.loads(h5_file.attrs[SERIES_INDEX_ATTR])
                self.assertEqual(index[0]["navigator_metadata"][0]["value"], "Sampleloss and Clipping detected")


class CacheOwnershipTests(unittest.TestCase):
    RESULT = {"series": {"signal": {"time": np.asarray([1.0]), "values": np.asarray([2.0]), "time_sorted": True}}}

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.output_path = self.folder / "run.h5"
        self.input_path = self.folder / "run.csv"
        self.input_path.write_bytes(b"test")

    def write(self, input_path=None, **options):
        input_path = input_path or self.input_path
        input_path.write_bytes(b"test")
        write_hdf5_cache(
            output_path=self.output_path, input_path=input_path,
            selected_config=ImportConfiguration(Path("test.json"), {}), import_result=self.RESULT, **options,
        )

    def assert_refused(self, input_path=None):
        original = self.output_path.read_bytes()
        with self.assertRaises(FileExistsError):
            self.write(input_path)
        self.assertEqual(self.output_path.read_bytes(), original)
        self.assertEqual(list(self.folder.glob(".run.h5.*.tmp")), [])

    def test_unrelated_h5_is_preserved(self):
        import h5py

        with h5py.File(self.output_path, "w") as h5_file:
            h5_file.attrs["tool"] = "another tool"
            h5_file["data"] = np.arange(4)
        self.assert_refused()

    def test_malformed_target_is_preserved(self):
        self.output_path.write_bytes(b"not an H5 file")
        self.assert_refused()

    def test_cache_of_another_source_is_preserved(self):
        self.write(self.folder / "run.ubx")
        self.assert_refused()

    def test_cache_of_the_same_source_is_replaced(self):
        self.write()
        import h5py

        from src.version import APP_VERSION

        with h5py.File(self.output_path, "r") as h5_file:
            first_created = h5_file.attrs["created_utc"]
            self.assertEqual(h5_file.attrs["tool_version"], APP_VERSION)
        self.write()
        with h5py.File(self.output_path, "r") as h5_file:
            self.assertNotEqual(h5_file.attrs["created_utc"], first_created)

    def test_cache_from_a_moved_folder_is_replaced(self):
        self.write()
        import h5py

        with h5py.File(self.output_path, "r+") as h5_file:
            h5_file.attrs["source_file"] = r"D:\old place\RUN.csv"
        self.write()

    def test_replace_any_replaces_an_unrelated_h5(self):
        self.output_path.write_bytes(b"not an H5 file")
        self.write(replace_any=True)
        self.assertNotEqual(self.output_path.read_bytes(), b"not an H5 file")


class ImportSelectionTests(unittest.TestCase):
    def test_extension_is_only_hint_for_importer_selection(self):
        class ContentBasedParser(DataParser):
            name = "Content based importer"
            priority = 100
            extensions = frozenset({".not_this_extension"})

            def can_parse(self, _input_path):
                return True

            def parse(self, _input_path, progress_callback=None):
                return {}

        parser_config: dict[str, object] = {"sections": []}
        path = ROOT / "data" / "TestData1" / "2026_08_20_13_42_13.log"
        parser = ContentBasedParser(Path("test.json"), parser_config)
        configuration = ImportConfiguration(Path("test.json"), parser_config, parser_instance=parser)

        selected, selected_parser = select_importer(path, [configuration])

        self.assertEqual(selected.name, "Content based importer")
        self.assertIs(selected_parser, parser)


class Hdf5TimeDatasetTests(unittest.TestCase):
    def test_failed_hdf5_replace_preserves_existing_cache(self):
        configuration = ImportConfiguration(Path("test.json"), {})
        result = {
            "series": {
                "signal": {
                    "time": np.asarray([1.0]),
                    "values": np.asarray([2.0]),
                    "time_sorted": True,
                }
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "sample.h5"
            input_path = Path(temp_dir) / "sample.bin"
            input_path.write_bytes(b"test")
            write_hdf5_cache(
                output_path=output_path, input_path=input_path,
                selected_config=configuration, import_result=result,
            )
            existing = output_path.read_bytes()

            with mock.patch("os.replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    write_hdf5_cache(
                        output_path=output_path,
                        input_path=input_path,
                        selected_config=configuration,
                        import_result=result,
                    )

            self.assertEqual(output_path.read_bytes(), existing)
            self.assertEqual(list(Path(temp_dir).glob(".sample.h5.*.tmp")), [])

    def test_failed_hdf5_write_preserves_existing_cache(self):
        configuration = ImportConfiguration(Path("test.json"), {})

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "sample.h5"
            input_path = Path(temp_dir) / "sample.bin"
            input_path.write_bytes(b"test")
            write_hdf5_cache(
                output_path=output_path, input_path=input_path,
                selected_config=configuration, import_result=CacheOwnershipTests.RESULT,
            )
            existing = output_path.read_bytes()

            with self.assertRaises(ValueError):
                write_hdf5_cache(
                    output_path=output_path,
                    input_path=input_path,
                    selected_config=configuration,
                    import_result={"series": {}},
                )

            self.assertEqual(output_path.read_bytes(), existing)
            self.assertEqual(list(Path(temp_dir).glob(".sample.h5.*.tmp")), [])

    def test_millisecond_series_uses_time_ms_and_matching_lod_datasets(self):
        parser_config = {}
        configuration = ImportConfiguration(
            Path("test.json"),
            parser_config,
            hdf5_config={"hdf5": {"lod_target_buckets": 1, "lod_bucket_growth": 2}},
        )
        result = {
            "series": {
                "Device signal": {
                    "time": np.asarray([1000.0, 1001.0, 1002.0, 1003.0]),
                    "values": np.asarray([1, 2, 3, 4], dtype=np.int16),
                    "time_unit": "ms",
                    "time_reference": "device_relative",
                    "navigator_group": "Test group",
                    "navigator_name": "Test signal",
                    "unit": "m/s",
                    "navigator_metadata": [
                        {"id": "count", "title": "Count", "source": "sample_count", "format": "integer"},
                        {"id": "minimum", "title": "Minimum", "source": "min", "format": "number"},
                        {"id": "unit", "title": "Unit", "source": "unit", "format": "text"},
                    ],
                    "time_sorted": True,
                }
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "sample.h5"
            input_path = Path(temp_dir) / "sample.bin"
            input_path.write_bytes(b"test")
            write_hdf5_cache(
                output_path=output_path,
                input_path=input_path,
                selected_config=configuration,
                import_result=result,
            )

            import h5py

            with h5py.File(output_path, "r") as h5_file:
                group = h5_file["series"]["Test_group"]["Test_signal"]
                self.assertEqual(group.attrs["time_dataset"], "time_ms")
                self.assertEqual(group.attrs["navigator_group"], "Test group")
                self.assertEqual(group.attrs["navigator_name"], "Test signal")
                self.assertEqual(group.attrs["sample_count"], 4)
                self.assertAlmostEqual(group.attrs["sample_rate_hz"], 1000.0)
                self.assertAlmostEqual(group.attrs["time_start_seconds"], 1.0)
                self.assertAlmostEqual(group.attrs["time_end_seconds"], 1.003)
                self.assertAlmostEqual(group.attrs["duration_seconds"], 0.003)
                self.assertAlmostEqual(h5_file.attrs["time_start_seconds"], 1.0)
                self.assertAlmostEqual(h5_file.attrs["time_end_seconds"], 1.003)
                self.assertAlmostEqual(h5_file.attrs["duration_seconds"], 0.003)
                series_index = json.loads(h5_file.attrs[SERIES_INDEX_ATTR])
                self.assertEqual(series_index[0]["navigator_name"], "Test signal")
                self.assertEqual(series_index[0]["sample_count"], 4)
                self.assertAlmostEqual(series_index[0]["sample_rate_hz"], 1000.0)
                self.assertEqual(
                    series_index[0]["navigator_metadata"],
                    [
                        {"id": "count", "title": "Count", "format": "integer", "value": 4},
                        {"id": "minimum", "title": "Minimum", "format": "number", "value": 1.0},
                        {"id": "unit", "title": "Unit", "format": "text", "value": "m/s"},
                    ],
                )
                self.assertIn("time_ms", group)
                self.assertNotIn("time_unix", group)
                # Without a transform, value_raw is the same dataset as value (a hard link).
                self.assertEqual(group["value_raw"], group["value"])
                level = group["lod"]["level_001"]
                self.assertIn("time_start_ms", level)
                self.assertIn("time_end_ms", level)

    def test_series_names_with_the_same_h5_name_are_both_written(self):
        import h5py
        from src.data.h5_series import _series_group_for_navigator_name
        configuration = ImportConfiguration(Path("test.json"), {}, hdf5_config={})
        series = {
            name: {"time": np.asarray([0.0, 1.0]), "values": np.asarray(values), "time_unit": "s",
                   "time_reference": "device_relative", "navigator_group": "Car", "navigator_name": name,
                   "time_sorted": True}
            for name, values in (("Speed (km/h)", [1.0, 2.0]), ("Speed [km/h]", [3.0, 4.0]))
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "car.h5"
            input_path = Path(temp_dir) / "car.csv"
            input_path.write_bytes(b"x")
            write_hdf5_cache(output_path=output_path, input_path=input_path, selected_config=configuration,
                             import_result={"series": series})
            with h5py.File(output_path, "r") as h5_file:
                self.assertEqual(sorted(h5_file["series/Car"]), ["Speed_km_h", "Speed_km_h_2"])
                for name, values in (("Car/Speed (km/h)", [1.0, 2.0]), ("Car/Speed [km/h]", [3.0, 4.0])):
                    np.testing.assert_array_equal(_series_group_for_navigator_name(h5_file, name)["value"], values)

    def test_time_said_to_be_sorted_is_checked(self):
        from src.jobs.import_data import _normalise_series
        # A clock reset in a series the parser called sorted.
        times, values = _normalise_series({"time": [0.0, 1.0, 2.0, 0.5], "values": [1, 2, 3, 4],
                                           "time_sorted": True})
        np.testing.assert_array_equal(times, [0.0, 0.5, 1.0, 2.0])
        np.testing.assert_array_equal(values, [1, 4, 2, 3])

    def test_navigator_reads_warnings_from_the_series_index(self):
        import h5py
        from src.data.navigator_data import read_h5_navigator_data
        configuration = ImportConfiguration(Path("test.json"), {}, hdf5_config={})
        series = {"Gappy": {"time": np.asarray([0.0, 0.01, 5.0]), "values": np.asarray([1.0, 2.0, 3.0]),
                            "time_unit": "s", "time_reference": "device_relative", "navigator_group": "G",
                            "navigator_name": "Gappy", "max_sample_interval_ms": 100, "time_sorted": True}}
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "gaps.h5"
            input_path = Path(temp_dir) / "gaps.csv"
            input_path.write_bytes(b"x")
            write_hdf5_cache(output_path=output_path, input_path=input_path, selected_config=configuration,
                             import_result={"series": series})
            with mock.patch.object(h5py.Group, "visititems", side_effect=AssertionError("walked")):
                data = read_h5_navigator_data(output_path)
            # A file without the indexed warning (an older one) is walked for it instead.
            with h5py.File(output_path, "a") as h5_file:
                index = json.loads(h5_file.attrs["series_index_json"])
                del index[0]["diagnostic_warning"]
                h5_file.attrs["series_index_json"] = json.dumps(index)
            older = read_h5_navigator_data(output_path)
        for result in (data, older):
            warning = {entry.column_id: entry.value for entry in result.series[0].metadata}.get("warning")
            self.assertEqual(warning, "Sampleloss detected")

    def test_hdf5_writer_reports_monotonic_progress(self):
        parser_config = {}
        configuration = ImportConfiguration(
            Path("test.json"),
            parser_config,
            hdf5_config={"hdf5": {"lod_target_buckets": 1, "lod_bucket_growth": 2}},
        )
        result = {
            "series": {
                "first": {
                    "time": np.asarray([1.0, 2.0, 3.0, 4.0]),
                    "values": np.asarray([10, 20, 30, 40], dtype=np.int16),
                    "time_sorted": True,
                },
                "second": {
                    "time": np.asarray([1.0, 2.0, 3.0, 4.0]),
                    "values": np.asarray([2.0, 3.0, 4.0, 5.0]),
                    "time_sorted": True,
                },
            }
        }
        progress_values: list[float] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "sample.h5"
            input_path = Path(temp_dir) / "sample.bin"
            input_path.write_bytes(b"test")

            write_hdf5_cache(
                output_path=output_path,
                input_path=input_path,
                selected_config=configuration,
                import_result=result,
                progress_callback=progress_values.append,
            )

        self.assertGreater(len(progress_values), 2)
        self.assertEqual(progress_values[0], 0.0)
        self.assertEqual(progress_values[-1], 1.0)
        self.assertTrue(all(
            earlier <= later
            for earlier, later in zip(progress_values, progress_values[1:])
        ))


if __name__ == "__main__":
    unittest.main()
