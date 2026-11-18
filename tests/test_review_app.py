from tests.plugin_fixtures import with_sample_importer, write_manifest
"""Regression tests for the launcher/application review findings."""

import json
import logging
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import h5py
import numpy as np


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtGui import QCloseEvent  # noqa: E402
from tests.qt_wait import qt_wait  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.app import lodgazilla  # noqa: E402
from src.app.lodgazilla import (  # noqa: E402
    SETTINGS_FLUSH_RETRY_MS,
    ImportRequest,
    LODGaZillaWindow,
    PlotWorkspace,
)
from src.app.settings import (  # noqa: E402
    HDF5_SECTION,
    begin_app_settings_cache,
    end_app_settings_cache,
    load_app_settings_data,
    load_settings_section,
    save_settings_section,
    set_app_settings_changed_callback,
)
from src.gui.plots.video import VideoPlotWidget  # noqa: E402
from src.jobs.import_batch import (  # noqa: E402
    discover_import_jobs,
    remove_stale_cache_temp_files,
    run_import_job,
)
from src.jobs.import_data import log_start_from_source_filename  # noqa: E402
from src.jobs.parallel_jobs import run_process_jobs  # noqa: E402
from src.plugins.manager import PluginManager  # noqa: E402

def setUpModule():
    # Windows built here would start the real plugin update check against GitHub;
    # these tests stay offline.
    # A plain function, not a Mock: PySide cannot connect signals to a Mock.
    patcher = mock.patch("src.plugins.updates.PluginUpdates.check", lambda self, sources: None)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


def _sleeping_worker(seconds):  # type: ignore[no-untyped-def]
    """Top-level (spawn-safe) worker that ignores cooperative cancellation."""
    time.sleep(seconds)
    return seconds


def _ignore_initializer(_progress_queue, _cancel_event=None):  # type: ignore[no-untyped-def]
    pass


def _write_series(path: Path, names) -> None:  # type: ignore[no-untyped-def]
    with h5py.File(path, "w") as h5_file:
        for offset, name in enumerate(names):
            group = h5_file.create_group(f"series/{name}")
            group.attrs["time_dataset"] = "time_unix"
            group.attrs["navigator_name"] = name
            group.create_dataset("time_unix", data=np.asarray([1.0, 2.0, 3.0]))
            group.create_dataset("value", data=np.asarray([1.0, 2.0, 3.0]) + offset)


def _write_sample_capture(folder):
    path = folder / "capture.csv"
    path.write_text("time,value\n-0.001,2.5\n0,3.5\n")
    return path


class CorruptSettingsTests(unittest.TestCase):
    def test_session_backs_up_a_corrupt_file_and_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app_settings.json"
            path.write_text('{"plot": {', encoding="utf-8")
            with self.assertLogs("lodgazilla.settings", level="WARNING") as logs:
                data = begin_app_settings_cache(path)
            try:
                backups = list(Path(directory).glob("app_settings.json.corrupt-*"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_text(encoding="utf-8"), '{"plot": {')
                self.assertIn("corrupt", "\n".join(logs.output))
                self.assertIn(HDF5_SECTION, data)
            finally:
                end_app_settings_cache(path, flush=False)

    def test_plain_load_does_not_move_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app_settings.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertLogs("lodgazilla.settings", level="WARNING"):
                load_app_settings_data(path)
            self.assertTrue(path.is_file())
            self.assertEqual(list(Path(directory).glob("*.corrupt-*")), [])


class SettingsFlushTests(unittest.TestCase):
    def test_session_change_notifies_the_registered_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app_settings.json"
            begin_app_settings_cache(path)
            changed = mock.Mock()
            try:
                set_app_settings_changed_callback(changed, path)
                save_settings_section(path, "plot", {"selected_series_line_width": 7})
                changed.assert_called_once_with()
                self.assertFalse(path.exists())
            finally:
                self.assertTrue(end_app_settings_cache(path))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["plot"]["selected_series_line_width"], 7)
            # Ending the session also drops the callback.
            save_settings_section(path, "plot", {"selected_series_line_width": 8})
            changed.assert_called_once_with()

    def test_failed_flush_is_retried_later(self):
        window = SimpleNamespace(settings_path=Path("unused.json"), _settings_flush_timer=mock.Mock())
        with mock.patch("src.app.lodgazilla.flush_app_settings_cache", return_value=False):
            self.assertFalse(LODGaZillaWindow.flush_settings(window))
        window._settings_flush_timer.start.assert_called_once_with(SETTINGS_FLUSH_RETRY_MS)


class MainWindowCloseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, directory: str) -> LODGaZillaWindow:
        return LODGaZillaWindow(Path(directory), settings_path=Path(directory) / "app_settings.json")

    def test_close_flushes_first_stops_plugins_and_disposes_every_card(self):
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(directory)
            order = mock.Mock()
            card = mock.Mock()
            card.dispose = order.dispose
            failing = mock.Mock()
            failing.dispose.side_effect = RuntimeError("boom")

            def children(kind):  # type: ignore[no-untyped-def]
                return [] if kind is VideoPlotWidget else [failing, card]

            window.plugin_manager.shutdown = order.plugin_shutdown
            with mock.patch.object(window.plot_workspace, "findChildren", side_effect=children), \
                 mock.patch("src.app.lodgazilla.flush_app_settings_cache", side_effect=order.flush), \
                 mock.patch("src.app.lodgazilla.shutdown_web_engine", side_effect=order.web_engine):
                window.closeEvent(QCloseEvent())
            names = [call[0] for call in order.mock_calls]
            self.assertEqual(names[:2], ["flush", "plugin_shutdown"])
            self.assertLess(names.index("dispose"), names.index("web_engine"))

    def test_settings_are_saved_when_a_teardown_step_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app_settings.json"
            window = self._window(directory)
            save_settings_section(path, "plot", {"selected_series_line_width": 9})
            with mock.patch("src.app.lodgazilla.shutdown_web_engine", side_effect=RuntimeError("web")):
                with self.assertRaises(RuntimeError):
                    window.closeEvent(QCloseEvent())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["plot"]["selected_series_line_width"], 9)

    def test_close_cancels_imports_before_terminating(self):
        process = mock.Mock()
        process.is_alive.side_effect = [True, False, False, False]
        cancel = threading.Event()
        window = SimpleNamespace(
            active_imports={1: SimpleNamespace(process=process, cancel_event=cancel, operation="H5 cache")},
            internal_logger=mock.Mock(),
        )
        LODGaZillaWindow.stop_active_imports(window, grace_seconds=0.1)
        self.assertTrue(cancel.is_set())
        process.join.assert_called_once()
        process.terminate.assert_not_called()


class _ColorCard:
    drop_card_type = "line"
    accepts_axis_drop = False

    def __init__(self, series):  # type: ignore[no-untyped-def]
        self.series = list(series)
        self.inserted = []

    def insert_series(self, path, name, index):  # type: ignore[no-untyped-def]
        self.inserted.append((path, name, index))
        self.series.insert(index, (path, name))

    def on_series_changed(self):  # type: ignore[no-untyped-def]
        raise AssertionError("insertion must go through insert_series")


class InsertIntoCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _plan(entries):  # type: ignore[no-untyped-def]
        items = [SimpleNamespace(source=SimpleNamespace(kind="series", entry=entry)) for entry in entries]
        return [SimpleNamespace(kind="line", items=items)]

    def test_each_new_source_is_inserted_through_the_card_in_order(self):
        a, b, c, d = ((Path("x.h5"), name) for name in "abcd")
        card = _ColorCard([a, b])
        workspace = SimpleNamespace(_series_drop_finished=mock.Mock(), _open_card_plan=mock.Mock())
        with mock.patch("src.app.lodgazilla.plan_cards", return_value=self._plan([c, a, d])):
            PlotWorkspace.add_plot_inputs_to_card(workspace, [], card, 0)
        self.assertEqual(card.series, [c, d, a, b])
        self.assertEqual(card.inserted, [(*c, 0), (*d, 1)])

    def test_line_card_keeps_existing_colors_when_inserting_above(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.h5"
            _write_series(path, ("a", "b", "c"))
            workspace = PlotWorkspace(default_plot_heights={"line": 200})
            try:
                card = workspace.add_line_plot(path, "a")
                workspace.add_series_to_plot(card, path, "b")
                card.series_plot.set_series_color(0, "#ff0000")
                before = dict(zip(card.series, card.series_plot.series_colors()))
                with mock.patch("src.app.lodgazilla.plan_cards", return_value=self._plan([(path, "c")])):
                    workspace.add_plot_inputs_to_card([], card, 0)
                after = dict(zip(card.series, card.series_plot.series_colors()))
                self.assertEqual(card.series[0], (path, "c"))
                self.assertEqual(after[(path, "a")], "#ff0000")
                self.assertEqual(after[(path, "b")], before[(path, "b")])
                self.assertNotIn(after[(path, "c")], {"#ff0000", before[(path, "b")]})
            finally:
                workspace.close_all_plots()
                workspace.close()


class FolderMultiSeriesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_folder_files_are_read_off_the_gui_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            _write_series(Path(directory) / "data.h5", ("a",))
            workspace = PlotWorkspace(default_plot_heights={"multi": 200})
            window = SimpleNamespace(plot_workspace=workspace, internal_logger=mock.Mock())
            threads = []
            original = lodgazilla.read_h5_navigator_data

            def recording_read(path):  # type: ignore[no-untyped-def]
                threads.append(threading.current_thread())
                return original(path)

            try:
                with mock.patch("src.app.lodgazilla.read_h5_navigator_data", side_effect=recording_read):
                    LODGaZillaWindow.plot_folder_in_multi_widget(window, Path(directory))
                    self.assertEqual(workspace.splitter.count(), 0)
                    for _ in range(300):
                        if workspace.splitter.count():
                            break
                        qt_wait(10)
                self.assertEqual(workspace.splitter.count(), 1)
                self.assertTrue(threads)
                self.assertNotIn(threading.main_thread(), threads)
            finally:
                workspace.close_all_plots()
                workspace.close()


class SeriesTimeRangeCacheTests(unittest.TestCase):
    def test_range_is_read_once_until_the_file_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.h5"
            path.write_bytes(b"x")
            lodgazilla._SERIES_TIME_RANGE_CACHE.clear()
            lodgazilla._H5_STAT_CACHE.clear()
            with mock.patch.object(PlotWorkspace, "_read_series_time_range", return_value=(1.0, 2.0)) as read:
                self.assertEqual(PlotWorkspace._series_time_range(path, "a"), (1.0, 2.0))
                self.assertEqual(PlotWorkspace._series_time_range(path, "a"), (1.0, 2.0))
                self.assertEqual(read.call_count, 1)
                path.write_bytes(b"changed")
                # Within a second (a bar drag), the file is not checked again.
                PlotWorkspace._series_time_range(path, "a")
                self.assertEqual(read.call_count, 1)
                with mock.patch.object(lodgazilla, "H5_STAT_MAX_AGE_SECONDS", -1.0):
                    PlotWorkspace._series_time_range(path, "a")
                self.assertEqual(read.call_count, 2)
            self.assertIsNone(PlotWorkspace._series_time_range(Path(directory) / "missing.h5", "a"))


class Hdf5SettingsPropagationTests(unittest.TestCase):
    def test_import_process_receives_current_session_hdf5_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            settings_path = folder / "app_settings.json"
            settings_path.write_text(json.dumps({HDF5_SECTION: {"compression": "gzip"}}), encoding="utf-8")
            begin_app_settings_cache(settings_path)
            try:
                save_settings_section(settings_path, HDF5_SECTION, {
                    **load_settings_section(HDF5_SECTION, settings_path), "compression": "lzf",
                })
                window = SimpleNamespace(
                    import_coordinator=mock.Mock(reserve_id=mock.Mock(return_value=1)),
                    plugin_manager=SimpleNamespace(enabled={}),
                    clear_imported_children=mock.Mock(), event_timer=mock.Mock(),
                    max_background_processes=1, available_import_slots=lambda: 0,
                    active_imports={}, internal_logger=mock.Mock(), settings_path=settings_path,
                )
                with mock.patch("src.app.lodgazilla.multiprocessing.get_context") as get_context, \
                     mock.patch("src.app.lodgazilla.start_process_tree"):
                    LODGaZillaWindow.start_import_process(window, ImportRequest(folder, False, 1))
                args = get_context.return_value.Process.call_args.kwargs["args"]
                self.assertEqual(args[5]["compression"], "lzf")
                self.assertIs(args[6], get_context.return_value.Event.return_value)
            finally:
                end_app_settings_cache(settings_path, flush=False)

    @with_sample_importer
    def test_worker_writes_the_cache_with_the_supplied_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            _write_sample_capture(folder)
            settings = {"compression": "lzf", "compression_level": 4, "chunk_rows": 1024,
                        "lod_target_buckets": 10, "lod_bucket_growth": 4}
            jobs = discover_import_jobs(
                folder, False, logging.getLogger("test"),
                enabled_plugins={"sample": True}, hdf5_settings=settings,
            )
            job = jobs[folder][0]
            self.assertEqual(job.hdf5_settings["compression"], "lzf")
            with mock.patch("src.jobs.import_data.load_hdf5_settings", side_effect=AssertionError("disk read")):
                result = run_import_job(job, mock.Mock())
            self.assertTrue(result.imported, result.error)
            with h5py.File(folder / "capture.h5", "r") as cache:
                self.assertEqual(cache["series/Test/signal/value"].compression, "lzf")

    @with_sample_importer
    def test_worker_preserves_an_unrelated_h5_with_the_cache_name(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            _write_sample_capture(folder)
            with h5py.File(folder / "capture.h5", "w") as original:
                original.attrs["tool"] = "another tool"
            original_bytes = (folder / "capture.h5").read_bytes()
            job = discover_import_jobs(
                folder, False, logging.getLogger("test"), enabled_plugins={"sample": True},
            )[folder][0]
            result = run_import_job(job, mock.Mock())
            self.assertFalse(result.imported)
            self.assertIn("Not replacing", result.error)
            self.assertEqual((folder / "capture.h5").read_bytes(), original_bytes)


class BatchCancellationTests(unittest.TestCase):
    @with_sample_importer
    def test_import_job_stops_when_cancelled(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            _write_sample_capture(folder)
            job = discover_import_jobs(
                folder, False, logging.getLogger("test"), enabled_plugins={"sample": True},
            )[folder][0]
            cancel = threading.Event()
            cancel.set()
            result = run_import_job(job, mock.Mock(), cancel)
            self.assertFalse(result.imported)
            self.assertIn("Cancelled", result.error)
            self.assertFalse((folder / "capture.h5").exists())

    def test_cancel_terminates_pool_workers_that_do_not_cooperate(self):
        import multiprocessing

        cancel = multiprocessing.get_context("spawn").Event()
        timer = threading.Timer(1.0, cancel.set)
        started = time.monotonic()
        timer.start()
        try:
            results = run_process_jobs(
                [60.0] * 4, worker_count=2, worker=_sleeping_worker, initializer=_ignore_initializer,
                emit_progress=lambda _event: None, make_failure_result=lambda job, error: None,
                cancel_event=cancel, cancel_grace_seconds=0.5,
            )
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - started, 45.0)
        self.assertEqual(results, [])


    def test_stale_cache_temp_files_are_swept(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "sub").mkdir()
            stale = [folder / ".data.h5.abc123.tmp", folder / "sub" / ".more.hdf5.x1.tmp"]
            keep = [folder / "notes.tmp", folder / "data.h5"]
            for path in stale + keep:
                path.write_bytes(b"x")
            removed = remove_stale_cache_temp_files(folder, True, logging.getLogger("test"))
            self.assertEqual(removed, 2)
            self.assertFalse(any(path.exists() for path in stale))
            self.assertTrue(all(path.exists() for path in keep))


class PluginLifecycleTests(unittest.TestCase):
    def _manager(self, directory: str, window):  # type: ignore[no-untyped-def]
        plugin_path = Path(directory) / "sample" / "plugin.py"
        plugin_path.parent.mkdir()
        plugin_path.write_text(
            "PLUGIN_ID = 'sample'\n"
            "PLUGIN_TITLE = 'Sample'\n"
            "class Plugin:\n"
            "    def __init__(self, window, log):\n"
            "        self.window = window.parent_widget\n"
            "        self.window.events.append('created')\n"
            "    def start(self): self.window.events.append('start')\n"
            "    def stop(self): self.window.events.append('stop')\n"
            "    def add_menu_actions(self, menu): pass\n"
            "PLUGIN_CLASS = Plugin\n",
            encoding="utf-8",
        )
        write_manifest(plugin_path.parent)
        return PluginManager(window, plugin_root=directory)

    def test_disabled_plugins_are_not_instantiated_and_shutdown_stops_enabled(self):
        window = SimpleNamespace(events=[])
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory, window)
            manager.set_enabled({"sample": False})
            self.assertEqual(window.events, [])
            manager.set_enabled({"sample": True})
            self.assertEqual(window.events, ["created", "start"])
            manager.shutdown()
            self.assertEqual(window.events, ["created", "start", "stop"])
            manager.shutdown()
            self.assertEqual(window.events.count("stop"), 1)




class LogStartTimezoneTests(unittest.TestCase):
    def test_offset_is_resolved_for_the_logs_own_date(self):
        for stem in ("log_20260115_120000", "log_20260715_120000"):
            with self.subTest(stem=stem):
                start = log_start_from_source_filename(Path(f"{stem}.bin"))
                wall_clock = datetime.strptime(stem[4:], "%Y%m%d_%H%M%S")
                self.assertEqual(start.timestamp(), time.mktime(wall_clock.timetuple()))
                self.assertEqual(start.replace(tzinfo=None), wall_clock)


if __name__ == "__main__":
    unittest.main()
