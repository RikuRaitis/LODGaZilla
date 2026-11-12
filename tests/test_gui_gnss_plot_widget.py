import os
import sys
import unittest
from pathlib import Path
import tempfile
from unittest import mock
from types import SimpleNamespace, MethodType

import h5py
import numpy as np
import pyqtgraph as pg

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.gui.plots.event_state import (  # noqa: E402
    EventStatePlotWidget,
    is_event_state_series,
    load_event_state_series,
)
from src.gui.plots.gnss import GnssPlotWidget, _normalise_coordinate  # noqa: E402
from tests.qt_wait import dispose_cards  # noqa: E402


class GnssCoordinateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        # The Event/state cards here are top-level; stop their loaders and delete them now.
        dispose_cards()

    def test_playback_markers_wait_for_ready_and_coalesce(self):
        page = mock.Mock()
        card = SimpleNamespace(draw_style="auto", _map_ready=False, _map_generation=0, _marker_times={0: None, 1: None}, _value_box_times={0: None, 1: None}, value_boxes=mock.Mock(),
                               _marker_revision=0, _marker_in_flight=False,
                               map_view=mock.Mock(), map_stack=mock.Mock(), map_loading=mock.Mock(),
                               map_type=mock.Mock(), legend=mock.Mock(), satellite_radio=mock.Mock(),
                               close=mock.Mock(), _track_data=[],
                               palette=QApplication.palette, map_container=mock.Mock())
        card.map_view.page.return_value = page
        card.satellite_radio.isChecked.return_value = False
        for name in ('set_vertical_marker', 'clear_vertical_marker', '_flush_marker',
                     '_marker_delivered', '_on_map_loaded', '_on_map_ready',
                     '_refresh_value_box_positions', '_request_value_box_positions', 'hide_marker_data', '_markers_ready',
                     'marker_values', '_update_selection', '_set_map_type', '_push_tracks'):
            setattr(card, name, MethodType(getattr(GnssPlotWidget, name), card))
        try:
            for timestamp in range(100):
                card.set_vertical_marker(timestamp)
            card._refresh_value_box_positions()
            card._set_map_type('Satellite')
            page.runJavaScript.assert_not_called()
            card._on_map_loaded(True)
            probe = page.runJavaScript.call_args
            self.assertEqual(probe.args[0], 'window.gnssReady === true')
            probe.args[1](True)
            self.assertIn('setMarkers([99.0, null]', page.runJavaScript.call_args.args[0])
            delivered = page.runJavaScript.call_args.args[1]
            page.runJavaScript.reset_mock()
            card.set_vertical_marker(100)
            card.set_vertical_marker(101)
            page.runJavaScript.assert_not_called()
            delivered(None)
            page.runJavaScript.assert_called_once()
            self.assertIn('setMarkers([101.0, null]', page.runJavaScript.call_args.args[0])
            old_delivery = page.runJavaScript.call_args.args[1]
            card._map_ready = False
            card._map_generation += 1
            card._marker_in_flight = False
            card.clear_vertical_marker()
            page.runJavaScript.reset_mock()
            old_delivery(None)
            page.runJavaScript.assert_not_called()
            card._on_map_ready(card._map_generation - 1, True)
            self.assertFalse(card._map_ready)
            card._on_map_ready(card._map_generation, True)
            self.assertIn('setMarkers([null, null]', page.runJavaScript.call_args.args[0])
            pending_delivery = page.runJavaScript.call_args.args[1]
            generation = card._map_generation
            card.set_vertical_marker(102)
            GnssPlotWidget.dispose(card)
            page.runJavaScript.reset_mock()
            pending_delivery(None)
            card._on_map_ready(generation, True)
            card._on_map_loaded(True)
            GnssPlotWidget._initialize_map(card)
            page.runJavaScript.assert_not_called()
            self.assertIsNone(card.map_view)
            self.assertFalse(card._map_ready)
        finally:
            card.close()

    def test_normalises_legacy_fixed_point_coordinates_for_map(self):
        # One scale applies to a whole series (see tests/test_review_gnss_xy.py).
        for values, maximum, expected in (
            ([602.72, -602.72], 90.0, [60.272, -60.272]),
            ([249_720_000.0, -249_720_000.0], 180.0, [24.972, -24.972]),
            ([249.72, 24.972], 180.0, [24.972, 2.4972]),
            ([60.272, 24.972], 90.0, [60.272, 24.972]),
        ):
            np.testing.assert_allclose(_normalise_coordinate(np.asarray(values), maximum), expected)

    def test_event_state_plot_renders_state_intervals_from_h5_labels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                h5_file.attrs["log_start_unix_seconds"] = 1_700_000_000.0
                h5_file.attrs["time_start_seconds"] = 0.05
                h5_file.attrs["time_end_seconds"] = 0.5
                group = h5_file.create_group("series/is_wet")
                group.attrs["time_dataset"] = "time_ms"
                group.attrs["time_unit"] = "ms"
                group.attrs["time_reference"] = "device_relative"
                group.attrs["unit"] = "bool"
                group.attrs["state_labels_json"] = '{"0": "Dry", "1": "Wet"}'
                group.create_dataset("time_ms", data=np.asarray([100.0, 200.0, 300.0]))
                group.create_dataset("value", data=np.asarray([0.0, 1.0, 0.0]))
                plot = EventStatePlotWidget(h5_path, "is_wet")

            self.assertEqual(len(plot._timeline_data), 1)
            self.assertGreater(len(plot._items), 1)
            self.assertTrue(plot._contains_time(1_700_000_000.15))
            self.assertFalse(plot._contains_time(1_700_000_001.0))
            self.assertAlmostEqual(float(plot._timeline_data[0]["h5_start"]), 1_700_000_000.0)
            self.assertAlmostEqual(float(plot._timeline_data[0]["h5_end"]), 1_700_000_000.45)
            self.assertTrue(is_event_state_series(h5_path, "is_wet"))
            self.assertEqual(len(plot.legend.rows), 1)


    def test_event_state_plot_keeps_two_event_series_in_legend_and_y_axis(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                h5_file.attrs["log_start_unix_seconds"] = 1_700_000_000.0
                series_root = h5_file.create_group("series")
                for name, values in (("bookmark_a", [1.0, 2.0]), ("bookmark_b", [3.0, 4.0])):
                    group = series_root.create_group(name)
                    group.attrs["time_dataset"] = "time_ms"
                    group.attrs["time_unit"] = "ms"
                    group.attrs["time_reference"] = "device_relative"
                    group.attrs["navigator_name"] = name
                    group.attrs["unit"] = "index"
                    group.create_dataset("time_ms", data=np.asarray([100.0, 200.0]))
                    group.create_dataset("value", data=np.asarray(values))
                plot = EventStatePlotWidget(h5_path, "bookmark_a")
                plot.resize(640, 180)
                plot.show()
                self.app.processEvents()
                plot.add_series(h5_path, "bookmark_b")
                self.app.processEvents()

            self.assertEqual(len(plot._timeline_data), 2)
            self.assertEqual(len(plot.legend.rows), 2)
            self.assertTrue(plot.legend.isVisible())
            self.assertGreater(plot.legend.width(), 0)
            self.assertLess(plot.legend.x(), plot.plot_widget.width())
            self.assertEqual(
                [label.text() for label in plot._lane_axis_labels],
                ["bookmark_a", "bookmark_b"],
            )

    def test_event_series_uses_file_time_origin_not_its_first_sample(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                h5_file.attrs["log_start_unix_seconds"] = 1_700_000_000.0
                h5_file.attrs["time_start_seconds"] = 6.0
                h5_file.attrs["time_end_seconds"] = 8.0
                group = h5_file.create_group("series/is_wet")
                group.attrs["time_dataset"] = "time_ms"
                group.attrs["time_unit"] = "ms"
                group.attrs["time_reference"] = "device_relative"
                group.attrs["unit"] = "bool"
                group.create_dataset("time_ms", data=np.asarray([6200.0, 6500.0]))
                group.create_dataset("value", data=np.asarray([0.0, 1.0]))

            data = load_event_state_series(h5_path, "is_wet")
            np.testing.assert_allclose(data["time"], [1_700_000_000.2, 1_700_000_000.5])
            self.assertAlmostEqual(float(data["h5_start"]), 1_700_000_000.0)
            self.assertAlmostEqual(float(data["h5_end"]), 1_700_000_002.0)

    def test_event_series_window_keeps_transitions_on_both_sides(self):
        """A state window needs its surrounding transitions to draw correctly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                group = h5_file.create_group("series/is_wet")
                group.attrs["time_dataset"] = "time_unix"
                group.attrs["unit"] = "bool"
                group.create_dataset("time_unix", data=np.asarray([0.0, 10.0, 20.0, 30.0]))
                group.create_dataset("value", data=np.asarray([0.0, 1.0, 0.0, 1.0]))

            data = load_event_state_series(h5_path, "is_wet", time_range=(11.0, 19.0))

        # 10 establishes the state at the left boundary and 20 closes it.
        np.testing.assert_array_equal(data["time"], np.asarray([10.0, 20.0]))
        np.testing.assert_array_equal(data["value"], np.asarray([1.0, 0.0]))

    def test_event_plot_caps_text_items_to_available_pixel_width(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                group = h5_file.create_group("series/bookmark_index")
                group.attrs["time_dataset"] = "time_unix"
                group.attrs["unit"] = "index"
                group.create_dataset("time_unix", data=np.arange(10_000, dtype=float))
                group.create_dataset("value", data=np.arange(10_000, dtype=float))

            plot = EventStatePlotWidget(h5_path, "bookmark_index")
            plot.resize(420, 160)
            self.app.processEvents()

        text_item_count = sum(isinstance(item, pg.TextItem) for item in plot._items)
        self.assertLess(text_item_count, 100)
