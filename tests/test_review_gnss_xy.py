"""Regression tests for review findings in GNSS, X/Y, frequency and image cards."""

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import h5py
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QSize, Qt, QThreadPool  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from tests.qt_wait import qt_wait  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.data.frequency import FFTSettings  # noqa: E402
from src.data.xyz import AxisSource  # noqa: E402
from src.gui.plots.frequency_data import load_frequency_data  # noqa: E402
from src.gui.plots.gnss import GnssPlotWidget, load_gnss_track, map_html, script_json  # noqa: E402
from src.gui.plots.gnss_3d import Gnss3DPlotWidget, _three_d_map_html, load_3d_gnss_group  # noqa: E402
from src.gui.plots.image import ImageCanvas, decode_image  # noqa: E402
from src.gui.plots.theme import canvas_background  # noqa: E402
from src.gui.plots.xy import XYPlotWidget, _result_contains_time, load_xy_data  # noqa: E402
from src.jobs.plot_groups import _Load  # noqa: E402
from tests.test_gui_gnss_3d_plot_widget import ScriptMapView  # noqa: E402


def add_series(handle, name, times, values, *, time_dataset="time_unix", time_unit=None,
               time_reference=None, unit=""):
    group = handle.create_group(f"series/{name}")
    group.attrs["time_dataset"] = time_dataset
    if time_unit:
        group.attrs["time_unit"] = time_unit
    if time_reference:
        group.attrs["time_reference"] = time_reference
    group.attrs["unit"] = unit
    if "/" in name:
        group.attrs["navigator_group"], group.attrs["navigator_name"] = name.rsplit("/", 1)
    group.create_dataset(time_dataset, data=np.asarray(times, dtype=float))
    group.create_dataset("value", data=np.asarray(values, dtype=float))
    return group


class _TempCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.addCleanup(lambda: QThreadPool.globalInstance().waitForDone(3000))

    def wait_loaded(self, widget):
        for _ in range(500):
            if not widget._loading:
                return
            qt_wait(10)
        self.fail("Card did not finish loading")


