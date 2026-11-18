"""Generic grouped imports; deliberately independent of installed plugins."""
import json
import logging
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import h5py
from src.data.import_sets import ImportSet
from src.data.folder_info import read_info_file
from src.data.navigator_data import read_h5_navigator_data
from src.jobs.import_batch import discover_import_jobs, run_import_job, record_import_source, duplicate_output_paths
from src.jobs.source_detection import (ImporterDetector, folder_source_rows, folder_files, STATE_CURRENT,
                                       STATE_INCOMPLETE, STATE_OUTDATED, STATE_MISSING)
from tests.plugin_fixtures import sample_importer


GROUP_PARSER = '''
from src.contracts.importer import ImportSet
class Parser(Parser):
    def import_set(self, path):
        return ImportSet('sample-set', 'Sample set', ('a.csv', 'b.csv', 'c.csv'),
                         'combined.h5', ('clock.txt',))
'''


class GroupedImportTests(unittest.TestCase):
    def setUp(self):
        self.plugins = self.enterContext(sample_importer())
        parser = self.plugins / "sample" / "importer.py"
        parser.write_text(parser.read_text() + GROUP_PARSER)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.folder = self.root / "run"
        self.folder.mkdir()
        for name in ("a.csv", "b.csv", "c.csv"):
            (self.folder / name).write_text("t,v\n0,1\n1,2\n")
        (self.folder / "clock.txt").write_text("reference")
        self.logger = logging.getLogger(__name__)

    def jobs(self, **kwargs):
        return discover_import_jobs(self.folder, False, self.logger, enabled_plugins={"sample": True}, **kwargs)

    def rows(self, *, recorded=True):
        detector = ImporterDetector()
        with mock.patch("src.jobs.import_data.DEFAULT_CONFIG_DIR", self.plugins):
            groups = detector.sets_for(self.folder, ("sample",))
        return folder_source_rows(
            self.folder, read_info_file(self.folder) if recorded else None, folder_files(self.folder),
            detect_importer=lambda path: None,
            h5_origin=lambda path: read_h5_navigator_data(path).origin,
            plugin_titles=lambda *args: ("sample", "Sample plugin"), import_sets=groups)

    def test_members_schedule_one_job_from_any_selected_member(self):
        jobs = self.jobs()[self.folder]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].output_path.name, "combined.h5")
        self.assertEqual(len(self.jobs(sources=("c.csv", "a.csv"))[self.folder]), 1)
        self.assertEqual(len(self.jobs(sources=("b.csv",))[self.folder]), 1)

    def test_provenance_recovery_freshness_and_missing_members(self):
        [job] = self.jobs()[self.folder]
        result = run_import_job(job)
        self.assertTrue(result.imported, result.error)
        record_import_source(result, self.logger)
        [record] = read_info_file(self.folder).sources
        self.assertEqual(record["inputs"], "a.csv|b.csv|c.csv")
        self.assertEqual(record["dependencies"], "clock.txt")
        with h5py.File(job.output_path) as handle:
            self.assertEqual(json.loads(handle.attrs["source_files_json"]), ["a.csv", "b.csv", "c.csv"])
        for recorded in (False, True):
            rows = [row for row in self.rows(recorded=recorded) if row.set_key]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].state, STATE_CURRENT)
            self.assertEqual(rows[0].inputs, ("a.csv", "b.csv", "c.csv"))
        future = time.time() + 10
        os.utime(self.folder / "clock.txt", (future, future))
        self.assertEqual(next(row for row in self.rows() if row.set_key).state, STATE_OUTDATED)
        (self.folder / "b.csv").unlink()
        row = next(row for row in self.rows() if row.set_key)
        self.assertEqual(row.state, STATE_INCOMPLETE)
        self.assertIn("b.csv", row.problem)

    def test_deleted_output_and_disabled_plugin_keep_group(self):
        [job] = self.jobs()[self.folder]
        result = run_import_job(job)
        record_import_source(result, self.logger)
        job.output_path.unlink()
        rows = folder_source_rows(self.folder, read_info_file(self.folder), folder_files(self.folder),
                                  detect_importer=lambda path: None, h5_origin=lambda path: {})
        self.assertEqual([row.state for row in rows if row.set_key], [STATE_MISSING])
        self.assertFalse(any(row.source in {"a.csv", "b.csv", "c.csv"} for row in rows))

    def test_incomplete_or_cancelled_import_preserves_previous_output(self):
        import threading
        [job] = self.jobs()[self.folder]
        self.assertTrue(run_import_job(job).imported)
        before = job.output_path.read_bytes()
        cancelled = threading.Event()
        cancelled.set()
        self.assertFalse(run_import_job(job, cancel_event=cancelled).imported)
        (self.folder / "c.csv").unlink()
        result = run_import_job(job)
        self.assertFalse(result.imported)
        self.assertIn("c.csv", result.error)
        self.assertEqual(job.output_path.read_bytes(), before)
        self.assertFalse(list(self.folder.glob(".*.tmp")))

    def test_reimport_from_another_member_and_unrelated_output_protection(self):
        [first] = self.jobs(sources=("a.csv",))[self.folder]
        self.assertTrue(run_import_job(first).imported)
        [second] = self.jobs(sources=("c.csv",))[self.folder]
        self.assertTrue(run_import_job(second).imported)
        with h5py.File(second.output_path, "a") as handle:
            handle.attrs["import_set_key"] = "someone-else"
        result = run_import_job(second)
        self.assertFalse(result.imported)
        self.assertIn("Not replacing", result.error)

    def test_duplicate_outputs_are_checked_after_deduplication(self):
        from dataclasses import replace
        [job] = self.jobs()[self.folder]
        other = replace(job, import_set=replace(job.import_set, key="another"))
        self.assertEqual(duplicate_output_paths({self.folder: [job, other]}), [job.output_path.resolve()])
        groups = [("sample", "Sample", job.import_set), ("sample", "Sample", other.import_set)]
        rows = folder_source_rows(self.folder, None, folder_files(self.folder),
                                  detect_importer=lambda path: None, h5_origin=lambda path: {}, import_sets=groups)
        self.assertEqual([row.state for row in rows if row.set_key], [STATE_INCOMPLETE, STATE_INCOMPLETE])

    def test_trial_plan_and_import_use_group_output(self):
        from src.ai.trial_import import trial_import
        draft = self.plugins / "sample"
        plan = trial_import(draft, self.folder / "b.csv", self.folder / "b.h5", plan_only=True)
        self.assertEqual(Path(plan["output"]).name, "combined.h5")
        self.assertEqual(plan["inputs"], ["a.csv", "b.csv", "c.csv"])
        self.assertFalse((self.folder / "combined.h5").exists())
        summary = trial_import(draft, self.folder / "b.csv", self.folder / "b.h5")
        self.assertEqual(summary["output"], plan["output"])

    def test_invalid_set_paths_are_rejected(self):
        for name in ("../secret.csv", "C:secret.csv", "a|b.csv", "", "a/b.csv"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                ImportSet("x", "X", (name,), "result.h5")

    def test_example_job_plans_once_and_does_not_retry_failed_members(self):
        from src.jobs.plugin_examples import PluginExamplesJob
        calls = []

        def trial(_plugin, source, _output, **kwargs):
            calls.append((source.name, kwargs.get("plan_only", False)))
            if kwargs.get("plan_only"):
                return {"output": str(self.folder / "combined.h5"),
                        "inputs": ["a.csv", "b.csv", "c.csv"], "dependencies": ["clock.txt"]}
            raise ValueError("invalid samples")

        job = PluginExamplesJob(self.plugins / "sample", self.folder)
        try:
            with mock.patch("src.jobs.plugin_examples.run_trial_import", side_effect=trial):
                job._run()
            self.assertEqual(calls, [("a.csv", True), ("a.csv", False)])
        finally:
            job.deleteLater()

    def test_installed_plugin_drop_copies_the_complete_set_and_reference(self):
        from src.jobs.magic_drop import PluginDropJob
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch("src.jobs.magic_drop.magic_drop.magic_drop_folder", return_value=Path(directory) / "drop"), \
                mock.patch("src.jobs.import_data.load_import_configurations") as load:
            from src.jobs.import_data import load_import_configuration
            load.return_value = [load_import_configuration(self.plugins / "sample" / "importer.json")]
            job = PluginDropJob([self.folder / "b.csv"], Path(directory), {"sample": True})
            try:
                job._run()
                self.assertIsNotNone(job.folder)
                self.assertEqual({p.name for p in job.folder.iterdir()}, {"a.csv", "b.csv", "c.csv", "clock.txt"})
            finally:
                job.deleteLater()

    def test_changed_member_is_not_hidden_by_unchanged_directory_timestamp(self):
        from src.jobs.navigator_snapshot import build_navigator_snapshot
        from src.jobs.source_detection import SourceDetection
        [job] = self.jobs()[self.folder]
        record_import_source(run_import_job(job), self.logger)
        with mock.patch("src.jobs.import_data.DEFAULT_CONFIG_DIR", self.plugins):
            detection = SourceDetection(("sample",))
            first = build_navigator_snapshot(self.root, None, rescan=True, detection=detection)
            folder_time = self.folder.stat().st_mtime_ns
            future = time.time() + 10
            os.utime(self.folder / "b.csv", (future, future))
            self.assertEqual(self.folder.stat().st_mtime_ns, folder_time)
            second = build_navigator_snapshot(self.root, first.snapshot, rescan=False, detection=detection)
        rows = second.snapshot.source_rows[self.folder.resolve()]
        self.assertEqual(next(row for row in rows if row.set_key).state, STATE_OUTDATED)


class GroupedSourceWidgetTests(unittest.TestCase):
    def test_children_are_non_actionable_and_parent_shows_planned_output(self):
        from PySide6.QtWidgets import QApplication
        from src.gui.navigator.widget import DataNavigatorWidget, source_output_text, ITEM_SOURCE_ROLE
        from src.jobs.source_detection import SourceRow, STATE_NEW
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "settings.json"
            settings.write_text("{}")
            widget = DataNavigatorWidget(root, settings_path=settings)
            try:
                row = SourceRow("import", "Combined", "result.h5", "Example", "example", STATE_NEW,
                                inputs=("a.csv", "b.csv", "c.csv"), set_key="combined",
                                dependencies=("clock.h5",))
                item = widget.make_source_item(root, row)
                self.assertEqual(item.childCount(), 4)
                self.assertIn("result.h5", source_output_text(row))
                activations = []
                widget.source_activated.connect(lambda *args: activations.append(args))
                widget.on_item_activated(item.child(0), 0)
                self.assertEqual(activations, [])
                widget.on_item_activated(item, 0)
                self.assertEqual(len(activations), 1)
                self.assertEqual(source_output_text(item.child(3).data(0, ITEM_SOURCE_ROLE)), "Reference")
            finally:
                widget.close()
                widget.deleteLater()
                app.processEvents()


if __name__ == "__main__":
    unittest.main()
