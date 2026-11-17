"""Source data: the records in _info.json, the rows each folder shows, and how they are made."""
import json
import logging
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.data.folder_info import (  # noqa: E402
    EXPORT_SOURCE_NAME, MAGIC_DROP_PLUGIN, SOURCE_EXPORT, SOURCE_IMPORT, read_info_file, record_sources,
    source_entry, write_info_file,
)
from src.jobs import source_detection  # noqa: E402
from src.jobs.import_batch import discover_import_jobs, run_reimport_batch  # noqa: E402
from src.jobs.import_data import CACHE_TOOL_NAME  # noqa: E402
from src.jobs.navigator_snapshot import build_navigator_snapshot  # noqa: E402
from src.jobs.source_detection import (  # noqa: E402
    STATE_CURRENT, STATE_MISSING, STATE_NEW, STATE_OUTDATED, ExportSpec, ImporterDetector, SourceDetection,
    folder_files, folder_source_rows,
)
from src.plugins.catalog import parse_operations, validate_manifest  # noqa: E402
from tests.plugin_fixtures import sample_importer  # noqa: E402


def touch(path: Path, text: str = "x", *, age: float = 0.0) -> Path:
    path.write_text(text, encoding="utf-8")
    if age:
        moment = time.time() - age
        os.utime(path, (moment, moment))
    return path


def rows_for(folder: Path, *, detect=None, origins=None, exports=()):
    info = read_info_file(folder)
    return folder_source_rows(
        folder.resolve(), info, folder_files(folder),
        detect_importer=detect or (lambda path: None),
        h5_origin=lambda path: (origins or {}).get(path.name, {}),
        plugin_titles=lambda config_file, config_name: ("sample", "Sample plugin"),
        exports=exports,
    )


