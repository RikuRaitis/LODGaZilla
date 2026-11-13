"""Regression tests for the Navigator review findings N1-N10."""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import h5py

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from src.data.folder_info import read_existing_info_file, read_info_file  # noqa: E402
from src.data.navigator_data import (  # noqa: E402
    folder_needs_h5_generation,
    FolderInfoIndex,
    add_default_info_keys,
    filter_folders_by_metadata,
    folder_info_index_from_data,
)
from src.gui.navigator.settings import (  # noqa: E402
    COLUMN_CATEGORY_FOLDER,
    COLUMN_CATEGORY_INFO,
    COLUMN_DATA,
    COLUMN_INFO_PAIR_VALUE,
    COLUMN_SAMPLE_RATE,
)
from src.gui.navigator.widget import (  # noqa: E402
    ITEM_PATH_ROLE,
    ITEM_SERIES_ROLE,
    ITEM_TYPE_ROLE,
    LOADING_MESSAGE,
    PHYSICAL_DATA_COLUMN,
    PHYSICAL_VALUE_COLUMN,
    ROW_TYPE_DETAILED_INFO,
    ROW_TYPE_FOLDER,
    ROW_TYPE_SERIES,
    DataNavigatorWidget,
    info_column_id,
)
from src.jobs.navigator_snapshot import build_navigator_snapshot  # noqa: E402
from src.gui.navigator.widget import ITEM_INFO_KEY_ROLE  # noqa: E402


def make_sample_navigator_folder(root: Path) -> Path:
    data_folder = root / "Data"
    folder = data_folder / "First"
    folder.mkdir(parents=True)
    (folder / "First_info.json").write_text(
        '{"editable": {"Place": "Lake"}, "readonly": {"serial number": "123"}}',
        encoding="utf-8",
    )
    with h5py.File(folder / "sample.h5", "w") as h5_file:
        h5_file.attrs["time_start_seconds"] = 10.0
        h5_file.attrs["time_end_seconds"] = 72.0
        h5_file.attrs["series_index_json"] = json.dumps([{
            "name": "IMU/acc_x", "display_name": "acc_x", "navigator_name": "acc_x",
            "navigator_group": "IMU", "unit": "m/s2", "sample_count": 4, "sample_rate_hz": 100.0,
            "visible": True,
            "navigator_metadata": [
                {"id": "sample_rate", "title": "Sample rate", "format": "rate", "value": 100.0},
            ],
        }])
    return data_folder


def first_item_of_type(navigator, row_type):
    for item in navigator.tree_items():
        if item.data(0, ITEM_TYPE_ROLE) == row_type:
            return item
    raise AssertionError(f"No item of type {row_type}")


def detailed_info_item_for_key(navigator, key):
    for item in navigator.tree_items():
        if (
            item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_DETAILED_INFO
            and str(item.data(0, ITEM_INFO_KEY_ROLE) or "").casefold() == key.casefold()
        ):
            return item
    raise AssertionError(f"No detailed info item for {key}")


def folder_items(navigator):
    return [item for item in navigator.tree_items() if item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_FOLDER]


def folder_item(navigator, folder: Path):
    return next(
        item for item in folder_items(navigator)
        if Path(str(item.data(0, ITEM_PATH_ROLE))) == folder.resolve()
    )


def add_metadata_row(navigator, folder: Path):
    for item in navigator.tree_items():
        if (
            item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_DETAILED_INFO
            and item.data(0, ITEM_PATH_ROLE) == str(folder.resolve())
            and navigator.item_identity(item)[-1] == "(add metadata row)"
        ):
            return item
    raise AssertionError("No Add metadata row")


class NavigatorTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data_folder = make_sample_navigator_folder(self.root)
        self.first = self.data_folder / "First"

    def make_navigator(self, folder=None):
        navigator = DataNavigatorWidget(folder or self.data_folder, settings_path=None)
        self.addCleanup(self.close_navigator, navigator)
        return navigator

    def close_navigator(self, navigator):
        self.wait_for_jobs()
        navigator.close()
        navigator.deleteLater()
        self.app.processEvents()

    def wait_for_jobs(self):
        QThreadPool.globalInstance().waitForDone(5000)
        self.app.processEvents()
        self.app.processEvents()


