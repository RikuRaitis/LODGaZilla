"""Card headers that wrap, Frequency card labels, 3D plot axis labels and map values boxes."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import h5py
import numpy as np

from src.gui.plots.frequency import FrequencyPlotWidget, _LIVE_WORKERS, ordinal
from src.gui.plots.gnss import MapMarkersMixin, page_positions
from src.gui.plots.markers import FIRST_MARKER, SECOND_MARKER
from src.gui.plots.xyz import axis_tick_label
from tests.qt_wait import qt_wait_until
from tests.test_review_plots import _QtTestCase


class LabelTextTests(unittest.TestCase):
    def test_ordinal(self):
        cases = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 11: "11th", 12: "12th", 13: "13th",
                 21: "21st", 22: "22nd", 23: "23rd", 101: "101st", 111: "111th", 112: "112th"}
        for number, text in cases.items():
            self.assertEqual(ordinal(number), text)

    def test_axis_tick_label(self):
        self.assertEqual(axis_tick_label("X", -8, 8, 0), "X -8")
        self.assertEqual(axis_tick_label("Y", -8, 8, 0.5), "Y 0")
        self.assertEqual(axis_tick_label("Z", -8, 8, 1), "Z 8")
        # Small differences around a large offset (Unix time) are kept.
        self.assertEqual(axis_tick_label("X", 1793682900.0, 1793682960.0, 0.5), "X 1793682930")

    def test_map_page_positions_arrive_as_json_text_or_a_list(self):
        # PySide6 6.11 hands a JavaScript array to the callback as an empty string, so the page sends JSON.
        self.assertEqual(page_positions("[[288,211],null]"), [[288, 211], None])
        self.assertEqual(page_positions([[1, 2]]), [[1, 2]])
        self.assertEqual(page_positions(""), [])
        self.assertEqual(page_positions(None), [])
        self.assertEqual(page_positions('{"a": 1}'), [])


class _Layer:
    def __init__(self):
        self.cleared = []

    def clear(self, index):
        self.cleared.append(index)


class _MapCard(MapMarkersMixin):
    """The parts of a map card that ``show_marker_data`` uses, without a web page."""

    def __init__(self, tracks):
        self._track_data = tracks
        self._value_box_times = {FIRST_MARKER: None, SECOND_MARKER: None}
        self.value_boxes = _Layer()
        self.requests = []

    def _request_value_box_positions(self, index):
        self.requests.append(index)


class MapValueBoxTests(unittest.TestCase):
    def test_a_marker_set_before_the_tracks_load_is_kept_for_them(self):
        card = _MapCard([])
        card.show_marker_data(100.0, FIRST_MARKER)
        self.assertEqual(card._value_box_times[FIRST_MARKER], 100.0)
        self.assertEqual(card.value_boxes.cleared, [])

    def test_a_marker_outside_every_loaded_track_hides_its_boxes(self):
        card = _MapCard([{"time": [0.0, 10.0], "lat": [60.0, 60.1], "lon": [24.0, 24.1]}])
        card.show_marker_data(5.0, SECOND_MARKER)
        self.assertEqual(card._value_box_times[SECOND_MARKER], 5.0)
        card.show_marker_data(50.0, SECOND_MARKER)
        self.assertIsNone(card._value_box_times[SECOND_MARKER])
        self.assertEqual(card.value_boxes.cleared, [SECOND_MARKER])


class CardHeaderTests(_QtTestCase):
    def test_header_controls_that_do_not_fit_continue_on_a_row_below(self):
        from src.gui.plots.line import LinePlotWidget

        card = LinePlotWidget(self.make_cache(), "a", settings_path=self.directory / "settings.json")
        self.widgets.append(card)
        card.resize(900, 300)
        card.show()
        self.app.processEvents()
        controls = [control for control in card.header_controls if not control.isHidden()]
        row_bottom = card.close_button.geometry().bottom()
        self.assertTrue(all(control.y() < row_bottom for control in controls))
        needed = sum(control.width() + 2 for control in controls)
        card.resize(card.close_button.width() + 16 + needed // 2, 300)
        self.app.processEvents()
        for control in controls:
            self.assertGreaterEqual(control.x(), 8, control.accessibleName())
            self.assertLessEqual(control.geometry().right(), card.width(), control.accessibleName())
        # The ones that did not fit moved below the header row, aligned from the right.
        below = [control for control in controls if control.y() > row_bottom]
        self.assertTrue(below)
        self.assertEqual(max(control.geometry().right() for control in below), card.close_button.geometry().right())


class FrequencyLabelTests(_QtTestCase):
    def tearDown(self):
        qt_wait_until(lambda: not _LIVE_WORKERS, 10000)
        super().tearDown()

    def make_signal(self):
        path = self.directory / "signal.h5"
        with h5py.File(path, "w") as cache:
            group = cache.create_group("series/signal")
            group.attrs.update(time_dataset="time", time_unit="ms", time_reference="device_relative",
                               navigator_name="signal", unit="mV")
            group.create_dataset("time", data=np.arange(8192) / 4096 * 1000)
            group.create_dataset("value", data=np.sin(2 * np.pi * 512 * np.arange(8192) / 4096))
        return path

    def test_time_axis_has_no_label_and_the_colour_bar_label_fits(self):
        card = FrequencyPlotWidget(self.make_signal(), "signal")
        self.widgets.append(card)
        card.resize(700, 500)
        card.show()
        self.assertTrue(qt_wait_until(lambda: card.data is not None, 10000))
        # A label on the time axis showed an SI multiplier, such as "(x1e+09)", for Unix times.
        self.assertEqual(card.plot_item.getAxis("bottom").labelText, "")
        self.assertEqual(card.colour_bar.axis.labelText, "Amplitude (dB re 1 mV)")
        card.resize(700, 150)
        self.assertTrue(qt_wait_until(lambda: card.colour_bar.axis.labelText == "dB", 5000))
        card.resize(700, 500)
        self.assertTrue(qt_wait_until(lambda: card.colour_bar.axis.labelText == "Amplitude (dB re 1 mV)", 5000))


if __name__ == "__main__":
    unittest.main()