class SourceRecordTests(unittest.TestCase):
    def test_records_are_added_replaced_and_kept_by_concurrent_writers(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "run"
            folder.mkdir()
            record_sources(folder, [source_entry(SOURCE_IMPORT, source="log.csv", output="log.h5", plugin="A")])
            # A re-import with another plugin replaces the import of the same source.
            record_sources(folder, [source_entry(SOURCE_IMPORT, source="LOG.csv", output="log.h5", plugin="B")])
            self.assertEqual([entry["plugin"] for entry in read_info_file(folder).sources], ["B"])
            # Two writers that read the same file both keep their additions.
            first, second = read_info_file(folder), read_info_file(folder)
            first.sources.append(source_entry(SOURCE_EXPORT, source=EXPORT_SOURCE_NAME, output="a.csv",
                                              plugin="X", plugin_id="x", inputs=["log.bin"]))
            second.editable["note"] = "kept"
            write_info_file(folder, first)
            write_info_file(folder, second)
            saved = read_info_file(folder)
            self.assertEqual(saved.editable["note"], "kept")
            self.assertEqual(sorted(entry["output"] for entry in saved.sources), ["a.csv", "log.h5"])
            self.assertEqual(next(entry for entry in saved.sources if entry["kind"] == SOURCE_EXPORT)["inputs"],
                             "log.bin")

    def test_malformed_records_are_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "run"
            folder.mkdir()
            (folder / "run_info.json").write_text(json.dumps({"sources": [
                {"kind": "import", "source": "a.csv", "output": "a.h5"},
                {"kind": "unknown", "source": "b.csv", "output": "b.h5"},
                {"kind": "import", "source": "c.csv"},
            ]}), encoding="utf-8")
            self.assertEqual([entry["source"] for entry in read_info_file(folder).sources], ["a.csv"])
            with self.assertRaises(ValueError):
                (folder / "run_info.json").write_text(json.dumps({"sources": {}}), encoding="utf-8")
                read_info_file(folder)


class SourceRowTests(unittest.TestCase):
    def test_recorded_imports_are_current_outdated_or_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "run"
            folder.mkdir()
            touch(folder / "a.csv", age=100)
            touch(folder / "a.h5", age=50)
            touch(folder / "b.csv")
            touch(folder / "b.h5", age=50)
            touch(folder / "c.csv")
            record_sources(folder, [
                source_entry(SOURCE_IMPORT, source=name, output=name.replace(".csv", ".h5"), plugin="Sample plugin",
                             plugin_id="sample") for name in ("a.csv", "b.csv", "c.csv")])
            states = {row.source: (row.state, row.recorded) for row in rows_for(folder)}
            self.assertEqual(states, {"a.csv": (STATE_CURRENT, True), "b.csv": (STATE_OUTDATED, True),
                                      "c.csv": (STATE_MISSING, True)})

    def test_unrecorded_files_use_their_h5_origin_or_a_detected_importer(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "run"
            folder.mkdir()
            touch(folder / "old.csv", age=100)
            touch(folder / "old.h5")
            touch(folder / "new.csv")
            touch(folder / "notes.txt")
            origins = {"old.h5": {"tool": CACHE_TOOL_NAME, "source_file": str(folder / "old.csv"),
                                  "config_file": "x/importer.json"}}
            rows = rows_for(folder, origins=origins,
                            detect=lambda path: ("sample", "Sample plugin") if path.suffix == ".csv" else None)
            self.assertEqual([(row.source, row.output, row.plugin, row.state, row.recorded) for row in rows], [
                ("new.csv", "", "Sample plugin", STATE_NEW, False),
                ("old.csv", "old.h5", "Sample plugin", STATE_CURRENT, False),
            ])

    def test_parent_folders_info_attachments_and_exports_are_not_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            year = Path(directory) / "2024"
            run = year / "run"
            run.mkdir(parents=True)
            # A parent folder with only subfolders, its info (and lock) and hidden files has no rows.
            (year / "2024_info.json").write_text("{}", encoding="utf-8")
            (year / "2024_info.json.lock").write_bytes(b"\0")
            touch(year / ".hidden")
            self.assertEqual(rows_for(year, detect=lambda path: ("sample", "Sample plugin")), [])
            # An attached video and its audio H5, and an export's output, are not offered for import.
            touch(run / "clip.mp4")
            touch(run / "clip.audio.h5")
            touch(run / "out.csv")
            info = read_info_file(run)
            info.videos.append({"path": "clip.mp4", "offset_seconds": "0"})
            info.sources.append(source_entry(SOURCE_EXPORT, source=EXPORT_SOURCE_NAME, output="out.csv",
                                             plugin="X", plugin_id="x", operation="Export"))
            write_info_file(run, info)
            rows = rows_for(run, detect=lambda path: ("sample", "Sample plugin"))
            self.assertEqual([(row.kind, row.output) for row in rows], [(SOURCE_EXPORT, "out.csv")])

    def test_exports_offer_uncovered_inputs_and_mark_outdated_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "run"
            folder.mkdir()
            touch(folder / "log_1.bin")
            touch(folder / "log_2.bin", age=100)
            touch(folder / "1.csv", age=50)
            spec = ExportSpec("x", "Sample CSV Export", "Sample CSV export", ("*.bin",))
            rows = rows_for(folder, exports=[spec])
            self.assertEqual([(row.source, row.output, row.state, row.inputs) for row in rows],
                             [(EXPORT_SOURCE_NAME, "", STATE_NEW, ("log_1.bin", "log_2.bin"))])
            record_sources(folder, [source_entry(SOURCE_EXPORT, source=EXPORT_SOURCE_NAME, output="1.csv",
                                                 plugin="Sample CSV Export", plugin_id="x", operation="Sample CSV export",
                                                 inputs=["log_1.bin"])])
            rows = rows_for(folder, exports=[spec])
            self.assertEqual([(row.output, row.state, row.inputs) for row in rows], [
                ("1.csv", STATE_OUTDATED, ("log_1.bin",)),
                ("", STATE_NEW, ("log_2.bin",)),
            ])
            # Without input patterns, an export reads the folder's H5 files.
            plain = ExportSpec("y", "Other", "Export")
            self.assertEqual(rows_for(folder, exports=[plain])[-1].plugin, "Sample CSV Export")
            touch(folder / "data.h5")
            self.assertEqual([(row.plugin, row.state) for row in rows_for(folder, exports=[plain])][-1],
                             ("Other", STATE_NEW))


class DetectionTests(unittest.TestCase):
    def test_enabled_importers_find_new_files_in_the_snapshot(self):
        with sample_importer() as plugins, tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            touch(run / "log.csv", "t,v\n0,1\n1,2\n")
            touch(run / "readme.txt")
            with mock.patch("src.jobs.import_data.DEFAULT_CONFIG_DIR", plugins), \
                    mock.patch.object(source_detection, "IMPORTER_DETECTOR", ImporterDetector()):
                without = build_navigator_snapshot(root, None, rescan=True)
                self.assertEqual(without.snapshot.source_rows[run.resolve()], [])
                detection = SourceDetection(("sample",))
                result = build_navigator_snapshot(root, without.snapshot, rescan=False, detection=detection)
            self.assertTrue(result.changed)
            rows = result.snapshot.source_rows[run.resolve()]
            self.assertEqual([(row.source, row.plugin, row.plugin_id, row.state) for row in rows],
                             [("log.csv", "Sample plugin", "sample", STATE_NEW)])

    def test_a_source_row_imports_one_file_and_records_it(self):
        with sample_importer(), tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            run.mkdir()
            touch(run / "a.csv", "t,v\n0,1\n1,2\n")
            touch(run / "b.csv", "t,v\n0,1\n1,2\n")
            logger = logging.getLogger("test_source_data")
            jobs = discover_import_jobs(run, False, logger, enabled_plugins={"sample": True}, sources=("a.csv",),
                                        importer_id="sample")
            self.assertEqual([Path(job.source_path).name for job in jobs[run]], ["a.csv"])
            self.assertEqual(discover_import_jobs(run, False, logger, enabled_plugins={"sample": True},
                                                  sources=("a.csv",), importer_id="other"), {})
            events = []
            queue = mock.Mock(put=events.append)
            run_reimport_batch(run, queue, recursive=False, logger=logger, process_limit=1,
                               enabled_plugins={"sample": True}, sources=("a.csv",), importer_id="sample")
            self.assertEqual(events[-1][:3], ("finished", 1, 0))
            self.assertFalse((run / "b.h5").exists())
            [entry] = read_info_file(run).sources
            self.assertEqual((entry["kind"], entry["source"], entry["output"], entry["plugin"], entry["plugin_id"]),
                             (SOURCE_IMPORT, "a.csv", "a.h5", "Sample plugin", "sample"))

    def test_detection_constant_matches_the_h5_writer(self):
        self.assertEqual(source_detection.CACHE_TOOL_NAME, CACHE_TOOL_NAME)


class ManifestInputsTests(unittest.TestCase):
    def manifest(self, **operation):
        return {"id": "x", "title": "X", "api_version": 1, "capabilities": ["ui"], "entry_point": "plugin.py",
                "operations": {"Export": {"worker": "worker.py:run", "kind": "export", "scopes": ["folder"],
                                          **operation}}}

    def test_export_inputs_are_file_name_patterns(self):
        manifest = self.manifest(inputs=["*.bin"])
        self.assertEqual(validate_manifest(manifest), "")
        self.assertEqual(parse_operations(manifest["operations"])[0].inputs, ("*.bin",))
        for inputs in ("*.bin", ["sub/*.bin"], [""], [3]):
            with self.assertRaises(ValueError):
                validate_manifest(self.manifest(inputs=inputs))


class NavigatorSourceRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_source_rows_show_columns_and_start_imports_on_double_click(self):
        from src.gui.navigator.widget import (
            COLUMN_SOURCE_OUTPUT, COLUMN_SOURCE_PLUGIN, ROW_TYPE_FOLDER, ROW_TYPE_SOURCE, ROW_TYPE_SOURCE_GROUP,
            ITEM_TYPE_ROLE, DataNavigatorWidget, columns_for_category, COLUMN_CATEGORY_SOURCE,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            touch(run / "log.csv")
            settings = root / "settings.json"
            settings.write_text("{}", encoding="utf-8")
            with mock.patch.object(source_detection.IMPORTER_DETECTOR, "importer_for",
                                   lambda path, enabled: ("sample", "Sample plugin") if enabled else None):
                navigator = DataNavigatorWidget(root, settings_path=settings)
                navigator.set_source_detection(SourceDetection(("sample",)))
            try:
                items = list(navigator.tree_items())
                folder = next(item for item in items if item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_FOLDER)
                children = [folder.child(index) for index in range(folder.childCount())]
                # Info first, then Source data.
                self.assertEqual([child.text(0) for child in children[:2]], ["Info", "Source data"])
                group = children[1]
                self.assertEqual(group.data(0, ITEM_TYPE_ROLE), ROW_TYPE_SOURCE_GROUP)
                row_item = group.child(0)
                self.assertEqual(row_item.data(0, ITEM_TYPE_ROLE), ROW_TYPE_SOURCE)
                self.assertEqual((navigator.item_logical_value(row_item, "data"),
                                  navigator.item_logical_value(row_item, COLUMN_SOURCE_OUTPUT),
                                  navigator.item_logical_value(row_item, COLUMN_SOURCE_PLUGIN)),
                                 ("log.csv", "Not imported", "Sample plugin"))
                self.assertEqual([column.title for column in columns_for_category(
                    COLUMN_CATEGORY_SOURCE, navigator.current_info_index)], ["Source", "Output", "Plugin"])
                activated, menus = [], []
                navigator.source_activated.connect(lambda path, row: activated.append((path, row.source)))
                navigator.source_context_menu_requested.connect(lambda path, row, _pos: menus.append(row.source))
                navigator.on_item_activated(row_item, 0)
                self.assertEqual(activated, [(run.resolve(), "log.csv")])
                # A folder double-click only expands or collapses it.
                navigator.on_item_activated(folder, 0)
                self.assertEqual(len(activated), 1)
                navigator.tree.scrollToItem(row_item)
                navigator.on_context_menu_requested(navigator.tree.visualItemRect(row_item).center())
                self.assertEqual(menus, ["log.csv"])
            finally:
                navigator.close()
                navigator.deleteLater()


class WindowSourceActionTests(unittest.TestCase):
    """The main window's Source data actions, on a stand-in window."""

    def window(self, *, importers=("sample",), enabled=True):
        from types import SimpleNamespace
        manager = mock.Mock()
        manager.enabled_importers.return_value = [SimpleNamespace(plugin_id=plugin_id) for plugin_id in importers]
        manager.is_enabled.return_value = enabled
        manager.descriptors = [SimpleNamespace(plugin_id="x", title="Sample CSV Export")]
        window = SimpleNamespace(plugin_manager=manager, internal_logger=mock.Mock(), start_import=mock.Mock(),
                                 start_draft_import=mock.Mock(),
                                 schedule_tree_refresh_after_completed_process=mock.Mock())
        from src.app.lodgazilla import LODGaZillaWindow
        window.run_source = lambda folder, row: LODGaZillaWindow.run_source(window, folder, row)
        return window

    def test_rows_start_their_import_or_export(self):
        from src.app.lodgazilla import LODGaZillaWindow
        from src.jobs.source_detection import SourceRow
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            touch(folder / "log.csv")
            window = self.window()
            row = SourceRow(SOURCE_IMPORT, "log.csv", "", "Sample plugin", "sample", STATE_NEW)
            LODGaZillaWindow.run_source(window, folder, row)
            request = window.start_import.call_args.args[0]
            self.assertEqual((request.folder, request.sources, request.importer_id),
                             (folder.resolve(), ("log.csv",), "sample"))
            # A disabled importer is named instead.
            window = self.window(importers=())
            LODGaZillaWindow.run_source(window, folder, row)
            window.start_import.assert_not_called()
            self.assertIn("Sample plugin", window.internal_logger.warning.call_args.args[0])
            # A Magic drop row runs its own parser again.
            window = self.window()
            LODGaZillaWindow.run_source(window, folder, SourceRow(SOURCE_IMPORT, "log.csv", "log.h5",
                                                                  MAGIC_DROP_PLUGIN, "", STATE_CURRENT,
                                                                  draft=str(folder / "draft")))
            window.start_draft_import.assert_called_once_with(folder / "draft", folder / "log.csv",
                                                              folder / "log.h5")
            # An export passes its input files.
            export = SourceRow(SOURCE_EXPORT, EXPORT_SOURCE_NAME, "", "Sample CSV Export", "x", STATE_NEW,
                               operation="Sample CSV export", inputs=("log_1.bin",))
            LODGaZillaWindow.run_source(window, folder, export)
            window.plugin_manager.run_operation.assert_called_once_with(
                "x", "Sample CSV export", folder, parameters={"source_files": [str(folder / "log_1.bin")]})

    def test_double_click_asks_before_doing_it_again(self):
        from PySide6.QtWidgets import QMessageBox
        from src.app.lodgazilla import LODGaZillaWindow
        from src.jobs.source_detection import SourceRow
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            touch(folder / "log.csv")
            done = SourceRow(SOURCE_IMPORT, "log.csv", "log.h5", "Sample plugin", "sample", STATE_OUTDATED)
            for answer, started in ((QMessageBox.StandardButton.No, False), (QMessageBox.StandardButton.Yes, True)):
                window = self.window()
                with mock.patch("src.app.lodgazilla.QMessageBox.question", return_value=answer) as question:
                    LODGaZillaWindow.activate_source(window, folder, done)
                self.assertIn("This replaces log.h5.", question.call_args.args[2])
                self.assertEqual(window.start_import.called, started)
            window = self.window()
            with mock.patch("src.app.lodgazilla.QMessageBox.question") as question:
                LODGaZillaWindow.activate_source(window, folder, SourceRow(
                    SOURCE_IMPORT, "log.csv", "", "Sample plugin", "sample", STATE_NEW))
            question.assert_not_called()
            window.start_import.assert_called_once()

    def test_reported_export_outputs_are_recorded(self):
        from types import SimpleNamespace
        from src.app.lodgazilla import LODGaZillaWindow
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "run"
            folder.mkdir()
            window = self.window()
            active = SimpleNamespace(plugin_id="x", operation="Sample CSV export")
            LODGaZillaWindow.record_export_output(window, active, str(folder / "1.csv"),
                                                  [str(folder / "log_1.bin"), str(Path(directory) / "elsewhere.bin")])
            [entry] = read_info_file(folder).sources
            self.assertEqual((entry["kind"], entry["source"], entry["output"], entry["plugin"], entry["operation"],
                              entry["inputs"]),
                             (SOURCE_EXPORT, EXPORT_SOURCE_NAME, "1.csv", "Sample CSV Export", "Sample CSV export",
                              "log_1.bin"))
            window.schedule_tree_refresh_after_completed_process.assert_called_once()


if __name__ == "__main__":
    unittest.main()