class DeletedFolderRefreshTests(NavigatorTestCase):
    """N1: a refresh never recreates a folder deleted on disk."""

    def test_refresh_after_deleting_folder_does_not_recreate_it(self):
        second = self.data_folder / "Second"
        second.mkdir()
        navigator = self.make_navigator()
        self.assertTrue((second / "Second_info.json").exists())

        shutil.rmtree(second)
        navigator.refresh_tree()

        self.assertFalse(second.exists())
        self.assertEqual([Path(str(item.data(0, ITEM_PATH_ROLE))) for item in folder_items(navigator)],
                         [self.first.resolve()])

    def test_existing_info_reader_never_creates_files(self):
        missing = self.root / "Missing"
        self.assertIsNone(read_existing_info_file(missing))
        self.assertFalse(missing.exists())


class AddMetadataRowEditorTests(NavigatorTestCase):
    """N2: the Add metadata row starts empty and unchanged commits write nothing."""

    def commit(self, navigator, item, physical_column, logical_column_id, text=None):
        navigator.tree.setCurrentItem(item, physical_column)
        navigator._selected_logical_column_id = logical_column_id
        delegate = navigator.tree.itemDelegate()
        index = navigator.tree.indexFromItem(item, physical_column)
        editor = QLineEdit()
        self.addCleanup(editor.deleteLater)
        delegate.setEditorData(editor, index)
        initial = editor.text()
        if text is not None:
            editor.setText(text)
        delegate.setModelData(editor, navigator.tree.model(), index)
        return initial

    def test_add_metadata_row_editor_is_empty_and_untouched_commit_is_ignored(self):
        navigator = self.make_navigator()
        info_path = self.first / "First_info.json"
        original = info_path.read_text(encoding="utf-8")

        row = add_metadata_row(navigator, self.first)
        self.assertEqual(self.commit(navigator, row, PHYSICAL_VALUE_COLUMN, COLUMN_INFO_PAIR_VALUE), "")
        row = add_metadata_row(navigator, self.first)
        self.assertEqual(self.commit(navigator, row, PHYSICAL_DATA_COLUMN, COLUMN_DATA), "")

        self.assertEqual(info_path.read_text(encoding="utf-8"), original)
        info = read_info_file(self.first)
        self.assertNotIn("[add new key]", info.editable)
        self.assertNotIn("[ADD NEW VALUE]", info.editable.values())

    def test_unchanged_value_commit_does_not_write(self):
        navigator = self.make_navigator()
        detail = detailed_info_item_for_key(navigator, "place")
        with mock.patch("src.gui.navigator.widget.write_info_file") as writer:
            initial = self.commit(navigator, detail, PHYSICAL_VALUE_COLUMN, COLUMN_INFO_PAIR_VALUE)
        self.assertEqual(initial, "Lake")
        writer.assert_not_called()

    def test_typed_value_in_add_metadata_row_is_saved(self):
        navigator = self.make_navigator()
        row = add_metadata_row(navigator, self.first)
        self.commit(navigator, row, PHYSICAL_DATA_COLUMN, COLUMN_DATA, "weather")
        self.assertEqual(read_info_file(self.first).editable["weather"], "")


class SettingsDialogThemeTests(NavigatorTestCase):
    """N3: Cancel after Apply keeps the applied theme."""

    def test_cancel_after_apply_restores_saved_theme(self):
        from src.gui.settings_widget import SettingsDialog

        settings_path = self.root / "app_settings.json"
        with mock.patch("src.gui.settings_widget.apply_theme") as apply_theme:
            dialog = SettingsDialog(settings_path)
            self.addCleanup(dialog.deleteLater)
            dialog.editor.theme_combo.setCurrentText("Dark")
            dialog.save_settings()
            apply_theme.reset_mock()
            dialog.reject()
        apply_theme.assert_called_once_with("dark")


class NavigatorDataLayerTests(unittest.TestCase):
    """Claude 9: the snapshot job and its readers do not depend on the GUI package."""

    def test_snapshot_job_imports_no_gui_modules(self):
        import subprocess

        code = ("import sys, src.jobs.navigator_snapshot, src.data.navigator_data; "
                "print(sorted(name for name in sys.modules if name.startswith('src.gui')))")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                                cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.stdout.strip(), "[]")


