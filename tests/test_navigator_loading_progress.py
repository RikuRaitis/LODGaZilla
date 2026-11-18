"""The Navigator's Loading row shows how many folders of the data folder are read."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.gui.navigator.widget import (  # noqa: E402
    ITEM_TYPE_ROLE, LOADING_MESSAGE, ROW_TYPE_FOLDER, ROW_TYPE_MESSAGE, DataNavigatorWidget, loading_progress_text,
)
from src.jobs import navigator_snapshot  # noqa: E402
from src.jobs.navigator_snapshot import build_navigator_snapshot  # noqa: E402


class SnapshotProgressTests(unittest.TestCase):
    def test_progress_counts_each_folder_once_it_is_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("a", "b", "c"):
                (root / name).mkdir()
            reports = []
            result = build_navigator_snapshot(root, None, rescan=True,
                                              progress=lambda done, total: reports.append((done, total)))
            # First the folders are found, then each is reported once read.
            self.assertEqual(reports, [(0, 0), (0, 3), (1, 3), (2, 3), (3, 3)])
            # A refresh without a rescan reuses the folder list: no finding step.
            reports.clear()
            build_navigator_snapshot(root, result.snapshot, rescan=False,
                                     progress=lambda done, total: reports.append((done, total)))
            self.assertEqual(reports[0], (0, 3))
            self.assertEqual(reports[-1], (3, 3))

    def test_a_first_read_sends_the_folders_read_so_far(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("a", "b", "c"):
                (root / name).mkdir()
            partials = []
            with mock.patch.object(navigator_snapshot, "PARTIAL_INTERVAL_SECONDS", 0):
                result = build_navigator_snapshot(root, None, rescan=True, partial=partials.append)
                # Each part has the folders read so far; the last one comes as the finished read.
                self.assertEqual([[folder.name for folder in part.folders] for part in partials], [["a"], ["a", "b"]])
                self.assertEqual(set(partials[0].folder_infos), {(root / "a").resolve()})
                # A refresh of a finished read has nothing to show early.
                partials.clear()
                build_navigator_snapshot(root, result.snapshot, rescan=True, partial=partials.append)
                self.assertEqual(partials, [])

    def test_progress_text(self):
        self.assertEqual(loading_progress_text(0, 0), f"{LOADING_MESSAGE} finding folders")
        self.assertEqual(loading_progress_text(120, 450), f"{LOADING_MESSAGE} 120 / 450 folders read (26 %)")
        self.assertEqual(loading_progress_text(450, 450), f"{LOADING_MESSAGE} 450 / 450 folders read (100 %)")


class LoadingRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_only_the_loading_row_shows_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "run").mkdir()
            settings = root / "settings.json"
            settings.write_text("{}", encoding="utf-8")
            navigator = DataNavigatorWidget(None, settings_path=settings)
            self.addCleanup(navigator.deleteLater)
            self.addCleanup(navigator.close)
            navigator.add_message_item(LOADING_MESSAGE)
            navigator._on_snapshot_progress(3, 12)
            row = navigator.tree.topLevelItem(0)
            self.assertEqual(row.data(0, ITEM_TYPE_ROLE), ROW_TYPE_MESSAGE)
            self.assertEqual(row.text(0), f"{LOADING_MESSAGE} 3 / 12 folders read (25 %)")
            # Another message, or a built tree, is left as it is.
            navigator.tree.clear()
            navigator.add_message_item("Folder does not exist: x")
            navigator._on_snapshot_progress(4, 12)
            self.assertEqual(navigator.tree.topLevelItem(0).text(0), "Folder does not exist: x")

    def test_folders_read_so_far_show_below_the_loading_row(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("a", "b", "c"):
                (root / name).mkdir()
            settings = root / "settings.json"
            settings.write_text("{}", encoding="utf-8")
            navigator = DataNavigatorWidget(None, settings_path=settings)
            self.addCleanup(navigator.deleteLater)
            self.addCleanup(navigator.close)
            navigator.folder_edit.setText(str(root.resolve()))
            partials = []
            with mock.patch.object(navigator_snapshot, "PARTIAL_INTERVAL_SECONDS", 0):
                build_navigator_snapshot(root.resolve(), None, rescan=True, partial=partials.append)
            navigator._snapshot_job = SimpleNamespace(root=root.resolve(), cancel=lambda: None)
            navigator._on_snapshot_progress(2, 3)
            navigator._on_snapshot_partial(root.resolve(), partials[-1])
            rows = [navigator.tree.topLevelItem(index) for index in range(navigator.tree.topLevelItemCount())]
            self.assertEqual(rows[0].text(0), f"{LOADING_MESSAGE} 2 / 3 folders read (66 %)")
            self.assertEqual(sorted(row.text(0) for row in rows if row.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_FOLDER),
                             ["a", "b"])
            # The progress keeps updating the row on top; the part is never the finished read.
            navigator._on_snapshot_progress(3, 3)
            self.assertEqual(navigator.tree.topLevelItem(0).text(0), f"{LOADING_MESSAGE} 3 / 3 folders read (100 %)")
            self.assertIsNone(navigator._source_snapshot)
            # A part from a read of another folder is ignored.
            navigator._snapshot_job = SimpleNamespace(root=root / "elsewhere", cancel=lambda: None)
            navigator._on_snapshot_partial(root.resolve(), partials[0])
            self.assertEqual(navigator.tree.topLevelItemCount(), len(rows))
            navigator._snapshot_job = None


if __name__ == "__main__":
    unittest.main()
