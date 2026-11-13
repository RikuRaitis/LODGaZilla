"""Saving and opening workspaces (.umw): file format, card restoring and the File bar."""
import json
import os
from dataclasses import replace
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import h5py
import numpy as np
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QColor, QImage, QVector3D
from PySide6.QtWidgets import QApplication, QMessageBox

from src.app.lodgazilla import LODGaZillaWindow, PlotWorkspace
from src.app.settings import load_lodgazilla_settings
from src.data.workspace_file import (
    WorkspaceFileError, read_workspace_file, updated_recent_workspaces, write_workspace_file,
)
from src.gui.settings_widget import SettingsDialog
from src.gui.plots.gnss import GnssPlotWidget
from src.gui.plots.gnss_3d import Gnss3DPlotWidget
from src.gui.plots.xyz import XYZPlotWidget
from src.plugins.manager import PluginManager
from tests.test_frequency_plot import write_signal
from tests.test_xyz_plot import FakeView

HEIGHTS = {"line": 200, "multi": 200, "xy": 250, "xyz": 300, "event": 100, "image": 200}

def setUpModule():
    # Windows built here would start the real plugin update check against GitHub;
    # these tests stay offline.
    # A plain function, not a Mock: PySide cannot connect signals to a Mock.
    patcher = mock.patch("src.plugins.updates.PluginUpdates.check", lambda self, sources: None)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


