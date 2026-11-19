"""The Navigator reads a data folder in phases, showing the folders at once and filling them in."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.gui.navigator.widget import (  # noqa: E402
    ITEM_LOADING_ROLE, ITEM_TYPE_ROLE, ROW_TYPE_FOLDER, DataNavigatorWidget, loading_phase_text,
)
from src.jobs import navigator_snapshot  # noqa: E402
from src.jobs.navigator_snapshot import PHASE_DATA, PHASE_FINDING, PHASE_SOURCES, build_navigator_snapshot  # noqa: E402


class SnapshotProgressTests(unittest.TestCase):
    def test_progress_reports_each_phase_folder_by_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("a", "b", "c"):
                (root / name).mkdir()
            reports = []
            result = build_navigator_snapshot(root, None, rescan=True,
                                              progress=lambda *report: reports.append(report))
            # The folders are found, their data files read, and last their Source data found.
            self.assertEqual(reports, [(0, 0, PHASE_FINDING),
                                       (0, 3, PHASE_DATA), (1, 3, PHASE_DATA), (2, 3, PHASE_DATA), (3, 3, PHASE_DATA),
                                       (0, 3, PHASE_SOURCES), (1, 3, PHASE_SOURCES), (2, 3, PHASE_SOURCES),
                                       (3, 3, PHASE_SOURCES)])
            # A refresh without a rescan reuses the folder list: no finding step.
            reports.clear()
            build_navigator_snapshot(root, result.snapshot, rescan=False,
                                     progress=lambda *report: reports.append(report))
            self.assertEqual(reports[0], (0, 3, PHASE_DATA))
            self.assertEqual(reports[-1], (3, 3, PHASE_SOURCES))

    def test_a_first_read_shows_every_folder_at_once_and_fills_them_in(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("a", "b", "c"):
                (root / name).mkdir()
            names = lambda paths: sorted(path.name for path in paths)  # noqa: E731
            partials = []
            with mock.patch.object(navigator_snapshot, "PARTIAL_INTERVAL_SECONDS", 0):
                result = build_navigator_snapshot(root, None, rescan=True, partial=partials.append)
                # Every part has every folder; the first before any _info.json is read.
                self.assertTrue(all(names(part.folders) == ["a", "b", "c"] for part in partials))
                self.assertEqual(partials[0].folder_infos, {})
                self.assertEqual(names(partials[0].loading), ["a", "b", "c"])
                self.assertEqual(set(partials[1].folder_infos), {(root / name).resolve() for name in "abc"})
                # The Source data is found last, folder by folder; the finished read has none loading.
                self.assertEqual([names(part.loading) for part in partials[-3:]],
                                 [["a", "b", "c"], ["b", "c"], ["c"]])
                self.assertEqual(result.snapshot.loading, frozenset())
                # A refresh of a finished read has nothing to show early.
                partials.clear()
                build_navigator_snapshot(root, result.snapshot, rescan=True, partial=partials.append)
                self.assertEqual(partials, [])

    def test_phase_text(self):
        self.assertEqual(loading_phase_text(PHASE_FINDING, 0), "Finding folders…")
        self.assertEqual(loading_phase_text(PHASE_DATA, 450), "Reading data of 450 folders…")
        self.assertEqual(loading_phase_text(PHASE_SOURCES, 1), "Finding Source data in 1 folder…")


class LoadingRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_navigator(self, root):
        settings = root / "settings.json"
        settings.write_text("{}", encoding="utf-8")
        navigator = DataNavigatorWidget(None, settings_path=settings)
        self.addCleanup(navigator.deleteLater)
        self.addCleanup(navigator.close)
        return navigator

    def test_each_phase_is_reported_once_for_the_internal_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "run").mkdir()
            navigator = self.make_navigator(root)
            statuses = []
            navigator.loading_status.connect(statuses.append)
            for done in range(3):
                navigator._on_snapshot_progress(done, 12, PHASE_DATA)
            navigator._on_snapshot_progress(0, 12, PHASE_SOURCES)
            navigator._on_snapshot_progress(5, 12, PHASE_SOURCES)
            self.assertEqual(statuses, ["Reading data of 12 folders…", "Finding Source data in 12 folders…"])
            # The tree has no status row.
            self.assertEqual(navigator.tree.topLevelItemCount(), 0)

    def test_folders_still_loading_turn_a_circle_below_the_loading_row(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("a", "b", "c"):
                (root / name).mkdir()
            navigator = self.make_navigator(root)
            navigator.folder_edit.setText(str(root.resolve()))
            partials = []
            with mock.patch.object(navigator_snapshot, "PARTIAL_INTERVAL_SECONDS", 0):
                result = build_navigator_snapshot(root.resolve(), None, rescan=True, partial=partials.append)
            navigator._snapshot_job = SimpleNamespace(root=root.resolve(), cancel=lambda: None)
            # The part where only "c" still waits for its Source data.
            navigator._on_snapshot_partial(root.resolve(), partials[-1])
            rows = [navigator.tree.topLevelItem(index) for index in range(navigator.tree.topLevelItemCount())]
            # No status row: only the folders.
            self.assertTrue(all(row.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_FOLDER for row in rows))
            folders = {row.text(0): row for row in rows}
            self.assertEqual(sorted(folders), ["a", "b", "c"])
            self.assertEqual({name: bool(row.data(0, ITEM_LOADING_ROLE)) for name, row in folders.items()},
                             {"a": False, "b": False, "c": True})
            self.assertTrue(navigator.tree._loading_timer.isActive())
            navigator.tree.viewport().repaint()  # The circle is drawn where the triangle would be.
            # The part is never the finished read.
            self.assertIsNone(navigator._source_snapshot)
            # A part from a read of another folder is ignored.
            navigator._snapshot_job = SimpleNamespace(root=root / "elsewhere", cancel=lambda: None)
            navigator._on_snapshot_partial(root.resolve(), partials[0])
            self.assertEqual(navigator.tree.topLevelItemCount(), len(rows))
            # The finished read: no circles, and the animation stops.
            navigator._snapshot_job = None
            navigator._partial_snapshot = None
            navigator._apply_snapshot_result(result)
            navigator.rebuild_tree()
            self.assertFalse(any(navigator.tree.topLevelItem(index).data(0, ITEM_LOADING_ROLE)
                                 for index in range(navigator.tree.topLevelItemCount())))
            self.assertFalse(navigator.tree._loading_timer.isActive())


if __name__ == "__main__":
    unittest.main()
