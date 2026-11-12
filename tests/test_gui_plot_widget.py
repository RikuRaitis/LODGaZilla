import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import h5py
import numpy as np


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QCoreApplication, QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from src.gui.plots.series import (  # noqa: E402
    MarkerDataLoadCoordinator,
    SeriesPlotWidget,
    load_clipping_ranges,
    load_marker_sample,
    load_plot_value_at_x,
    load_plot_series,
    load_sample_loss_ranges,
    load_timing_delta_series,
)
from src.gui.plots.xy import load_xy_data  # noqa: E402
from src.gui.plots.diagnostic_areas import CLIPPING, SAMPLE_LOSS, visible_spans  # noqa: E402
from tests.qt_wait import dispose_cards  # noqa: E402


class PlotSeriesLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        # Pending view updates must not read a temporary cache after its test
        # removed it, when a later GUI test pumps the shared event loop.
        dispose_cards()

    def test_loads_raw_series_with_title_and_unit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                group = h5_file.create_group("series/acc_x")
                group.attrs["time_dataset"] = "time_unix"
                group.attrs["navigator_name"] = "Acceleration X"
                group.attrs["unit"] = "m/s²"
                group.create_dataset("time_unix", data=np.asarray([1.0, 2.0, 3.0]))
                group.create_dataset("value", data=np.asarray([4.0, 5.0, 6.0]))

            data = load_plot_series(h5_path, "acc_x")

        np.testing.assert_array_equal(data.x, np.asarray([1.0, 2.0, 3.0]))
        np.testing.assert_array_equal(data.y, np.asarray([4.0, 5.0, 6.0]))
        self.assertEqual(data.title, "Acceleration X")
        self.assertEqual(data.unit, "m/s²")
        self.assertIsNone(data.lod_level)

    def test_converts_device_relative_time_to_a_common_real_time_axis(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                h5_file.attrs["log_start_unix_seconds"] = 1_700_000_000.0
                group = h5_file.create_group("series/acc_x")
                group.attrs["time_dataset"] = "time_ms"
                group.attrs["time_unit"] = "ms"
                group.attrs["time_reference"] = "device_relative"
                group.create_dataset("time_ms", data=np.asarray([100.0, 250.0]))
                group.create_dataset("value", data=np.asarray([1.0, 2.0]))

            data = load_plot_series(h5_path, "acc_x")

        np.testing.assert_allclose(data.x, np.asarray([1_700_000_000.0, 1_700_000_000.15]))

    def test_loads_nearest_raw_sample_for_marker_popup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                group = h5_file.create_group("series/acc_x")
                group.attrs["time_dataset"] = "time_unix"
                group.create_dataset("time_unix", data=np.asarray([1.0, 3.0, 7.0]))
                group.create_dataset("value", data=np.asarray([10.0, 30.0, 70.0]))

            sample_x, sample_y = load_plot_value_at_x(h5_path, "acc_x", 4.2)

        self.assertEqual((sample_x, sample_y), (3.0, 30.0))

    def test_marker_sample_includes_converted_and_original_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                h5_file.attrs["log_start_unix_seconds"] = 1_700_000_000.0
                group = h5_file.create_group("series/acc_x")
                group.attrs["time_dataset"] = "time_ms"
                group.attrs["time_unit"] = "ms"
                group.attrs["time_reference"] = "device_relative"
                group.attrs["unit"] = "m/s²"
                group.create_dataset("time_ms", data=np.asarray([100.0, 120.0]))
                group.create_dataset("value", data=np.asarray([1.25, 2.5]))
                group.create_dataset("value_raw", data=np.asarray([5000.0, 10000.0]))

            sample = load_marker_sample(h5_path, "acc_x", 119.0)

        self.assertEqual(sample.sample_time, 120.0)
        self.assertEqual(sample.log_elapsed_ms, 20.0)
        self.assertEqual(sample.converted_value, 2.5)
        self.assertEqual(sample.original_value, 10000.0)
        self.assertEqual(sample.unit, "m/s²")

    def test_marker_sample_can_return_nearest_value_outside_series_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                group = h5_file.create_group("series/is_wet")
                group.attrs["time_dataset"] = "time_ms"
                group.attrs["time_unit"] = "ms"
                group.create_dataset("time_ms", data=np.asarray([100.0, 200.0]))
                group.create_dataset("value", data=np.asarray([0.0, 1.0]))

            sample = load_marker_sample(
                h5_path,
                "is_wet",
                450.0,
                allow_outside_series=True,
            )

        self.assertEqual(sample.sample_time, 200.0)
        self.assertEqual(sample.converted_value, 1.0)

    def test_loads_timing_delta_between_consecutive_samples(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                group = h5_file.create_group("series/acc_x")
                group.attrs["time_dataset"] = "time_ms"
                group.attrs["time_unit"] = "ms"
                group.attrs["navigator_name"] = "Acceleration X"
                group.create_dataset("time_ms", data=np.asarray([10.0, 12.0, 17.0, 18.0]))
                group.create_dataset("value", data=np.asarray([1.0, 2.0, 3.0, 4.0]))

            data = load_timing_delta_series(h5_path, "acc_x")

        np.testing.assert_array_equal(data.x, np.asarray([12.0, 17.0, 18.0]))
        np.testing.assert_array_equal(data.y, np.asarray([2.0, 5.0, 1.0]))
        self.assertEqual(data.title, "Acceleration X timing delta")
        self.assertEqual(data.unit, "ms")

    def test_xy_loader_aligns_y_to_x_times_and_honours_time_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                for name, times, values in (
                    ("x", [0.0, 10.0, 20.0], [1.0, 2.0, 3.0]),
                    ("y", [1.0, 11.0, 19.0], [4.0, 5.0, 6.0]),
                ):
                    group = h5_file.create_group(f"series/{name}")
                    group.attrs["time_dataset"] = "time_ms"
                    group.create_dataset("time_ms", data=np.asarray(times))
                    group.create_dataset("value", data=np.asarray(values))

            times, x_values, y_values, _x_label, _y_label = load_xy_data(
                h5_path, "x", h5_path, "y", time_range=(5.0, 20.0)
            )

        # X=20 is outside Y's measured coverage (which ends at 19), even
        # though it is inside the requested interval. Do not clamp it to Y[-1].
        np.testing.assert_array_equal(times, np.asarray([10.0]))
        np.testing.assert_array_equal(x_values, np.asarray([2.0]))
        np.testing.assert_array_equal(y_values, np.asarray([5.0]))

    def test_uses_hdf5_lod_envelope_when_raw_data_exceeds_budget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                group = h5_file.create_group("series/acc_x")
                group.attrs["time_dataset"] = "time_unix"
                group.create_dataset("time_unix", data=np.arange(16, dtype=float))
                group.create_dataset("value", data=np.arange(16, dtype=float))
                lod = group.create_group("lod/level_001")
                lod.attrs["bucket_count"] = 2
                lod.create_dataset("time_start_unix", data=np.asarray([0.0, 8.0]))
                lod.create_dataset("time_end_unix", data=np.asarray([7.0, 15.0]))
                lod.create_dataset("time_min_unix", data=np.asarray([2.0, 12.0]))
                lod.create_dataset("time_max_unix", data=np.asarray([5.0, 10.0]))
                lod.create_dataset("value_first", data=np.asarray([4.0, 6.0]))
                lod.create_dataset("value_last", data=np.asarray([7.0, 8.0]))
                lod.create_dataset("value_min", data=np.asarray([1.0, 2.0]))
                lod.create_dataset("value_max", data=np.asarray([9.0, 10.0]))

            data = load_plot_series(h5_path, "acc_x", maximum_points=8)

        self.assertEqual(data.lod_level, 1)
        np.testing.assert_array_equal(data.x, np.asarray([0.0, 2.0, 5.0, 7.0, 8.0, 10.0, 12.0, 15.0]))
        np.testing.assert_array_equal(data.y, np.asarray([4.0, 1.0, 9.0, 7.0, 6.0, 10.0, 2.0, 8.0]))

    def test_resolves_navigator_series_name_when_hdf5_path_is_sanitized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                group = h5_file.create_group("series/Example_Sensor/gyro_y")
                group.attrs["time_dataset"] = "time_ms"
                group.attrs["navigator_group"] = "Example Sensor"
                group.attrs["navigator_name"] = "gyro_y"
                group.create_dataset("time_ms", data=np.asarray([1000.0, 2000.0]))
                group.create_dataset("value", data=np.asarray([1.0, 2.0]))

            data = load_plot_series(h5_path, "Example Sensor/gyro_y")

        np.testing.assert_array_equal(data.x, np.asarray([1000.0, 2000.0]))
        np.testing.assert_array_equal(data.y, np.asarray([1.0, 2.0]))

    def test_loads_and_displays_sample_loss_regions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                group = h5_file.create_group("series/acc_x")
                group.attrs["time_dataset"] = "time_ms"
                group.create_dataset("time_ms", data=np.asarray([0.0, 1.0, 10.0]))
                group.create_dataset("value", data=np.asarray([1.0, 2.0, 3.0]))
                failures = group.create_group("sample_loss_list")
                failures.create_dataset("sample_loss_start_ms", data=np.asarray([1.0]))
                failures.create_dataset("sample_loss_stop_ms", data=np.asarray([10.0]))
                clipping = group.create_group("clipping_list")
                clipping.create_dataset("clipping_start_ms", data=np.asarray([0.0]))
                clipping.create_dataset("clipping_stop_ms", data=np.asarray([1.0]))

            ranges = load_sample_loss_ranges(h5_path, "acc_x")
            clipping_ranges = load_clipping_ranges(h5_path, "acc_x")
            widget = SeriesPlotWidget()
            # The areas are off by default; this test draws them.
            widget.show_sample_loss_areas = widget.show_clipping_areas = True
            widget.show_series(h5_path, "acc_x")

        np.testing.assert_array_equal(ranges, np.asarray([[1.0, 10.0]]))
        np.testing.assert_array_equal(clipping_ranges, np.asarray([[0.0, 1.0]]))
        (loss,), (clipping,) = widget.diagnostic_items(SAMPLE_LOSS), widget.diagnostic_items(CLIPPING)
        np.testing.assert_array_equal(loss.ranges, ranges)
        np.testing.assert_array_equal(clipping.ranges, clipping_ranges)
        self.assertGreater(loss.zValue(), 0)
        # One series: no band telling series apart.
        self.assertIsNone(loss.series_color)

    def test_every_series_of_a_line_card_gets_its_areas(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                for name, loss in (("first", None), ("second", [4.0, 6.0])):
                    group = h5_file.create_group(f"series/{name}")
                    group.attrs["time_dataset"] = "time_ms"
                    group.create_dataset("time_ms", data=np.asarray([0.0, 10.0]))
                    group.create_dataset("value", data=np.asarray([1.0, 2.0]))
                    if loss is not None:
                        failures = group.create_group("sample_loss_list")
                        failures.create_dataset("sample_loss_start_ms", data=np.asarray([loss[0]]))
                        failures.create_dataset("sample_loss_stop_ms", data=np.asarray([loss[1]]))
            widget = SeriesPlotWidget()
            widget.show_sample_loss_areas = True
            widget.show_series_entries([(h5_path, "first"), (h5_path, "second")])
        (item,) = widget.diagnostic_items(SAMPLE_LOSS)
        self.assertEqual(item.series_index, 1)
        # With several series, the band shows whose area it is.
        self.assertEqual(item.series_color, widget.series_colors()[1])
        widget.set_series_color(1, "#123456")
        self.assertEqual(item.series_color, "#123456")

    def test_marker_snaps_to_the_nearest_sample_of_any_series(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                for name, times in (("slow", [0.0, 10.0]), ("fast", [4.0, 5.0, 6.0])):
                    group = h5_file.create_group(f"series/{name}")
                    group.attrs["time_dataset"] = "time_s"
                    group.create_dataset("time_s", data=np.asarray(times))
                    group.create_dataset("value", data=np.arange(len(times), dtype=float))
            widget = SeriesPlotWidget()
            widget.show_series_entries([(h5_path, "slow"), (h5_path, "fast")])
        slow, fast = widget.all_current_data()
        # The first series' nearest sample is far away; the second series has one close by.
        self.assertEqual(widget.nearest_visible_x(fast.x[1] + 0.2), float(fast.x[1]))
        self.assertEqual(widget.nearest_visible_x(slow.x[-1] - 0.1), float(slow.x[-1]))

    def test_loads_legacy_sample_loss_list_for_existing_caches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                group = h5_file.create_group("series/acc_x")
                group.attrs["time_dataset"] = "time_ms"
                group.create_dataset("time_ms", data=np.asarray([1.0, 5.0]))
                group.create_dataset("value", data=np.asarray([1.0, 2.0]))
                failures = group.create_group("failure_list")
                failures.create_dataset("sample_loss_start_ms", data=np.asarray([1.0]))
                failures.create_dataset("sample_loss_stop_ms", data=np.asarray([5.0]))

            ranges = load_sample_loss_ranges(h5_path, "acc_x")

        np.testing.assert_array_equal(ranges, np.asarray([[1.0, 5.0]]))

    def test_diagnostic_areas_expand_to_the_minimum_span_and_merge_per_pixel(self):
        spans = visible_spans(np.asarray([[10.0, 10.0], [50.0, 60.0], [500.0, 501.0]]), 0.0, 100.0,
                              minimum_span=4.0, pixel_width=0.1)
        # Widened about the center; the range outside the view is left out.
        np.testing.assert_allclose(spans, [[8.0, 12.0], [50.0, 60.0]])
        # Thousands of ranges in a 100-pixel view: one span per pixel column.
        many = np.column_stack((np.arange(10_000) * 0.01, np.arange(10_000) * 0.01 + 0.001))
        merged = visible_spans(many, 0.0, 100.0, minimum_span=0.0, pixel_width=1.0)
        self.assertLessEqual(len(merged), 101)
        self.assertAlmostEqual(merged[0][0], 0.0)
        self.assertAlmostEqual(merged[-1][1], 99.991)

    def test_switches_from_lod_to_raw_samples_for_a_small_visible_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                group = h5_file.create_group("series/acc_x")
                group.attrs["time_dataset"] = "time_unix"
                group.create_dataset("time_unix", data=np.arange(16, dtype=float))
                group.create_dataset("value", data=np.arange(16, dtype=float))
                lod = group.create_group("lod/level_001")
                lod.attrs["bucket_count"] = 2
                lod.create_dataset("time_start_unix", data=np.asarray([0.0, 8.0]))
                lod.create_dataset("time_end_unix", data=np.asarray([7.0, 15.0]))
                lod.create_dataset("time_min_unix", data=np.asarray([2.0, 12.0]))
                lod.create_dataset("time_max_unix", data=np.asarray([5.0, 10.0]))
                lod.create_dataset("value_first", data=np.asarray([0.0, 8.0]))
                lod.create_dataset("value_last", data=np.asarray([7.0, 15.0]))
                lod.create_dataset("value_min", data=np.asarray([0.0, 8.0]))
                lod.create_dataset("value_max", data=np.asarray([7.0, 15.0]))

            data = load_plot_series(
                h5_path,
                "acc_x",
                maximum_points=8,
                x_range=(0.0, 7.0),
            )

        self.assertIsNone(data.lod_level)
        # Raw data include one sample beyond the visible right edge.
        np.testing.assert_array_equal(data.x, np.arange(9, dtype=float))
        np.testing.assert_array_equal(data.y, np.arange(9, dtype=float))

    def test_opening_a_series_autoranges_once_then_disables_autorange(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                group = h5_file.create_group("series/acc_x")
                group.attrs["time_dataset"] = "time_unix"
                group.create_dataset("time_unix", data=np.asarray([1.0, 2.0, 3.0]))
                group.create_dataset("value", data=np.asarray([4.0, 5.0, 6.0]))

            widget = SeriesPlotWidget()
            widget.show_series(h5_path, "acc_x")

        self.assertEqual(widget.plot_item.getViewBox().autoRangeEnabled(), [False, False])

    def test_lod_refresh_preserves_the_existing_view_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                group = h5_file.create_group("series/acc_x")
                group.attrs["time_dataset"] = "time_unix"
                group.create_dataset("time_unix", data=np.arange(100, dtype=float))
                group.create_dataset("value", data=np.arange(100, dtype=float))

            widget = SeriesPlotWidget()
            widget.show_series(h5_path, "acc_x")
            view_box = widget.plot_item.getViewBox()
            view_box.setRange(xRange=(20.0, 30.0), yRange=(10.0, 40.0), padding=0)
            widget.update_visible_data()

        x_range, y_range = view_box.viewRange()
        self.assertAlmostEqual(x_range[0], 20.0)
        self.assertAlmostEqual(x_range[1], 30.0)
        self.assertAlmostEqual(y_range[0], 10.0)
        self.assertAlmostEqual(y_range[1], 40.0)


class MarkerDataLoadCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_coalesces_pending_requests_and_detaches_running_loader_on_shutdown(self):
        class FakeLoader(QObject):
            loaded = Signal(int, float, object)
            finished = Signal()
            created: list["FakeLoader"] = []

            def __init__(self, request_id, x_value, entries, parent, **_kwargs):
                super().__init__(parent)
                self.request_id = request_id
                self.x_value = x_value
                self.entries = entries
                self.interrupted = False
                self.started = False
                self.__class__.created.append(self)

            def start(self):
                self.started = True

            def requestInterruption(self):
                self.interrupted = True

        parent = QWidget()
        coordinator = MarkerDataLoadCoordinator(parent)
        entries = [(Path("data.h5"), "acc_x")]
        with mock.patch("src.gui.plots.series.MarkerDataLoader", FakeLoader):
            coordinator.request(1.0, entries)
            coordinator.request(2.0, entries)
            coordinator.request(3.0, entries)
            self.assertEqual(len(FakeLoader.created), 1)

            FakeLoader.created[0].finished.emit()
            self.app.processEvents()
            self.assertEqual(len(FakeLoader.created), 2)
            self.assertEqual(FakeLoader.created[1].x_value, 3.0)

            running = FakeLoader.created[1]
            coordinator.shutdown()

        self.assertTrue(running.interrupted)
        self.assertIs(running.parent(), QCoreApplication.instance())
        self.assertIsNone(coordinator._active_loader)


if __name__ == "__main__":
    unittest.main()