class GnssTrackTests(_TempCase):
    def test_r4_explicit_units_cover_zero_crossings_and_radians_in_both_maps(self):
        path = self.folder / "units.h5"
        times = [1., 2., 3.]
        with h5py.File(path, "w") as handle:
            for prefix, unit, latitude, longitude in (
                ("Raw", "raw", [-10., 0., 10.], [-20., 0., 20.]),
                ("Radians", "rad", [np.pi/3]*3, [np.pi/6]*3),
                ("Degrees", "deg", [60.]*3, [30.]*3),
                ("Legacy", "deg*10", [85.]*3, [10.]*3),
            ):
                add_series(handle, prefix + "/latitude", times, latitude, unit=unit)
                add_series(handle, prefix + "/longitude", times, longitude, unit=unit)
            add_series(handle, "Degrees/bad_latitude", times, [120.]*3, unit="deg")
        for prefix, expected_lat, expected_lon in (
            ("Raw", [-.000001, 0., .000001], [-.000002, 0., .000002]),
            ("Radians", [60.]*3, [30.]*3), ("Degrees", [60.]*3, [30.]*3),
            ("Legacy", [8.5]*3, [1.]*3),
        ):
            with self.subTest(prefix=prefix):
                lat, lon = prefix + "/latitude", prefix + "/longitude"
                track = load_gnss_track(path, lat, path, lon)
                np.testing.assert_allclose(track["lat"], expected_lat)
                np.testing.assert_allclose(track["lon"], expected_lon)
                spatial = load_3d_gnss_group((AxisSource(path, lat), AxisSource(path, lon), None))
                np.testing.assert_allclose(np.asarray(spatial["coordinates"])[:, :2],
                                           np.column_stack((expected_lon, expected_lat)))
        self.assertEqual(load_gnss_track(path, "Degrees/bad_latitude", path, "Degrees/longitude")["lat"], [])


    def test_g1_device_relative_map_times_use_the_file_origin(self):
        path = self.folder / "relative.h5"
        with h5py.File(path, "w") as handle:
            handle.attrs["log_start_unix_seconds"] = 1_700_000_000.0
            handle.attrs["time_start_seconds"] = 0.0
            for name, values in (("GNSS/latitude_deg", [60.0, 60.1]), ("GNSS/longitude_deg", [24.0, 24.1])):
                add_series(handle, name, [120_000.0, 121_000.0], values, time_dataset="time_ms",
                           time_unit="ms", time_reference="device_relative", unit="deg")
        track = load_gnss_track(path, "GNSS/latitude_deg", path, "GNSS/longitude_deg")
        # The first fix is 120 s after logging starts, not at the log start.
        np.testing.assert_allclose(track["time"], [1_700_000_120.0, 1_700_000_121.0])
        filtered = load_gnss_track(path, "GNSS/latitude_deg", path, "GNSS/longitude_deg",
                                   time_range=(1_700_000_120.5, 1_700_000_130.0))
        np.testing.assert_allclose(filtered["lat"], [60.1])

    def test_g2_one_coordinate_scale_per_series(self):
        path = self.folder / "scale.h5"
        times = [1.0, 2.0, 3.0]
        with h5py.File(path, "w") as handle:
            # London in E7: the longitude is below 1.8° and must still be rescaled.
            add_series(handle, "E7/latitude", times, [515_074_000, 515_075_000, 515_076_000], unit="raw")
            add_series(handle, "E7/longitude", times, [-1_278_000, -1_279_000, -1_280_000], unit="raw")
            # ×10 storage crossing 9° latitude must not jump across the globe.
            add_series(handle, "X10/latitude", times, [85.0, 90.0, 95.0], unit="deg*10")
            add_series(handle, "X10/longitude", times, [249.0, 249.5, 250.0], unit="deg*10")
        london = load_gnss_track(path, "E7/latitude", path, "E7/longitude")
        np.testing.assert_allclose(london["lat"], [51.5074, 51.5075, 51.5076])
        np.testing.assert_allclose(london["lon"], [-0.1278, -0.1279, -0.128])
        crossing = load_gnss_track(path, "X10/latitude", path, "X10/longitude")
        np.testing.assert_allclose(crossing["lat"], [8.5, 9.0, 9.5])
        # The scale comes from the whole series, not the selected samples.
        selected = load_gnss_track(path, "X10/latitude", path, "X10/longitude", time_range=(0.5, 1.5))
        np.testing.assert_allclose(selected["lat"], [8.5])

    def test_g5_g6_map_pages_carry_no_track_data_and_pushed_json_is_inert(self):
        name = "</script><script>window.pwned=1</script>"
        encoded = script_json([{"name": name}])
        self.assertNotIn("<", encoded)
        self.assertEqual(json.loads(encoded)[0]["name"], name)
        self.assertIn("let tracks=[];", map_html())
        self.assertIn("let tracks=[];", _three_d_map_html())
        page = mock.Mock()
        card = SimpleNamespace(map_view=mock.Mock(), _track_data=[{"name": name, "lat": [1.0]}], draw_style="auto")
        card.map_view.page.return_value = page
        GnssPlotWidget._push_tracks(card, True)
        script = page.runJavaScript.call_args.args[0]
        self.assertTrue(script.startswith("setTracks("))
        self.assertNotIn("</script>", script)

    def test_g5_2d_map_receives_tracks_only_after_ready(self):
        page = mock.Mock()
        card = SimpleNamespace(
            _map_ready=False, _map_failed=False, _map_generation=1, _marker_times={0: None, 1: None}, _sync_time=None, _value_box_times={0: None, 1: None}, value_boxes=mock.Mock(),
            _marker_revision=0, _marker_in_flight=False, _track_data=[], _loading=True,
            map_view=mock.Mock(), map_stack=mock.Mock(), map_loading=mock.Mock(), map_type=mock.Mock(),
            legend=mock.Mock(), satellite_radio=mock.Mock(), scale_bar_check=mock.Mock(),
            grouped_series=SimpleNamespace(groups=[SimpleNamespace(color="#123456")]),
            update_sample_coverage=mock.Mock(), map_container=mock.Mock(),
            palette=QApplication.palette, draw_style="auto",
        )
        card.map_view.page.return_value = page
        card.satellite_radio.isChecked.return_value = False
        for name in ("_loaded_tracks", "_push_tracks", "_on_map_ready", "_flush_marker", "_load_map",
                     "_update_selection", "_refresh_value_box_positions", "_request_value_box_positions", "_markers_ready",
                     "marker_values", "_set_scale_bar"):
            setattr(card, name, getattr(GnssPlotWidget, name).__get__(card))
        card._loaded_tracks([{"lat": [1.0], "lon": [2.0], "time": [3.0]}])
        page.runJavaScript.assert_not_called()
        card.map_view.setHtml.assert_not_called()
        card._on_map_ready(1, True)
        scripts = [call.args[0] for call in page.runJavaScript.call_args_list]
        # The scale bar is shown once the map is ready; the page puts it at the bottom right.
        self.assertIn("setScaleBar(true);", scripts)
        from src.gui.plots.gnss import map_html
        self.assertIn("L.control.scale({position:'bottomright',imperial:false})", map_html())
        # The map credit sits at the top right, without Leaflet's name.
        self.assertIn("map.attributionControl.setPrefix(false);", map_html())
        self.assertIn("map.attributionControl.setPosition('topright');", map_html())
        self.assertIn("attribution:'&copy; OpenStreetMap'", map_html())
        # The map background is matched to the canvases before the tracks are pushed.
        self.assertIn("backgroundColor", scripts[0])
        tracks = [script for script in scripts if script.startswith("setTracks(")]
        self.assertEqual(len(tracks), 1)
        self.assertTrue(tracks[0].endswith(", true);"))
        self.assertIn("#123456", tracks[0])
        # A failed page is reloaded empty when new tracks arrive.
        card._map_ready, card._map_failed = False, True
        card._loaded_tracks([None])
        card.map_view.setHtml.assert_called_once_with(map_html(background=canvas_background(card).name()))