def write_cache(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as cache:
        cache.attrs["log_start_unix_seconds"] = 1_700_000_000.0
        for index, name in enumerate("xyz"):
            group = cache.create_group("series/" + name)
            group.attrs.update(time_dataset="time", time_unit="s", navigator_name=name, unit="V")
            group.create_dataset("time", data=np.arange(100, dtype=float))
            group.create_dataset("value", data=np.sin(np.arange(100) / (index + 3)))


def wait(app, milliseconds):
    # Background loaders need the GIL; QTest.qWait would hold it.
    end = time.monotonic() + milliseconds / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def cards(state):
    return [card for row in state["rows"] for card in row["cards"]]


class WorkspaceFileFormatTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_paths_are_stored_relative_and_read_back_absolute(self):
        data = self.folder / "data" / "a.h5"
        (self.folder / "views").mkdir()
        saved = write_workspace_file(self.folder / "views" / "w", {
            "navigator": {"folder": str(self.folder / "data")},
            "workspace": {"rows": [{"cards": [{"series": [{"path": str(data), "name": "x"}]}]}]},
        })
        self.assertEqual(saved.name, "w.umw")
        raw = json.loads(saved.read_text(encoding="utf-8"))
        self.assertEqual((raw["format"], raw["version"]), ("UMW", 1))
        self.assertEqual(raw["navigator"]["folder"], "../data")
        self.assertEqual(raw["workspace"]["rows"][0]["cards"][0]["series"][0]["path"], "../data/a.h5")
        state = read_workspace_file(saved)
        self.assertEqual(Path(state["workspace"]["rows"][0]["cards"][0]["series"][0]["path"]), data.resolve())
        self.assertEqual(state["workspace"]["rows"][0]["cards"][0]["series"][0]["name"], "x")
        self.assertFalse(list((self.folder / "views").glob("*.partial")))

    def test_other_files_are_refused(self):
        other = self.folder / "other.umw"
        other.write_text(json.dumps({"format": "something else"}))
        with self.assertRaises(WorkspaceFileError):
            read_workspace_file(other)
        other.write_text(json.dumps({"format": "UMW", "version": 99}))
        with self.assertRaisesRegex(WorkspaceFileError, "newer version"):
            read_workspace_file(other)
        with self.assertRaises(WorkspaceFileError):
            read_workspace_file(self.folder / "missing.umw")

    def test_recent_list_keeps_ten_newest_without_duplicates(self):
        recent = [str(self.folder / f"{index}.umw") for index in range(10)]
        updated = updated_recent_workspaces(recent, self.folder / "5.umw")
        self.assertEqual(updated[0], str((self.folder / "5.umw").resolve()))
        self.assertEqual(len(updated), 10)
        self.assertEqual(len(set(updated)), 10)
        self.assertEqual(len(updated_recent_workspaces(updated, self.folder / "new.umw")), 10)


class WorkspaceRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(ignore_cleanup_errors=True)))
        self.cache = self.folder / "data" / "rec.h5"
        write_cache(self.cache)
        self.images = []
        for name in ("a.png", "b.png"):
            image = QImage(20, 10, QImage.Format.Format_RGB32)
            image.fill(QColor("red"))
            image.save(str(self.folder / "data" / name))
            self.images.append(self.folder / "data" / name)
        self.addCleanup(lambda: (QThreadPool.globalInstance().waitForDone(3000), wait(self.app, 200)))

    def workspace(self):
        workspace = PlotWorkspace(default_plot_heights=HEIGHTS)
        workspace.resize(900, 1200)
        workspace.show()
        self.addCleanup(workspace.close_all_plots)
        return workspace

    def test_cards_rows_views_colors_and_markers_survive_a_round_trip(self):
        source = self.workspace()
        source.set_markers_linked(True)
        line = source.add_line_plot(self.cache, "x")
        line.add_series(self.cache, "y")
        line.series_plot.set_series_color(1, "#123456")
        multi = source.add_multi_plot([(self.cache, "x"), (self.cache, "z")])
        multi.series_plot.transforms[(self.cache, "z")] = (0.2, 0.6)
        multi.series_plot.apply_transforms()
        xy = source.add_xy_plot((self.cache, "x"), (self.cache, "y"))
        xy.grouped_series.set_color(0, "#abcdef")
        first, second = (source.add_image_plot(self.cache.parent, image) for image in self.images)
        self.assertTrue(source.move_plot(second, first, "right"))
        wait(self.app, 400)
        xy.plot_item.vb.setRange(xRange=(-0.5, 0.5), yRange=(-0.2, 0.3), padding=0)
        line.plot_view_box().setRange(xRange=(10, 40), yRange=(-2, 2), padding=0)
        line.set_time_axis_representation("log_elapsed")
        line.set_vertical_marker(20.0, 0)
        source.on_marker_placed(line, 20.0)
        line.set_vertical_marker(30.0, 1)
        source.on_second_marker_placed(line, 30.0)
        source.activate_plot(multi)
        wait(self.app, 200)
        saved = write_workspace_file(self.folder / "w.umw", {"workspace": source.workspace_state()})
        expected = read_workspace_file(saved)["workspace"]

        restored = self.workspace()
        restored.set_markers_linked(True)
        self.assertEqual(restored.restore_workspace_state(expected), [])
        wait(self.app, 800)
        actual = restored.workspace_state()
        self.assertEqual([len(row["cards"]) for row in actual["rows"]], [1, 1, 1, 2])
        self.assertEqual([row["height"] for row in actual["rows"]], [row["height"] for row in expected["rows"]])
        self.assertEqual(actual["active"], 1)
        for index, (saved_card, card) in enumerate(zip(cards(expected), cards(actual))):
            if index == 1:
                # A linked time card follows the first card's time range, which is compared above.
                saved_card["view"][0] = card["view"][0]
            self.assertEqual(card, saved_card, saved_card["type"])
        # Linked markers show the saved selection everywhere.
        self.assertEqual(restored.plots()[1].marker_values(), (20.0, 30.0))
        self.assertEqual(cards(expected)[0]["time_axis_representation"], "log_elapsed")
        self.assertEqual(restored.plots()[0].time_axis_representation(), "log_elapsed")

    def test_missing_files_are_reported_and_other_cards_open(self):
        source = self.workspace()
        source.add_line_plot(self.cache, "x")
        source.add_line_plot(self.cache, "y").add_series(self.cache, "z")
        state = source.workspace_state()
        moved = self.cache.with_name("gone.h5")
        cards(state)[1]["series"][1]["path"] = str(moved)
        cards(state)[0]["series"][0]["path"] = str(moved)
        restored = self.workspace()
        problems = restored.restore_workspace_state(state)
        self.assertEqual(len(problems), 2)
        self.assertIn("gone.h5", problems[0])
        self.assertEqual([card.series for card in restored.plots()], [[(self.cache, "y")]])

    def test_malformed_cards_are_reported_and_other_cards_open(self):
        source = self.workspace()
        source.add_line_plot(self.cache, "x")
        state = source.workspace_state()
        good = cards(state)[0]
        state["rows"] = [{"cards": [{"type": "line", "series": [{"name": "no path"}]}]},
                         {"cards": [{"type": "video"}, "not a card"]}, "not a row", {"cards": [good]}]
        restored = self.workspace()
        problems = restored.restore_workspace_state(state)
        self.assertEqual(len(problems), 4)
        self.assertEqual([card.series for card in restored.plots()], [[(self.cache, "x")]])

    def test_plot_navigator_trims_survive_a_round_trip(self):
        source = self.workspace()
        line = source.add_line_plot(self.cache, "x")
        xy = source.add_xy_plot((self.cache, "x"), (self.cache, "y"))
        source.set_plot_navigator_range(line, 20.0, 30.0)
        source.set_plot_navigator_range(xy, 5.0, 15.0)
        saved = write_workspace_file(self.folder / "trim.umw", {"workspace": source.workspace_state()})
        state = read_workspace_file(saved)["workspace"]
        self.assertEqual([card.get("trim") for card in cards(state)], [[20.0, 30.0], [5.0, 15.0]])
        restored = self.workspace()
        self.assertEqual(restored.restore_workspace_state(state), [])
        restored_line, restored_xy = restored.plots()
        # Each card shows only its trimmed range again, and its Plot navigator bar is trimmed.
        self.assertEqual(restored._plot_navigator_ranges[restored_line], (20.0, 30.0))
        self.assertEqual(restored_line.series_plot.data_time_range, (20.0, 30.0))
        self.assertEqual(restored_xy.time_range, (5.0, 15.0))

    def test_opening_replaces_existing_cards(self):
        source = self.workspace()
        source.add_line_plot(self.cache, "x")
        state = source.workspace_state()
        target = self.workspace()
        target.add_xy_plot((self.cache, "x"), (self.cache, "y"))
        target.add_line_plot(self.cache, "z")
        target.restore_workspace_state(state)
        self.assertEqual([card.series for card in target.plots()], [[(self.cache, "x")]])