class BackgroundSnapshotTests(NavigatorTestCase):
    """N4: disk scans and H5 inspection run in a background job."""

    def test_background_refresh_reads_disk_off_the_gui_thread(self):
        navigator = self.make_navigator()
        navigator.background_refresh = True
        second = self.data_folder / "Second"
        second.mkdir()
        shutil.copy(self.first / "sample.h5", second / "sample.h5")
        threads = []
        from src.jobs import navigator_snapshot

        original = navigator_snapshot.read_h5_navigator_data

        def recording_reader(path):
            threads.append(threading.current_thread())
            return original(path)

        with mock.patch("src.jobs.navigator_snapshot.read_h5_navigator_data", side_effect=recording_reader):
            navigator.refresh_tree(rescan=True)
            self.assertTrue(navigator.snapshot_refresh_in_progress())
            self.wait_for_jobs()

        self.assertFalse(navigator.snapshot_refresh_in_progress())
        self.assertTrue(threads)
        self.assertTrue(all(thread is not threading.main_thread() for thread in threads))
        self.assertEqual(len(folder_items(navigator)), 2)

    def test_first_background_load_shows_loading_then_tree(self):
        navigator = DataNavigatorWidget(None, settings_path=None)
        self.addCleanup(self.close_navigator, navigator)
        navigator.background_refresh = True
        navigator.set_data_folder(self.data_folder)
        top = navigator.tree.topLevelItem(0)
        self.assertEqual(navigator.item_logical_value(top, COLUMN_DATA), LOADING_MESSAGE)
        self.wait_for_jobs()
        self.assertEqual([navigator.item_logical_value(item, COLUMN_DATA) for item in folder_items(navigator)],
                         ["First"])

    def test_deferred_initial_load_scans_in_the_background(self):
        threads = []
        from src.jobs import navigator_snapshot

        original = navigator_snapshot.read_h5_navigator_data

        def recording_reader(path):
            threads.append(threading.current_thread())
            return original(path)

        with mock.patch("src.jobs.navigator_snapshot.read_h5_navigator_data", side_effect=recording_reader):
            navigator = DataNavigatorWidget(self.data_folder, settings_path=None, defer_initial_load=True)
            self.addCleanup(self.close_navigator, navigator)
            top = navigator.tree.topLevelItem(0)
            self.assertEqual(navigator.item_logical_value(top, COLUMN_DATA), LOADING_MESSAGE)
            self.assertEqual(threads, [])
            self.app.processEvents()
            self.assertTrue(navigator.snapshot_refresh_in_progress())
            self.wait_for_jobs()

        self.assertTrue(threads)
        self.assertTrue(all(thread is not threading.main_thread() for thread in threads))
        self.assertEqual([navigator.item_logical_value(item, COLUMN_DATA) for item in folder_items(navigator)],
                         ["First"])

    def test_refresh_before_the_deferred_load_does_not_scan_and_loading_is_reported(self):
        threads = []
        from src.jobs import navigator_snapshot

        original = navigator_snapshot.read_h5_navigator_data

        def recording_reader(path):
            threads.append(threading.current_thread())
            return original(path)

        with mock.patch("src.jobs.navigator_snapshot.read_h5_navigator_data", side_effect=recording_reader):
            navigator = DataNavigatorWidget(self.data_folder, settings_path=None, defer_initial_load=True)
            self.addCleanup(self.close_navigator, navigator)
            loading = []
            navigator.loading_changed.connect(loading.append)
            # As the main window's settings do while it is built, before the event loop runs.
            navigator.refresh_tree()
            self.assertEqual(threads, [])
            self.assertEqual(navigator.item_logical_value(navigator.tree.topLevelItem(0), COLUMN_DATA),
                             LOADING_MESSAGE)
            self.app.processEvents()
            self.wait_for_jobs()

        self.assertTrue(threads)
        self.assertTrue(all(thread is not threading.main_thread() for thread in threads))
        self.assertEqual(loading[0], True)
        self.assertEqual(loading[-1], False)
        self.assertEqual([navigator.item_logical_value(item, COLUMN_DATA) for item in folder_items(navigator)],
                         ["First"])

    def test_folder_switch_before_deferred_initial_load_wins(self):
        other = Path(self.root) / "Other"
        (other / "Run").mkdir(parents=True)
        shutil.copy(self.first / "sample.h5", other / "Run" / "sample.h5")
        navigator = DataNavigatorWidget(self.data_folder, settings_path=None, defer_initial_load=True)
        self.addCleanup(self.close_navigator, navigator)
        navigator.set_data_folder(other)
        self.wait_for_jobs()
        self.wait_for_jobs()
        self.assertEqual([navigator.item_logical_value(item, COLUMN_DATA) for item in folder_items(navigator)],
                         ["Run"])

    def test_ui_only_background_refresh_does_not_rebuild_when_disk_is_unchanged(self):
        navigator = self.make_navigator()
        navigator.background_refresh = True
        navigator.refresh_tree()
        item = first_item_of_type(navigator, ROW_TYPE_FOLDER)
        self.wait_for_jobs()
        self.assertIs(first_item_of_type(navigator, ROW_TYPE_FOLDER), item)

    def test_metadata_edit_updates_index_without_rescanning(self):
        navigator = self.make_navigator()
        item = first_item_of_type(navigator, ROW_TYPE_FOLDER)
        place = navigator.logical_column_for_id(info_column_id("place"), COLUMN_CATEGORY_FOLDER)
        with mock.patch("pathlib.Path.rglob", side_effect=AssertionError("rglob")), mock.patch(
            "src.gui.navigator.widget.build_navigator_snapshot", side_effect=AssertionError("snapshot")
        ):
            navigator.on_logical_cell_edited(item, place, "Sea")
        self.assertEqual(navigator.current_info_index.values_by_folder[self.first.resolve()]["place"], "Sea")
        self.assertEqual(navigator._source_snapshot.folder_infos[self.first.resolve()].editable["place"], "Sea")
        # The next UI refresh reuses the stored edit instead of re-reading it.
        with mock.patch("src.jobs.navigator_snapshot.read_existing_info_file") as reader:
            navigator.refresh_tree()
        reader.assert_not_called()
        self.assertEqual(navigator.item_logical_value(first_item_of_type(navigator, ROW_TYPE_FOLDER),
                                                      info_column_id("place")), "Sea")

    def test_edit_during_background_job_survives_the_job_result(self):
        navigator = self.make_navigator()
        navigator.background_refresh = True
        gate = threading.Event()
        from src.jobs import navigator_snapshot

        original = navigator_snapshot.read_h5_navigator_data

        def slow_reader(path):
            gate.wait(5)
            return original(path)

        (self.first / "sample.h5").touch()
        with mock.patch("src.jobs.navigator_snapshot.read_h5_navigator_data", side_effect=slow_reader):
            navigator.refresh_tree()
            item = first_item_of_type(navigator, ROW_TYPE_FOLDER)
            place = navigator.logical_column_for_id(info_column_id("place"), COLUMN_CATEGORY_FOLDER)
            navigator.on_logical_cell_edited(item, place, "Sea")
            gate.set()
            self.wait_for_jobs()
        self.assertEqual(navigator.item_logical_value(first_item_of_type(navigator, ROW_TYPE_FOLDER),
                                                      info_column_id("place")), "Sea")


