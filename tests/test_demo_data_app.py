"""Creating the demo data from the app: the background job, deletion and the Settings dialog button."""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import h5py  # noqa: E402
from PySide6.QtWidgets import QApplication, QGroupBox, QMessageBox, QPushButton  # noqa: E402

from src.app import demo_data  # noqa: E402
from src.app.demo_data import (  # noqa: E402
    DemoDataJob, demo_data_exists, parse_progress, remove_demo_data, uses_folder,
)
from src.gui.settings_widget import SettingsEditorWidget  # noqa: E402
from tests.qt_wait import qt_wait_until  # noqa: E402
from tools.create_demo_dataset import Progress, stage_weights  # noqa: E402

# Stands in for tools/create_demo_dataset.py: reports progress, then writes demo.umw or fails.
FAKE_GENERATOR = """
import argparse, pathlib, sys, time
parser = argparse.ArgumentParser()
parser.add_argument("--output-dir", type=pathlib.Path)
parser.add_argument("--progress", action="store_true")
parser.add_argument("--fail", action="store_true")
parser.add_argument("--slow", action="store_true")
args = parser.parse_args()
args.output_dir.mkdir(parents=True, exist_ok=True)
print("Writing demo data", flush=True)
print("PROGRESS 0.5000", flush=True)
if args.slow:
    time.sleep(60)
if args.fail:
    print("Something broke", flush=True)
    sys.exit(3)
(args.output_dir / "demo.umw").write_text("{}", encoding="utf-8")
print("PROGRESS 1.0000", flush=True)
"""


class DemoDataHelperTests(unittest.TestCase):
    def test_parse_progress(self):
        self.assertEqual(parse_progress("PROGRESS 0.2500"), 0.25)
        self.assertEqual(parse_progress("PROGRESS 1.7"), 1.0)
        self.assertIsNone(parse_progress("PROGRESS soon"))
        self.assertIsNone(parse_progress("  chase_cam_4k.mp4: 150/1920 frames"))

    def test_uses_folder_finds_absolute_paths_inside_the_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "demodata"
            inside = {"type": "multi", "series": [{"path": str(root / "a" / "watch.h5"), "name": "X"}], "color": "#fff"}
            outside = {"type": "line", "series": [{"path": str(Path(temp) / "other" / "w.h5")}]}
            self.assertTrue(uses_folder(inside, root))
            self.assertFalse(uses_folder(outside, root))
            self.assertFalse(uses_folder({"name": "demodata"}, root))

    def test_remove_deletes_read_only_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "demodata"
            (root / "session").mkdir(parents=True)
            locked = root / "session" / "info.json"
            locked.write_text("{}", encoding="utf-8")
            locked.chmod(stat.S_IREAD)
            remove_demo_data(root)
            self.assertFalse(root.exists())
            remove_demo_data(root)  # Nothing to delete is fine.

    def test_progress_runs_through_the_stages_to_one(self):
        args = Namespace(no_video=False, long_hours=2.0)
        weights = stage_weights(args)
        self.assertEqual(len(weights), 7)
        output = io.StringIO()
        progress = Progress(weights, enabled=True)
        with contextlib.redirect_stdout(output):
            progress.step(0.0)
            progress.next()
            progress.next()
            progress.step(0.5)
            for _ in range(5):
                progress.next()
        values = [float(line.split()[1]) for line in output.getvalue().splitlines()]
        self.assertEqual(values, sorted(values))
        self.assertAlmostEqual(values[-1], 1.0)
        self.assertEqual(len(stage_weights(Namespace(no_video=True, long_hours=0))), 4)
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            Progress(weights, enabled=False).next()
        self.assertEqual(quiet.getvalue(), "")


class DemoDataJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def run_job(self, root: Path, *arguments: str, replace: bool = False, cancel: bool = False):
        script = root.parent / "fake_generator.py"
        script.write_text(FAKE_GENERATOR, encoding="utf-8")
        results, fractions, messages = [], [], []
        with mock.patch.object(demo_data, "GENERATOR", script):
            job = DemoDataJob(root, replace=replace, arguments=arguments)
            self.addCleanup(job.deleteLater)
            job.finished.connect(lambda ok, error: results.append((ok, error)))
            job.progress.connect(fractions.append)
            job.message.connect(messages.append)
            job.start()
            if cancel:
                self.assertTrue(qt_wait_until(lambda: fractions, 20_000))
                job.cancel()
            self.assertTrue(qt_wait_until(lambda: results, 30_000))
        job.wait(5)
        return results[0], fractions, messages

    def test_job_reports_progress_and_completes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "demodata"
            (ok, error), fractions, messages = self.run_job(root)
            self.assertEqual((ok, error), (True, ""))
            self.assertEqual(fractions, [0.5, 1.0])
            self.assertIn("Writing demo data", messages)
            self.assertTrue(demo_data_exists(root))

    def test_replace_deletes_the_old_folder_first(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "demodata"
            root.mkdir()
            (root / "user_change.txt").write_text("edited", encoding="utf-8")
            (ok, _error), _fractions, _messages = self.run_job(root, replace=True)
            self.assertTrue(ok)
            self.assertFalse((root / "user_change.txt").exists())

    def test_failure_reports_the_generator_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "demodata"
            (ok, error), _fractions, _messages = self.run_job(root, "--fail")
            self.assertFalse(ok)
            self.assertIn("Something broke", error)
            self.assertFalse(demo_data_exists(root))

    def test_cancel_stops_the_generator(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "demodata"
            (ok, error), _fractions, _messages = self.run_job(root, "--slow", cancel=True)
            self.assertEqual((ok, error), (False, ""))
            self.assertFalse(demo_data_exists(root))


    def test_a_detached_job_reports_nothing_more(self):
        with tempfile.TemporaryDirectory() as temp:
            job = DemoDataJob(Path(temp) / "demodata")
            self.addCleanup(job.deleteLater)
            messages = []
            job.message.connect(messages.append)
            job._emit("message", "before")
            job.detach()
            job._emit("message", "after")
            self.app.processEvents()
            self.assertEqual(messages, ["before"])

class DemoDataWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self, folder: Path):
        from src.app.lodgazilla import LODGaZillaWindow
        from src.plugins.manager import PluginManager

        settings = folder / "settings.json"
        settings.write_text(json.dumps({"navigator_controls": {"recent_folders": []}}))
        with mock.patch("src.app.lodgazilla.PluginManager",
                        side_effect=lambda window, **kwargs: PluginManager(window, plugin_root=folder / "empty", **kwargs)):
            window = LODGaZillaWindow(folder, settings_path=settings)
        self.addCleanup(self.app.processEvents)
        self.addCleanup(window.close)
        return window

    def test_regenerate_closes_demo_cards_replaces_the_folder_and_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            root = folder / "demodata"
            (root / "session").mkdir(parents=True)
            cache = root / "session" / "watch.h5"
            with h5py.File(cache, "w") as handle:
                series = handle.create_group("series/signal")
                series.create_dataset("time_unix", data=[1., 2., 3.])
                series.create_dataset("value", data=[4., 5., 6.])
                series.attrs["time_dataset"] = "time_unix"
            script = folder / "fake_generator.py"
            script.write_text(FAKE_GENERATOR, encoding="utf-8")
            window = self.make_window(folder)
            card = window.plot_workspace.add_line_plot(cache, "signal")
            self.app.processEvents()
            with mock.patch("src.app.lodgazilla.DEMO_DATA_ROOT", root), \
                    mock.patch.object(demo_data, "GENERATOR", script), \
                    mock.patch("src.app.lodgazilla.QMessageBox.warning", return_value=QMessageBox.StandardButton.Yes), \
                    mock.patch("src.app.lodgazilla.QMessageBox.question",
                               return_value=QMessageBox.StandardButton.No) as question:
                window.regenerate_demo_data()
                self.assertNotIn(card, window.plot_workspace.plots())
                self.assertIsNotNone(window.demo_data_progress)
                self.assertTrue(qt_wait_until(lambda: window.demo_data_job is None, 30_000))
            question.assert_called_once()
            self.assertIsNone(window.demo_data_progress)
            self.assertTrue(demo_data_exists(root))
            self.assertFalse(cache.exists())

    def test_dont_ask_again_stops_the_startup_offer(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            window = self.make_window(folder)

            asked = []

            def decline(box):
                asked.append(box.text())
                box.checkBox().setChecked(True)
                return QMessageBox.StandardButton.No

            with mock.patch("src.app.lodgazilla.DEMO_DATA_ROOT", folder / "demodata"), \
                    mock.patch.object(QMessageBox, "exec", new=decline):
                window.offer_demo_data()
                window.offer_demo_data()
            self.assertEqual(len(asked), 1)
            self.assertIsNone(window.demo_data_job)
            self.assertFalse(window.lodgazilla_settings["offer_demo_data"])


class DemoDataSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_general_tab_has_the_regenerate_button(self):
        with tempfile.TemporaryDirectory() as temp:
            editor = SettingsEditorWidget(Path(temp) / "app_settings.json")
            self.addCleanup(editor.deleteLater)
            general = editor.tabs.widget(0)
            titles = [group.title() for group in general.findChildren(QGroupBox)]
            self.assertIn("Demo data", titles)
            button = next(button for button in general.findChildren(QPushButton)
                          if button.text() == "Re-generate demo data")
            requests = []
            editor.demo_data_regenerate_requested.connect(lambda: requests.append(True))
            button.click()
            self.assertEqual(requests, [True])


if __name__ == "__main__":
    sys.exit(unittest.main())