class CameraView(FakeView):
    """A fake 3D view with the camera options of pyqtgraph's GLViewWidget."""

    def __init__(self, parent):
        super().__init__(parent)
        self.opts = {"center": QVector3D(), "distance": 6.0, "elevation": 25.0, "azimuth": 45.0, "fov": 60.0}

    def setCameraPosition(self, pos=None, distance=None, elevation=None, azimuth=None):
        super().setCameraPosition(pos=pos, distance=distance, elevation=elevation, azimuth=azimuth)
        for key, value in (("center", pos), ("distance", distance), ("elevation", elevation), ("azimuth", azimuth)):
            if value is not None:
                self.opts[key] = value


class CardStateTests(unittest.TestCase):
    """Card types whose settings are not covered by the round trip above."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory(ignore_cleanup_errors=True)))
        self.cache = folder / "rec.h5"
        write_cache(self.cache)
        self.signal = folder / "signal.h5"
        times = np.arange(8192) / 4096
        write_signal(self.signal, times, np.sin(2 * np.pi * 512 * times))
        self.addCleanup(lambda: (QThreadPool.globalInstance().waitForDone(3000), wait(self.app, 200)))

    def round_trip(self, create, change):  # type: ignore[no-untyped-def]
        workspace = PlotWorkspace(default_plot_heights=HEIGHTS)
        self.addCleanup(workspace.close_all_plots)
        card = create(workspace)
        change(card)
        wait(self.app, 300)
        state = workspace.workspace_state()
        restored = PlotWorkspace(default_plot_heights=HEIGHTS)
        self.addCleanup(restored.close_all_plots)
        self.assertEqual(restored.restore_workspace_state(state), [])
        wait(self.app, 300)
        return cards(state)[0], cards(restored.workspace_state())[0], restored.plots()[0]

    def test_3d_plot_keeps_traces_camera_and_sample_limit(self):
        def change(card):  # type: ignore[no-untyped-def]
            card.add_trace(((self.cache, "y"), (self.cache, "z"), (self.cache, "x")))
            card.set_axis_component(2, "time")
            card.view.setCameraPosition(pos=QVector3D(1, 2, 3), distance=9, elevation=10, azimuth=-30)
            card.sample_limit_combo.setCurrentIndex(card.sample_limit_combo.count() - 1)

        with mock.patch.object(XYZPlotWidget, "view_class", CameraView):
            saved, restored, card = self.round_trip(
                lambda workspace: workspace.add_xyz_plot((self.cache, "x"), (self.cache, "y"), (self.cache, "z")),
                change)
        self.assertEqual(restored, saved)
        self.assertEqual(len(card.traces), 2)
        self.assertEqual(card.grouped_series.axis(2).component, "time")
        self.assertEqual(saved["camera"]["distance"], 9)

    def test_frequency_keeps_fft_settings_peak_line_and_view(self):
        def change(card):  # type: ignore[no-untyped-def]
            card.set_fft_settings(replace(card.settings, window_size=1024, window="Hamming"))
            card.set_peak_line_visible(False)
            card.set_peak_line_threshold(0.5)

        saved, restored, card = self.round_trip(
            lambda workspace: workspace.add_frequency_plot(self.signal, "Scope/Channel A"), change)
        self.assertEqual(card.settings.window_size, 1024)
        self.assertEqual(card.settings.window, "Hamming")
        self.assertFalse(card.show_peak_line)
        self.assertEqual(restored["peak_line"], saved["peak_line"])
        self.assertEqual(restored["view"], saved["view"])

    def test_sample_interval_card_keeps_its_series(self):
        saved, restored, card = self.round_trip(
            lambda workspace: workspace.add_sample_interval_plot([(self.cache, "x"), (self.cache, "y")]),
            lambda card: None)
        self.assertEqual(restored["series"], saved["series"])
        self.assertEqual(card.drop_card_type, "interval")


class MapViewTests(unittest.TestCase):
    """A saved map camera replaces the first fit to the tracks, once tracks exist."""

    def test_saved_view_is_sent_with_the_first_tracks(self):
        for card_type, view in ((GnssPlotWidget, {"center": [60.1, 24.9], "zoom": 13.0}),
                                (Gnss3DPlotWidget, {"center": [24.9, 60.1], "zoom": 13.0, "pitch": 40.0,
                                                    "bearing": 10.0})):
            with self.subTest(card=card_type.__name__):
                card = SimpleNamespace(_pending_map_view=view, _track_data=[], map_view=mock.Mock(), draw_style="auto")
                card_type._push_tracks(card, True)
                script = card.map_view.page().runJavaScript.call_args.args[0]
                self.assertNotIn("restoreView", script)
                card._track_data = [{"lat": [60.1], "lon": [24.9], "time": [1.0]}]
                card_type._push_tracks(card, True)
                script = card.map_view.page().runJavaScript.call_args.args[0]
                # The view is set just before the tracks (the Map sends its draw style first).
                self.assertIn(f"restoreView={json.dumps(view)};setTracks(", script)
                self.assertIsNone(card._pending_map_view)

    def test_reported_camera_is_saved(self):
        card = SimpleNamespace(_map_view_state=None)
        GnssPlotWidget._view_reported(card, '{"center": [1, 2], "zoom": 3}')
        GnssPlotWidget._view_reported(card, "not json")
        self.assertEqual(card._map_view_state, {"center": [1, 2], "zoom": 3})


class WorkspaceMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_save_open_and_recent_list_restore_navigator_and_cards(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory(ignore_cleanup_errors=True)))
        settings = folder / "settings.json"
        settings.write_text(json.dumps({"navigator_controls": {"recent_folders": []}}))
        cache = folder / "data" / "rec.h5"
        write_cache(cache)
        with mock.patch("src.app.lodgazilla.PluginManager",
                        side_effect=lambda window, **kwargs: PluginManager(window, plugin_root=folder / "empty", **kwargs)):
            window = LODGaZillaWindow(folder / "data", settings_path=settings)
        self.addCleanup(lambda: (window.close(), self.app.processEvents()))
        window.show()
        window.plot_workspace.add_line_plot(cache, "x")
        window.navigator.group_by_combo.setCurrentText("Year")
        window.set_markers_linked(False)
        window.set_legends_shown(False)
        window.set_card_controls_shown(False)
        workspace_file = folder / "saved.umw"
        file_bar = window.navigator.controls
        with mock.patch("src.app.lodgazilla.QFileDialog.getSaveFileName", return_value=(str(workspace_file), "")):
            file_bar.save_workspace_button.click()
        self.assertTrue(workspace_file.is_file())
        self.assertEqual(window.workspace_path, workspace_file.resolve())

        window.plot_workspace.close_all_plots()
        window.plot_workspace.add_line_plot(cache, "z")
        window.navigator.group_by_combo.setCurrentText("Year/Month")
        window.set_markers_linked(True)
        window.set_legends_shown(True)
        window.set_card_controls_shown(True)
        # The File bar's Open recent button shows the recent list.
        self.assertIs(file_bar.recent_workspaces_button.menu(), window.recent_workspaces_menu)
        window.populate_recent_workspaces_menu()
        recent = [action for action in window.recent_workspaces_menu.actions() if action.isEnabled()]
        self.assertEqual([action.text() for action in recent], ["saved.umw"])
        # A card is open, so opening asks first; Cancel keeps the cards.
        with mock.patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Cancel) as asked:
            recent[0].trigger()
        self.assertEqual(asked.call_count, 1)
        self.assertEqual([card.series for card in window.plot_workspace.plots()], [[(cache, "z")]])
        with mock.patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Discard):
            recent[0].trigger()
        self.assertEqual([card.series for card in window.plot_workspace.plots()], [[(cache, "x")]])
        self.assertEqual(window.navigator.group_by_combo.currentText(), "Year")
        # The Plot navigator buttons are preferences, not part of the workspace.
        self.assertNotIn("plot_navigator", json.loads(workspace_file.read_text(encoding="utf-8")))
        self.assertTrue(window.plot_workspace.link_markers)
        self.assertTrue(window.plot_workspace.legends_shown)
        self.assertTrue(window.plot_navigator.card_controls_button.isChecked())
        self.assertEqual(load_lodgazilla_settings(settings)["recent_workspaces"],
                         [str(workspace_file.resolve())])
        # Saving the Settings dialog keeps the list.
        dialog = SettingsDialog(settings, window)
        dialog.save_settings()
        self.assertEqual(load_lodgazilla_settings(settings)["recent_workspaces"],
                         [str(workspace_file.resolve())])

        # Closing the window yourself asks too; Cancel keeps it open, Save saves first.
        event = mock.Mock(spontaneous=mock.Mock(return_value=True))
        with mock.patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Cancel):
            window.closeEvent(event)
        event.ignore.assert_called_once()
        window.plot_workspace.add_line_plot(cache, "z")
        with mock.patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Save):
            self.assertTrue(window.confirm_replacing_cards())
        self.assertEqual(len(cards(json.loads(workspace_file.read_text(encoding="utf-8"))["workspace"])), 2)
        window.plot_workspace.close_all_plots()
        with mock.patch.object(QMessageBox, "exec") as asked:
            self.assertTrue(window.confirm_replacing_cards())
        asked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