class SelectionPreservationTests(NavigatorTestCase):
    """N5: refresh keeps the selected row and logical column."""

    def test_refresh_keeps_current_series_and_logical_column(self):
        navigator = self.make_navigator()
        series = first_item_of_type(navigator, ROW_TYPE_SERIES)
        navigator.tree.setCurrentItem(series, PHYSICAL_VALUE_COLUMN)
        navigator._selected_logical_column_id = COLUMN_SAMPLE_RATE

        navigator.refresh_tree()

        current = navigator.tree.currentItem()
        self.assertIsNotNone(current)
        self.assertIsNot(current, series)
        self.assertEqual(current.data(0, ITEM_TYPE_ROLE), ROW_TYPE_SERIES)
        self.assertEqual(current.data(0, ITEM_SERIES_ROLE), "IMU/acc_x")
        self.assertIn(current, navigator.tree.selectedItems())
        self.assertEqual(navigator._selected_logical_column_id, COLUMN_SAMPLE_RATE)
        self.assertEqual(navigator.tree.selectedItems(), [current])


class ProgressExpansionTests(NavigatorTestCase):
    """N6: a folder showing import progress stays expanded across refreshes."""

    def test_expanded_folder_with_progress_stays_expanded(self):
        navigator = self.make_navigator()
        item = first_item_of_type(navigator, ROW_TYPE_FOLDER)
        item.setExpanded(True)
        navigator.set_item_progress(item, 1, 4, "First", "10 s")
        self.assertEqual(item.text(PHYSICAL_DATA_COLUMN), "")

        navigator.refresh_tree()

        item = first_item_of_type(navigator, ROW_TYPE_FOLDER)
        self.assertTrue(item.isExpanded())
        self.assertEqual(navigator.item_logical_value(item, COLUMN_DATA), "First")