class Gnss3DAltitudeTests(_TempCase):
    def setUp(self):
        super().setUp()
        self.path = self.folder / "track.h5"
        with h5py.File(self.path, "w") as handle:
            for name, values in (("GNSS/latitude_deg", [60, 61, 62]), ("GNSS/longitude_deg", [24, 25, 26]),
                                 ("GNSS/altitude_m", [10, 0, -5]), ("Other/depth", [-1, -2, -3]),
                                 ("Other/nan_height", [np.nan] * 3)):
                add_series(handle, name, [1.0, 2.0, 3.0], values, unit="m")
        patcher = mock.patch("src.gui.plots.gnss_3d.QWebEngineView", ScriptMapView)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lat_lon = (AxisSource(self.path, "GNSS/latitude_deg"), AxisSource(self.path, "GNSS/longitude_deg"))

    def heights(self, track):
        return [point[2] for point in track["coordinates"]]

    def test_2_missing_unreadable_and_nan_altitudes_draw_flat(self):
        track = load_3d_gnss_group((*self.lat_lon, None))
        self.assertEqual(self.heights(track), [0.0, 0.0, 0.0])
        self.assertTrue(track["flat_altitude"])
        self.assertIsNone(track["altitude_warning"])
        for name in ("Other/nan_height", "Other/missing"):
            with self.subTest(name=name), self.assertLogs("lodgazilla.gnss_3d", "WARNING"):
                track = load_3d_gnss_group((*self.lat_lon, AxisSource(self.path, name)))
                self.assertEqual(self.heights(track), [0.0, 0.0, 0.0])
                self.assertTrue(track["flat_altitude"])
                self.assertTrue(track["altitude_warning"])
        track = load_3d_gnss_group((*self.lat_lon, AxisSource(self.path, "GNSS/altitude_m")))
        self.assertEqual(self.heights(track), [10.0, 0.0, -5.0])
        self.assertFalse(track["flat_altitude"])

    def test_2_optional_axis_slot_is_loaded_while_required_slots_wait(self):
        calls = []
        load = _Load(1, ((1, 2, None), (1, None, None)), None,
                     lambda axes, **_kwargs: calls.append(axes) or "loaded", optional_axes=frozenset({2}))
        results = []
        load.signals.completed.connect(lambda _revision, value: results.extend(value))
        load.run()
        self.assertEqual(calls, [(1, 2, None)])
        self.assertEqual(results, ["loaded", None])

    def test_2_8_card_draws_flat_and_follows_alt_reassignment(self):
        widget = Gnss3DPlotWidget(self.path, "GNSS/latitude_deg", initial_axes=(*self.lat_lon, None))
        self.addCleanup(widget.close)
        self.addCleanup(widget.dispose)
        self.wait_loaded(widget)
        # Without Auto, which would put a track without altitude on the ground.
        widget._auto_pending = False
        widget.map_view.runJavaScript("map.events.load()")
        widget.map_view.loadFinished.emit(True)
        self.assertIsNone(widget.height_series)
        self.assertEqual(widget.legend.entries[2].text, "0 m (flat)")
        self.assertEqual(widget.map_view.runJavaScript("routeCoordinates().map(p=>p[2])"), [0, 0, 0])
        widget.replace_legend_series(2, (self.path, "Other/depth"))
        self.assertEqual(widget.height_series, (self.path, "Other/depth"))
        self.assertEqual(widget._height_values([1.0, 3.0])[0], [-1.0, -3.0])
        self.wait_loaded(widget)
        self.assertEqual(widget.map_view.runJavaScript("routeCoordinates().map(p=>p[2])"), [-1, -2, -3])
        with self.assertLogs("lodgazilla.gnss_3d", "WARNING"):
            widget.replace_legend_series(2, (self.path, "Other/nan_height"))
            self.wait_loaded(widget)
        self.assertIn("unusable: 0 m (flat)", widget.legend.entries[2].text)
        self.assertEqual(widget.map_view.runJavaScript("routeCoordinates().map(p=>p[2])"), [0, 0, 0])
        widget.replace_legend_series(2, None)
        self.assertIsNone(widget.height_series)

    def test_g5_3d_page_is_loaded_without_tracks(self):
        pages = []
        original = ScriptMapView.setHtml

        def capture(view, html):
            pages.append(html)
            original(view, html)

        with mock.patch.object(ScriptMapView, "setHtml", capture):
            widget = Gnss3DPlotWidget(self.path, "GNSS/latitude_deg")
            self.addCleanup(widget.close)
            self.addCleanup(widget.dispose)
            self.wait_loaded(widget)
        self.assertEqual(len(pages), 1)
        self.assertNotIn("GNSS", pages[0].split("<script>", 3)[-1])
        widget.map_view.loadFinished.emit(True)
        self.assertEqual(widget.map_view.runJavaScript("tracks.length"), 1)



