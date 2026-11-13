import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import h5py
import numpy as np
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QMenu, QDialogButtonBox

from src.data.frequency import FFTSettings, amplitude_spectrum, colour_values, requested_window_size
from src.gui.plots.frequency_data import load_frequency_data
from src.gui.plots.frequency import FrequencyPlotWidget, _LIVE_WORKERS
from src.gui.plots.frequency_settings import FrequencySettingsDialog
from src.gui.plots.interactive import TimeAxisItem
from src.app.lodgazilla import LODGaZillaWindow, PlotWorkspace


def write_signal(path, times, values, unit="s", start=None):
    with h5py.File(path, "w") as cache:
        if start is not None:
            cache.attrs["log_start_unix_seconds"] = start
        group = cache.create_group("series/Scope/Channel_A")
        group.attrs.update(time_dataset="time", time_unit=unit, time_reference="device_relative",
                           navigator_group="Scope", navigator_name="Channel A", unit="mV")
        group.create_dataset("time", data=times)
        group.create_dataset("value", data=values)
def values_text(card):
    """Text of the card's visible values boxes."""
    return "\n".join(box.toPlainText() for box in card.value_boxes.visible_boxes())


class FrequencyDataTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "signal.h5"
        self.times = np.arange(8192) / 4096
        self.values = 3 * np.sin(2 * np.pi * 512 * self.times)

    def test_window_gain_dc_and_nyquist_amplitudes(self):
        n = np.arange(1024)
        for window in ("Hann", "Hamming", "Rectangular"):
            settings = FFTSettings(window_size=1024, window=window, remove_mean=False)
            spectrum = amplitude_spectrum(2 + 3 * np.sin(2 * np.pi * n / 8) + 4 * (-1.0) ** n, settings)
            np.testing.assert_allclose(spectrum[[0, 128, 512]], [2, 3, 4], atol=1e-10)
        spectrum = amplitude_spectrum(np.full(1024, 9), FFTSettings())
        np.testing.assert_allclose(spectrum, 0, atol=1e-10)

    def test_time_units_and_canonical_alignment_do_not_change_frequencies(self):
        for unit, scale in (("s", 1), ("ms", 1000), ("us", 1e6)):
            with self.subTest(unit=unit):
                write_signal(self.path, self.times * scale - 6 * scale, self.values, unit, start=1800000000)
                stat = self.path.stat()
                data = load_frequency_data(self.path, "Scope/Channel A", FFTSettings(window_size=1024))
                self.assertAlmostEqual(data.sample_rate, 4096)
                self.assertEqual(data.amplitude.shape, (513, 15))
                np.testing.assert_allclose(data.amplitude[128], 3, rtol=1e-5)
                self.assertEqual(data.full_time_range[0], 1800000000)
                self.assertEqual(self.path.stat().st_mtime_ns, stat.st_mtime_ns)
                with h5py.File(self.path, "r") as cache:
                    self.assertEqual(list(cache["series/Scope/Channel_A"]), ["time", "value"])

    def test_frequency_change_has_time_on_columns_and_frequency_on_rows(self):
        values = np.sin(2 * np.pi * np.where(self.times < 1, 256, 1024) * self.times)
        write_signal(self.path, self.times, values)
        data = load_frequency_data(self.path, "Scope/Channel A", FFTSettings(window_size=1024))
        df = data.sample_rate / data.window_size
        self.assertEqual(np.argmax(data.amplitude[:, 0]) * df, 256)
        self.assertEqual(np.argmax(data.amplitude[:, -1]) * df, 1024)
        times, peaks, amplitudes = data.strongest_signal()
        self.assertEqual(peaks[0], 256)
        self.assertEqual(peaks[-1], 1024)
        self.assertTrue(np.all(np.diff(times) > 0))
        np.testing.assert_allclose(amplitudes[[0, -1]], 1, rtol=1e-5)

    def test_peak_track_excludes_band_edges_and_breaks_at_missing_or_silent_windows(self):
        write_signal(self.path, self.times, self.values)
        data = load_frequency_data(self.path, "Scope/Channel A", FFTSettings(window_size=1024))
        data = replace(data, first_frequency_hz=500, frequency_range=(502, 510), time_edges=(10, 20),
                       amplitude=np.array([[99, 99, 99, 99, 99], [3, 1, np.nan, 0, 2],
                                           [1, 4, np.nan, 0, 2], [99, 99, 99, 99, 99]]))
        times, peaks, amplitudes = data.strongest_signal()
        np.testing.assert_allclose(times, [11, 13, 15, 17, 19])
        np.testing.assert_allclose(peaks, [504, 508, np.nan, np.nan, 504])
        np.testing.assert_allclose(amplitudes, [3, 4, np.nan, np.nan, 2])
        # A band narrower than the bin spacing can contain no bin centres.
        self.assertTrue(np.isnan(replace(data, frequency_range=(505, 506)).strongest_signal()[1]).all())

    def test_missing_values_and_timestamp_gaps_remain_blank(self):
        values = self.values.copy()
        values[2048:3072] = np.nan
        times = self.times.copy()
        times[4096:] += 1
        write_signal(self.path, times, values)
        data = load_frequency_data(self.path, "Scope/Channel A", FFTSettings(window_size=512))
        self.assertGreater(data.skipped_windows, 0)
        columns = np.linspace(*data.time_edges, data.amplitude.shape[1], endpoint=False)
        self.assertTrue(np.isnan(data.amplitude[:, (columns > 1.1) & (columns < 1.9)]).all())
        self.assertTrue(np.isfinite(data.amplitude[:, -1]).all())

    def test_irregular_timing_is_rejected(self):
        times = np.cumsum(np.tile([0.001, 0.004], 4096))
        write_signal(self.path, times, self.values)
        with self.assertRaisesRegex(ValueError, "not regularly sampled"):
            load_frequency_data(self.path, "Scope/Channel A", FFTSettings())

    def test_overview_is_bounded_and_zoom_uses_dense_windows(self):
        write_signal(self.path, self.times, self.values)
        with mock.patch("src.gui.plots.frequency_data.MAX_TIME_COLUMNS", 4):
            full = load_frequency_data(self.path, "Scope/Channel A", FFTSettings(window_size=512))
            zoom = load_frequency_data(self.path, "Scope/Channel A", FFTSettings(window_size=512), (0.5, 0.75))
        self.assertLessEqual(full.amplitude.shape[1], 4)
        self.assertGreater(full.window_stride, 1)
        self.assertEqual(zoom.window_stride, 1)
        self.assertGreaterEqual(zoom.time_edges[0], 0.5)
        self.assertLessEqual(zoom.time_edges[1], 0.75)
        cancelled = Event()
        cancelled.set()
        self.assertIsNone(load_frequency_data(self.path, "Scope/Channel A", FFTSettings(), cancelled=cancelled))

    def test_db_reference_and_missing_window_colours(self):
        values, levels = colour_values(np.array([[1, 10, 0, np.nan]]), FFTSettings())
        np.testing.assert_allclose(values[0, :2], [0, 20])
        self.assertTrue(np.isfinite(values[0, 2]))
        self.assertTrue(np.isnan(values[0, 3]))
        self.assertEqual(levels, (-60, 20))

    def test_requested_resolution_uses_longer_real_windows_and_validates_inputs(self):
        self.assertEqual(requested_window_size(2_500_000, FFTSettings(target_bin_hz=100)), 32768)
        self.assertEqual(requested_window_size(2_500_000, FFTSettings(target_bin_hz=50)), 65536)
        self.assertEqual(requested_window_size(2_500_000, FFTSettings(target_bin_hz=0.01)), 262144)
        for options in ({"target_bin_hz": 0}, {"target_bin_hz": np.nan},
                        {"frequency_min_hz": -1}, {"frequency_min_hz": 5, "frequency_max_hz": 4},
                        {"frequency_max_hz": np.inf}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                FFTSettings(**options)

    def test_high_resolution_band_separates_close_tones_and_preserves_fft_amplitudes(self):
        rate = 2_500_000
        times = np.arange(131072) / rate
        # Two tones less than one original 2048-point bin apart, exactly on
        # the longer FFT's grid to check normalization independently of leakage.
        df = rate / 32768
        frequencies = np.array([720, 725]) * df
        values = 3 * np.sin(2 * np.pi * frequencies[0] * times) + 2 * np.sin(2 * np.pi * frequencies[1] * times)
        write_signal(self.path, times, values)
        settings = FFTSettings(target_bin_hz=100)
        full = load_frequency_data(self.path, "Scope/Channel A", settings)
        band = load_frequency_data(self.path, "Scope/Channel A", replace(
            settings, frequency_min_hz=50000, frequency_max_hz=60000))
        self.assertEqual(band.window_size, 32768)
        self.assertEqual(band.frequency_range, (50000, 60000))
        first_bin = round(band.first_frequency_hz / df)
        np.testing.assert_allclose(band.amplitude, full.amplitude[first_bin:first_bin + len(band.amplitude)])
        np.testing.assert_allclose(band.amplitude[720 - first_bin], 3, rtol=1e-5)
        np.testing.assert_allclose(band.amplitude[725 - first_bin], 2, rtol=1e-5)
        self.assertLess(band.amplitude.shape[0], 140)
        self.assertGreater(band.amplitude.shape[0], 125)

    def test_selected_band_uses_image_budget_and_short_ranges_report_actual_window(self):
        write_signal(self.path, self.times, self.values)
        settings = FFTSettings(window_size=1024, target_bin_hz=4)
        with mock.patch("src.gui.plots.frequency_data.MAX_IMAGE_CELLS", 1026):
            full = load_frequency_data(self.path, "Scope/Channel A", settings)
            band = load_frequency_data(self.path, "Scope/Channel A", replace(
                settings, frequency_min_hz=500, frequency_max_hz=540))
        self.assertLessEqual(full.amplitude.size, 1026)
        self.assertLessEqual(band.amplitude.size, 1026)
        self.assertGreater(band.amplitude.shape[1], full.amplitude.shape[1])
        short = load_frequency_data(self.path, "Scope/Channel A", settings, (0, 0.01))
        self.assertEqual(short.window_size, 32)
        self.assertEqual(short.sample_rate / short.window_size, 128)
        with self.assertRaisesRegex(ValueError, "above Nyquist"):
            load_frequency_data(self.path, "Scope/Channel A", replace(settings, frequency_min_hz=5000))


class FrequencyCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "signal.h5"
        times = np.arange(8192) / 4096
        write_signal(self.path, times, np.sin(2 * np.pi * 512 * times))
        self.workspace = PlotWorkspace()
        self.workspace.resize(900, 600)
        self.workspace.show()
        self.addCleanup(self.cleanup_workspace)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            # Release the GIL for the Python FFT worker as well as pumping Qt.
            time.sleep(0.01)
        self.assertTrue(predicate())

    def cleanup_workspace(self):
        self.workspace.close_all_plots()
        self.wait_for(lambda: not _LIVE_WORKERS)
        self.workspace.close()
        self.workspace.deleteLater()
        self.app.processEvents()

    def test_relative_time_axis_preserves_millisecond_and_microsecond_ticks(self):
        axis = TimeAxisItem()
        axis.configure(raw_origin=-6, seconds_per_unit=0.001, log_start_unix_seconds=None, is_unix_time=False)
        self.assertEqual(axis.tickStrings([-6, 14, 34], 1, 20), ["0.00sec", "0.02sec", "0.04sec"])
        axis.configure(raw_origin=0, seconds_per_unit=0.001, log_start_unix_seconds=None, is_unix_time=False)
        self.assertEqual(axis.tickStrings([0, 0.0004, 0.0008], 1, 0.0004),
                         ["0.0000000sec", "0.0000004sec", "0.0000008sec"])

    def test_card_renders_and_links_time_axes_markers_and_navigator(self):
        line = self.workspace.add_line_plot(self.path, "Scope/Channel A")
        card = self.workspace.add_frequency_plot(self.path, "Scope/Channel A")
        self.wait_for(lambda: card.data is not None and card._worker is None and not card._timer.isActive())
        self.assertIsInstance(card, FrequencyPlotWidget)
        self.assertEqual(self.workspace._plot_navigator_type(card), "frequency")
        self.assertEqual(card.image.image.shape, card.data.amplitude.shape)
        self.assertEqual(card.plot_item.getAxis("left").labelUnits, "Hz")
        self.assertGreaterEqual(card.height(), 160)
        line.set_time_axis_representation("log_elapsed")
        self.assertEqual(card.time_axis_representation(), "log_elapsed")
        self.workspace.set_markers_linked(True)
        self.workspace.on_marker_placed(line, 0.5)
        self.assertAlmostEqual(card.plot_widget.vertical_marker.value(), 0.5)
        line.plot_view_box().setXRange(0.5, 1.0, padding=0)
        self.wait_for(lambda: card.data is not None and card.data.time_edges[0] >= 0.5)
        self.assertLessEqual(card.data.time_edges[1], 1.01)

    def test_fft_context_controls_recalculate_and_display_only_controls_reuse_data(self):
        card = self.workspace.add_frequency_plot(self.path, "Scope/Channel A")
        self.wait_for(lambda: card.data is not None)
        menu = card.build_fft_menu()
        size_menu = next(a.menu() for a in menu.actions() if a.text() == "FFT window size")
        next(a for a in size_menu.actions() if a.text() == "512").trigger()
        self.wait_for(lambda: card.data.window_size == 512)
        data = card.data
        card.set_fft_settings(replace(card.settings, decibels=False))
        self.assertIs(card.data, data)
        np.testing.assert_allclose(card.image.image, data.amplitude)
        menu.deleteLater()

    def test_latest_request_wins_and_closing_does_not_wait_for_a_worker(self):
        entered, release = Event(), Event()
        from src.gui.plots.frequency import load_frequency_data as real_load
        calls = []
        def delayed(*args):
            calls.append(args[2].window_size)
            if len(calls) == 1:
                entered.set()
                release.wait(5)
            return real_load(*args)
        with mock.patch("src.gui.plots.frequency.load_frequency_data", side_effect=delayed):
            card = self.workspace.add_frequency_plot(self.path, "Scope/Channel A")
            self.wait_for(entered.is_set)
            card.set_fft_settings(replace(card.settings, window_size=512))
            card.set_fft_settings(replace(card.settings, window_size=1024))
            release.set()
            self.wait_for(lambda: card.data is not None and card.data.window_size == 1024)
            self.assertNotIn(512, calls)
        entered.clear()
        release.clear()
        def blocked(*args):
            entered.set()
            release.wait(5)
            return None
        with mock.patch("src.gui.plots.frequency.load_frequency_data", side_effect=blocked):
            card.set_fft_settings(replace(card.settings, window_size=512))
            self.wait_for(entered.is_set)
            started = time.monotonic()
            self.workspace.remove_plot(card)
            self.assertLess(time.monotonic() - started, 1)
            release.set()
            self.wait_for(lambda: not _LIVE_WORKERS)

    def test_peak_line_controls_and_marker_popup_use_same_frequency_without_recomputing(self):
        card = self.workspace.add_frequency_plot(self.path, "Scope/Channel A")
        self.wait_for(lambda: card.data is not None and card._worker is None and not card._timer.isActive())
        data, revision = card.data, card._revision
        self.assertTrue(card.peak_line.isVisible())
        self.assertEqual(card.peak_line.opts["pen"].color().name(), "#ffffff")
        self.assertGreater(card.peak_line.zValue(), card.image.zValue())
        np.testing.assert_allclose(card.peak_line.getData()[1], 512)
        menu = card.build_fft_menu()
        self.addCleanup(menu.deleteLater)
        toggle = next(a for a in menu.actions() if a.text() == "Show strongest frequency")
        toggle.trigger()
        self.assertFalse(card.peak_line.isVisible())
        color_action = next(a for a in menu.actions() if a.text() == "Strongest-frequency line colour…")
        with mock.patch("src.gui.plots.frequency.QColorDialog.getColor", return_value=QColor("#ff5500")):
            color_action.trigger()
        with mock.patch("src.gui.plots.frequency.QColorDialog.getColor", return_value=QColor()):
            color_action.trigger()
        toggle.trigger()
        self.assertTrue(card.peak_line.isVisible())
        self.assertEqual(card.peak_line.opts["pen"].color().name(), "#ff5500")
        self.assertIs(card.data, data)
        self.assertEqual(card._revision, revision)
        self.assertIsNone(card._worker)

        card.set_peak_line_visible(False)
        self.workspace.on_marker_placed(card, 0.5)
        self.assertTrue(card.value_boxes.visible_boxes())
        self.assertEqual(values_text(card), "512 Hz")
        box = card.value_boxes.visible_boxes()[0]
        self.assertIn("Strongest frequency: 512 Hz", box.details_text())
        card.set_fft_settings(replace(card.settings, window_size=1024))
        self.wait_for(lambda: card.data.window_size == 1024)
        self.assertIn("512 Hz", values_text(card))
        self.assertFalse(card.peak_line.isVisible())
        self.workspace.set_marker_info_display("off")
        self.assertFalse(card.value_boxes.visible_boxes())
        card.set_fft_settings(replace(card.settings, window_size=512))
        self.wait_for(lambda: card.data.window_size == 512)
        self.assertFalse(card.value_boxes.visible_boxes())
        self.workspace.set_marker_info_display("active")
        self.assertTrue(card.value_boxes.visible_boxes())
        card.clear_vertical_marker()
        self.assertFalse(card.value_boxes.visible_boxes())

    def test_peak_marker_hides_for_gaps_and_refreshes_after_new_data(self):
        card = self.workspace.add_frequency_plot(self.path, "Scope/Channel A")
        self.wait_for(lambda: card.data is not None and card._worker is None and not card._timer.isActive())
        original = card.data
        amplitude = original.amplitude.copy()
        amplitude[:, 1] = np.nan
        card._show_data(replace(original, amplitude=amplitude))
        x = card._peak_times[1]
        self.assertTrue(np.isnan(card.peak_line.getData()[1][2]))
        card.show_marker_data(x)
        self.assertFalse(card.value_boxes.visible_boxes())
        card._show_data(original)
        self.assertTrue(card.value_boxes.visible_boxes())
        self.assertIn("512 Hz", values_text(card))
        card.show_marker_data(original.time_edges[1] + 1)
        self.assertFalse(card.value_boxes.visible_boxes())
        card.show_marker_data(x)
        card.set_data_time_range((100, 101))
        self.wait_for(lambda: card.data is None)
        self.assertFalse(card.value_boxes.visible_boxes())
        self.assertIsNone(card.peak_line.getData()[1])

    def test_peak_threshold_filters_linear_amplitude_with_gaps_without_changing_marker_values(self):
        card = self.workspace.add_frequency_plot(self.path, "Scope/Channel A")
        self.wait_for(lambda: card.data is not None and card._worker is None and not card._timer.isActive())
        amplitude = np.zeros_like(card.data.amplitude)
        amplitude[256, :] = [20, 19, 25, 10, 21, 0, np.nan]
        data = replace(card.data, amplitude=amplitude)
        card._show_data(data)
        revision = card._revision
        menu = card.build_fft_menu()
        self.addCleanup(menu.deleteLater)
        action = next(a for a in menu.actions() if a.text().startswith("Strongest-frequency line threshold"))
        self.assertIn("0 mV", action.text())
        with mock.patch("src.gui.plots.frequency.QInputDialog.getDouble", return_value=(20, True)) as dialog:
            action.trigger()
        self.assertIn("mV", dialog.call_args.args[2])
        expected = [512, np.nan, 512, np.nan, 512, np.nan, np.nan]
        np.testing.assert_allclose(card.peak_line.getData()[1][1:-1], expected)
        self.assertTrue(np.isnan(card.peak_line.getData()[1][-1]))
        self.assertIs(card.data, data)
        self.assertEqual(card._revision, revision)
        self.assertIsNone(card._worker)
        np.testing.assert_allclose(data.amplitude, amplitude)
        card.show_marker_data(card._peak_times[1])
        self.assertIn("512 Hz", values_text(card))
        card.set_fft_settings(replace(card.settings, decibels=False))
        np.testing.assert_allclose(card.peak_line.getData()[1][1:-1], expected)
        card._show_data(data)
        np.testing.assert_allclose(card.peak_line.getData()[1][1:-1], expected)
        with mock.patch("src.gui.plots.frequency.QInputDialog.getDouble", return_value=(0, False)):
            action.trigger()
        self.assertEqual(card.peak_line_threshold, 20)
        card.set_peak_line_threshold(30)
        self.assertTrue(np.isnan(card.peak_line.getData()[1]).all())
        card.set_peak_line_visible(False)
        card.set_peak_line_threshold(0)
        self.assertFalse(card.peak_line.isVisible())
        np.testing.assert_allclose(card.peak_line.getData()[1][1:-1], [512] * 5 + [np.nan, np.nan])
        for invalid in (-1, np.nan, np.inf):
            with self.assertRaises(ValueError):
                card.set_peak_line_threshold(invalid)

    def test_navigator_context_menu_opens_frequency_card(self):
        from tests.plot_menu_helpers import wait_for_plot_menu
        entry = (self.path, "Scope/Channel A")
        navigator = SimpleNamespace(selected_series_entries=lambda: [entry], tree=mock.Mock())
        window = SimpleNamespace(navigator=navigator, plot_workspace=self.workspace)
        class TestMenu(QMenu):
            def exec(self, _position):
                pass
        menu = TestMenu()
        with mock.patch("src.app.lodgazilla.QMenu", return_value=menu):
            LODGaZillaWindow.show_series_context_menu(window, *entry, QPoint())
        wait_for_plot_menu(menu)
        next(action for action in menu.actions() if action.data() == "frequency").trigger()
        self.assertIsInstance(self.workspace.plots()[0], FrequencyPlotWidget)
        menu.deleteLater()

    def test_frequency_dialog_preview_and_band_zoom_survive_recalculation(self):
        dialog = FrequencySettingsDialog(FFTSettings(), 2_500_000)
        self.addCleanup(dialog.deleteLater)
        dialog.full_range.setChecked(False)
        dialog.minimum.setValue(50000)
        dialog.maximum.setValue(60000)
        dialog.target_enabled.setChecked(True)
        dialog.target.setValue(100)
        self.assertEqual(dialog.selected_settings().frequency_max_hz, 60000)
        self.assertIn("76.2939 Hz", dialog.preview.text())
        self.assertIn("13.1072 ms", dialog.preview.text())
        dialog.maximum.setValue(40000)
        self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())

        card = self.workspace.add_frequency_plot(self.path, "Scope/Channel A")
        self.wait_for(lambda: card.data is not None)
        card.set_fft_settings(replace(card.settings, frequency_min_hz=500, frequency_max_hz=600, target_bin_hz=4))
        self.wait_for(lambda: card.data is not None and card.data.frequency_range == (500, 600))
        np.testing.assert_allclose(card.plot_view_box().viewRange()[1], [500, 600])
        df = card.data.sample_rate / card.data.window_size
        self.assertAlmostEqual(card.image.mapRectToParent(card.image.boundingRect()).top(), card.data.first_frequency_hz - df / 2)
        card.show_marker_data(0.5)
        self.assertIn("512 Hz", values_text(card))
        card.plot_view_box().setYRange(510, 550, padding=0)
        card.set_fft_settings(replace(card.settings, overlap=0.75))
        self.wait_for(lambda: card._worker is None and not card._timer.isActive())
        np.testing.assert_allclose(card.plot_view_box().viewRange()[1], [510, 550])
        card._set_fft_option("window_size", 512)
        self.assertIsNone(card.settings.target_bin_hz)
        card.full_frequency_range()
        self.wait_for(lambda: card.data is not None and card.data.frequency_range == (0, 2048))
        np.testing.assert_allclose(card.plot_view_box().viewRange()[1], [0, 2048])

    def test_unreachable_target_is_reported_in_the_app_log(self):
        logged = []
        self.workspace.plot_status_changed.connect(logged.append)
        card = self.workspace.add_frequency_plot(self.path, "Scope/Channel A")
        self.wait_for(lambda: card.data is not None)
        card.set_fft_settings(replace(card.settings, target_bin_hz=0.01))
        self.wait_for(lambda: logged and "not reached" in logged[-1])
        self.assertEqual(card.data.window_size, 8192)
        self.assertIn("Bin spacing 0.5 Hz", logged[-1])
        self.assertTrue(logged[-1].startswith("Frequency — "))
        # A redraw with the same details logs nothing new.
        count = len(logged)
        card._log_status(card._logged_status)
        self.assertEqual(len(logged), count)


if __name__ == "__main__":
    unittest.main()