class DetailedInfoReentryTests(NavigatorTestCase):
    """N7: a key edit is written once and announced once."""

    def test_key_rename_writes_once(self):
        navigator = self.make_navigator()
        detail = detailed_info_item_for_key(navigator, "place")
        data_column = navigator.logical_column_for_id(COLUMN_DATA, COLUMN_CATEGORY_INFO)
        changed = []
        navigator.folder_info_changed.connect(changed.append)
        from src.gui.navigator import widget

        with mock.patch("src.gui.navigator.widget.write_info_file", wraps=widget.write_info_file) as writer:
            navigator.on_logical_cell_edited(detail, data_column, "location")

        self.assertEqual(writer.call_count, 1)
        self.assertEqual(len(changed), 1)
        self.assertEqual(list(read_info_file(self.first).editable), ["location"])


class OutsideRootLinkTests(NavigatorTestCase):
    """N8: a linked folder resolving outside the data root is dropped."""

    def test_link_outside_root_is_dropped(self):
        outside = self.root / "Outside"
        outside.mkdir()
        link = self.data_folder / "Linked"
        try:
            import _winapi

            _winapi.CreateJunction(str(outside), str(link))
        except (ImportError, OSError):
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Cannot create a directory link here")

        result = build_navigator_snapshot(self.data_folder, None, rescan=True)
        self.assertEqual(result.snapshot.folders, [self.first.resolve()])
        self.assertFalse((outside / "Outside_info.json").exists())

        navigator = self.make_navigator()
        navigator.group_by_combo.setCurrentText("Year")
        navigator.refresh_tree()
        self.assertEqual(len(folder_items(navigator)), 1)


class FilterExactMatchTests(unittest.TestCase):
    """N9: a listed value matches exactly; free text matches substrings."""

    def test_listed_value_is_exact_and_free_text_is_substring(self):
        folders = [Path(name).resolve() for name in ("a", "b", "c", "d")]
        index = FolderInfoIndex(
            {"run"}, {"run"},
            {folders[0]: {"run": "1"}, folders[1]: {"run": "10"},
             folders[2]: {"run": "21"}, folders[3]: {"run": "Run 1b"}},
            {},
        )
        self.assertEqual(filter_folders_by_metadata(folders, index, "run", "1"), [folders[0]])
        self.assertEqual(filter_folders_by_metadata(folders, index, "run", "run 1B"), [folders[3]])
        self.assertEqual(filter_folders_by_metadata(folders, index, "run", "b"), [folders[3]])
        self.assertEqual(filter_folders_by_metadata(folders, index, "run", "2"), [folders[2]])


class DefaultReadonlyKeyTests(NavigatorTestCase):
    """N10: a default key that is read-only in a folder stays read-only."""

    def test_default_key_does_not_override_readonly_value(self):
        info = read_info_file(self.first)
        index = add_default_info_keys(
            folder_info_index_from_data({self.first.resolve(): info}), ["serial number", "weather"]
        )
        editable = index.editable_values_by_folder[self.first.resolve()]
        self.assertNotIn("serial number", editable)
        self.assertEqual(editable["weather"], "")
        self.assertEqual(index.values_by_folder[self.first.resolve()]["serial number"], "123")

    def test_navigator_does_not_open_editor_for_readonly_default_key(self):
        navigator = self.make_navigator()
        navigator.default_info_metadata_keys = {"serial number"}
        navigator.refresh_tree()
        item = first_item_of_type(navigator, ROW_TYPE_FOLDER)
        serial = navigator.logical_column_for_id(info_column_id("serial number"), COLUMN_CATEGORY_FOLDER)
        self.assertFalse(navigator.is_folder_info_column_editable(item, serial))


class FolderGenerationRuleTests(unittest.TestCase):
    def test_only_folders_with_their_own_data_files_need_h5_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            year = Path(directory) / "2024"
            run = year / "run"
            run.mkdir(parents=True)
            (year / "2024_info.json").write_text("{}", encoding="utf-8")
            # Writing folder info leaves its lock beside it.
            (year / "2024_info.json.lock").write_bytes(b"\0")
            (year / ".hidden").write_text("x", encoding="utf-8")
            # A parent folder with only subfolders, its info and hidden files is not data.
            self.assertFalse(folder_needs_h5_generation(year))
            (run / "log.csv").write_text("t,v\n", encoding="utf-8")
            self.assertTrue(folder_needs_h5_generation(run))
            (run / "log.h5").write_bytes(b"")
            self.assertFalse(folder_needs_h5_generation(run))
            self.assertFalse(folder_needs_h5_generation(Path(directory) / "missing"))


if __name__ == "__main__":
    unittest.main()