class BoundedLoadTests(_TempCase):
    """X/Y, Map and 3D Map read long series in bounded pieces, never a whole dataset."""

    COUNT = 300_000
    LARGEST_READ = 70_000

    def setUp(self):
        super().setUp()
        self.path = self.folder / "long.h5"
        times = 1_700_000_000.0 + np.arange(self.COUNT) * 0.01
        with h5py.File(self.path, "w") as handle:
            add_series(handle, "GNSS/latitude", times, 60.0 + np.arange(self.COUNT) * 1e-7)
            add_series(handle, "GNSS/longitude", times, 24.0 + np.arange(self.COUNT) * 1e-7)
            add_series(handle, "GNSS/altitude", times, np.arange(self.COUNT) * 0.001, unit="m")
        self.times = times
        self.reads = []
        original = h5py.Dataset.__getitem__

        def recording(dataset, key):
            result = original(dataset, key)
            self.reads.append(np.size(result))
            return result

        def whole(dataset, *args, **kwargs):
            # np.asarray(dataset) reads all of it.
            self.reads.append(dataset.size)
            return original(dataset, ())

        for name, replacement in (("__getitem__", recording), ("__array__", whole)):
            patcher = mock.patch.object(h5py.Dataset, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_xy_reads_every_nth_sample_and_aligns_y(self):
        window = (self.times[100_000], self.times[200_000])
        time, x_values, y_values, *_ = load_xy_data(self.path, "GNSS/latitude", self.path, "GNSS/longitude",
                                                    time_range=window, maximum_points=1_000)
        self.assertLessEqual(max(self.reads), self.LARGEST_READ)
        self.assertLessEqual(len(time), 1_000)
        self.assertGreater(len(time), 900)
        self.assertTrue(window[0] <= time[0] and time[-1] <= window[1])
        np.testing.assert_allclose(y_values - 24.0, x_values - 60.0, atol=1e-9)

    def test_map_and_3d_map_read_bounded_pieces(self):
        track = load_gnss_track(self.path, "GNSS/latitude", self.path, "GNSS/longitude", maximum_points=1_000)
        self.assertEqual(track["available_points"], self.COUNT)
        self.assertLessEqual(len(track["lat"]), 1_000)
        np.testing.assert_allclose(np.asarray(track["lon"]) - 24.0, np.asarray(track["lat"]) - 60.0, atol=1e-9)
        self.reads.clear()
        axes = (AxisSource(self.path, "GNSS/latitude"), AxisSource(self.path, "GNSS/longitude"),
                AxisSource(self.path, "GNSS/altitude"))
        result = load_3d_gnss_group(axes, maximum_points=1_000)
        self.assertLessEqual(max(self.reads), self.LARGEST_READ)
        heights = [point[2] for point in result["coordinates"]]
        expected = (np.asarray(result["time"]) - self.times[0]) / 0.01 * 0.001
        np.testing.assert_allclose(heights, expected, atol=1e-6)

class XYTests(_TempCase):
    def test_r5_coverage_has_no_half_sample_extrapolation(self):
        path = self.make_file([-.4, 0., 1., 2., 2.4], [0., 1., 2.])
        times, *_ = load_xy_data(path, "x", path, "y")
        np.testing.assert_array_equal(times, [0., 1., 2.])
        with self.assertRaisesRegex(ValueError, "no samples"):
            load_xy_data(path, "x", path, "y", time_range=(2.1, 2.4))
        path = self.make_file([1.1, 1.4], [0., 1.])
        with self.assertRaisesRegex(ValueError, "no overlapping"):
            load_xy_data(path, "x", path, "y")

    def make_file(self, x_times, y_times, *, y_relative=False):
        path = self.folder / "xy.h5"
        with h5py.File(path, "w") as handle:
            add_series(handle, "x", x_times, np.arange(len(x_times), dtype=float))
            if y_relative:
                add_series(handle, "y", y_times, np.arange(len(y_times), dtype=float) * 10,
                           time_dataset="time_ms", time_unit="ms", time_reference="device_relative")
            else:
                add_series(handle, "y", y_times, np.arange(len(y_times), dtype=float) * 10)
        return path

    def test_g3_x_samples_outside_y_coverage_are_dropped(self):
        path = self.make_file(np.arange(0.0, 601.0, 100.0), np.arange(300.0, 601.0, 100.0))
        time, x_values, y_values, *_ = load_xy_data(path, "x", path, "y")
        np.testing.assert_allclose(time, [300, 400, 500, 600])
        np.testing.assert_allclose(x_values, [3, 4, 5, 6])
        np.testing.assert_allclose(y_values, [0, 10, 20, 30])
        other = self.make_file([0.0, 100.0], [300.0, 400.0])
        with self.assertRaisesRegex(ValueError, "no overlapping"):
            load_xy_data(other, "x", other, "y")

    def test_g3_mixed_time_domains_are_rejected(self):
        path = self.make_file([1_700_000_000.0, 1_700_000_001.0], [0.0, 1000.0], y_relative=True)
        with self.assertRaisesRegex(ValueError, "log start"):
            load_xy_data(path, "x", path, "y")

    def test_7_contains_time_uses_sorted_bounds(self):
        result = (np.asarray([1.0, 2.0, 3.0]), np.zeros(3), np.zeros(3), "x", "y")
        self.assertTrue(_result_contains_time(result, 2.5))
        self.assertFalse(_result_contains_time(result, 3.5))
        self.assertFalse(_result_contains_time(None, 2.0))
        self.assertFalse(_result_contains_time((np.empty(0),), 2.0))

    def test_g4_click_ignores_nan_samples(self):
        path = self.make_file([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
        card = XYPlotWidget((path, "x"), (path, "y"))
        self.addCleanup(card.close)
        self.addCleanup(card.dispose)
        self.wait_loaded(card)
        card._time = np.asarray([1.0, 2.0, 3.0])
        card._x = np.asarray([np.nan, 1.0, 2.0])
        card._y = np.asarray([0.0, 10.0, 20.0])
        placed = []
        card.marker_placed.connect(placed.append)
        event = mock.Mock()
        event.button.return_value = Qt.MouseButton.LeftButton
        event.double.return_value = False
        with mock.patch.object(card.plot_item.vb, "mapSceneToView", return_value=QPointF(2.0, 20.0)):
            card._on_scene_click(event)
            self.assertEqual(placed, [3.0])
            card._x = np.full(3, np.nan)
            card._on_scene_click(event)
            self.assertEqual(placed, [3.0])


class FrequencyTests(_TempCase):
    def write(self, times, *, time_dataset="time_unix", time_unit=None):
        path = self.folder / "freq.h5"
        values = np.sin(2 * np.pi * 10.0 * np.arange(len(times)) / 100.0)
        with h5py.File(path, "w") as handle:
            add_series(handle, "signal", times, values, time_dataset=time_dataset, time_unit=time_unit)
        return path

    def test_g7_windows_after_an_early_gap_remain_valid(self):
        dt = 0.01
        times = np.arange(4096) * dt
        # A gap of 100.7 samples leaves later samples 0.3·dt early on the grid.
        times[2048:] += 100.7 * dt
        path = self.write(times)
        data = load_frequency_data(path, "signal", FFTSettings(window_size=256, overlap=0.0))
        valid = np.any(np.isfinite(data.amplitude), axis=0)
        midpoint = len(valid) // 2
        self.assertTrue(valid[:midpoint].any())
        # Old behaviour rejected every window after the gap.
        self.assertGreaterEqual(int(valid[midpoint:].sum()), 6)

    def test_g8_quantised_timestamps_keep_the_true_rate(self):
        times = np.round(np.arange(8192) * 1000.0 / 104.0)
        path = self.write(times, time_dataset="time_ms", time_unit="ms")
        data = load_frequency_data(path, "signal", FFTSettings(window_size=512, overlap=0.5))
        self.assertAlmostEqual(data.sample_rate, 104.0, delta=0.2)
        self.assertTrue(np.isfinite(data.amplitude).any())
        self.assertEqual(data.skipped_windows, 0)


class ImageTests(_TempCase):
    def test_g9_decoding_is_scaled_and_off_the_gui_thread(self):
        path = self.folder / "large.png"
        image = QImage(400, 200, QImage.Format.Format_ARGB32)
        image.fill(QColor("red"))
        self.assertTrue(image.save(str(path)))
        decoded, size, error = decode_image(path, 100)
        self.assertEqual(error, "")
        self.assertEqual(size, QSize(400, 200))
        self.assertEqual(decoded.size(), QSize(100, 50))
        with mock.patch("src.gui.plots.image.QThreadPool.globalInstance") as pool:
            canvas = ImageCanvas(path, None)
            self.addCleanup(canvas.close)
            # Construction only queues the decode.
            pool.return_value.start.assert_called_once()
            self.assertTrue(canvas.loading)
            self.assertTrue(canvas.image.isNull())
            pool.return_value.start.call_args.args[0].run()
        self.assertFalse(canvas.loading)
        self.assertEqual(canvas.source_size, QSize(400, 200))
        canvas.resize(300, 300)
        rect = canvas.image_rect()
        self.assertAlmostEqual(rect.width() / rect.height(), 2.0, places=2)
        first = canvas._scaled_pixmap(rect.size())
        self.assertIs(canvas._scaled_pixmap(rect.size()), first)
        self.assertIsNot(canvas._scaled_pixmap(QSize(100, 50)), first)

    def test_g9_late_decode_after_dispose_is_ignored(self):
        path = self.folder / "missing.png"
        with mock.patch("src.gui.plots.image.QThreadPool.globalInstance") as pool:
            canvas = ImageCanvas(path, None)
            self.addCleanup(canvas.close)
            canvas.dispose()
            pool.return_value.start.call_args.args[0].run()
        self.assertTrue(canvas.loading)
        self.assertEqual(canvas.error, "")


if __name__ == "__main__":
    unittest.main()
