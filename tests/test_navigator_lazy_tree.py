"""The Navigator makes a folder's contents when it is opened and updates folders in place."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import h5py  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.data.folder_info import FolderInfoData, write_info_file  # noqa: E402
from src.gui.navigator.widget import (  # noqa: E402
    DIAGNOSTIC_SAMPLE_LOSS, ITEM_CONTENT_PENDING_ROLE, ITEM_DIAGNOSTIC_FLAGS_ROLE, ITEM_LOADING_ROLE, ITEM_TYPE_ROLE,
    ROW_TYPE_FOLDER, ROW_TYPE_IMAGE, ROW_TYPE_SERIES, DataNavigatorWidget,
)
from src.jobs import navigator_snapshot  # noqa: E402
from src.jobs.navigator_snapshot import build_navigator_snapshot  # noqa: E402


def write_h5(path, *, warning=""):
    with h5py.File(path, "w") as h5:
        group = h5.create_group("series/IMU/X")
        group.attrs.update(navigator_group="IMU", navigator_name="X", unit="m/s^2",
                           time_dataset="time_unix", time_unit="s")
        group.create_dataset("time_unix", data=[0.0, 1.0])
        group.create_dataset("value", data=[1.0, 2.0])
        h5.attrs["series_index_json"] = json.dumps([{
            "name": "IMU/X", "navigator_group": "IMU", "navigator_name": "X", "unit": "m/s^2",
            "sample_count": 2, "diagnostic_warning": warning}])


class LazyTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name in ("a", "b"):
            (self.root / name).mkdir()
            write_h5(self.root / name / "data.h5", warning="Sampleloss detected" if name == "b" else "")
        write_info_file(self.root / "a", FolderInfoData(images=[{"path": "photo.png"}]))
        settings = self.root / "settings.json"
        settings.write_text("{}", encoding="utf-8")
        self.navigator = DataNavigatorWidget(None, settings_path=settings)
        self.addCleanup(self.navigator.deleteLater)
        self.addCleanup(self.navigator.close)
        self.navigator.folder_edit.setText(str(self.root))

    def folders(self):
        tree = self.navigator.tree
        return {tree.topLevelItem(i).text(0): tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                if tree.topLevelItem(i).data(0, ITEM_TYPE_ROLE) == ROW_TYPE_FOLDER}

    def show(self, snapshot, *, partial):
        navigator = self.navigator
        if partial:
            navigator._snapshot_job = SimpleNamespace(root=self.root, cancel=lambda: None)
            navigator._on_snapshot_partial(self.root, snapshot)
            navigator._snapshot_job = None
        else:
            navigator._partial_snapshot = None
            navigator._apply_snapshot_result(SimpleNamespace(snapshot=snapshot, read_errors={}, read_ok=set(),
                                                             changed=True))
            navigator.update_tree_view()

    def test_a_folder_is_made_when_opened_and_keeps_its_warning_style_before(self):
        result = build_navigator_snapshot(self.root, None, rescan=True)
        self.show(result.snapshot, partial=False)
        folders = self.folders()
        a, b = folders["a"], folders["b"]
        # Only the folder rows: each shows its open triangle, its contents are not made yet.
        self.assertEqual((a.childCount(), b.childCount()), (0, 0))
        self.assertTrue(a.data(0, ITEM_CONTENT_PENDING_ROLE))
        self.assertEqual(int(a.childIndicatorPolicy().value), 0)  # ShowIndicator
        # Its Sample loss style comes from its data.
        self.assertTrue(int(b.data(0, ITEM_DIAGNOSTIC_FLAGS_ROLE) or 0) & DIAGNOSTIC_SAMPLE_LOSS)
        self.assertFalse(int(a.data(0, ITEM_DIAGNOSTIC_FLAGS_ROLE) or 0) & DIAGNOSTIC_SAMPLE_LOSS)
        # Opening it makes Info, Media (its image, from the background read) and its series.
        a.setExpanded(True)
        self.assertFalse(a.data(0, ITEM_CONTENT_PENDING_ROLE))
        rows = list(self.navigator.tree_items())
        self.assertEqual(sum(1 for row in rows if row.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_SERIES), 1)
        image = next(row for row in rows if row.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_IMAGE)
        self.assertEqual(image.text(0), "photo.png")
        self.assertEqual(b.childCount(), 0)
        # A rebuild opens it again with its contents.
        self.navigator.rebuild_tree()
        a = self.folders()["a"]
        self.assertTrue(a.isExpanded())
        self.assertGreater(a.childCount(), 0)

    def test_parts_of_a_read_update_the_rows_in_place(self):
        partials = []
        with mock.patch.object(navigator_snapshot, "PARTIAL_INTERVAL_SECONDS", 0):
            result = build_navigator_snapshot(self.root, None, rescan=True, partial=partials.append)
        self.show(partials[0], partial=True)
        first = self.folders()
        self.assertTrue(all(item.data(0, ITEM_LOADING_ROLE) for item in first.values()))
        first["a"].setExpanded(True)
        self.navigator.tree.setCurrentItem(first["a"])
        # Each later part keeps the same rows, opened and current, and fills them in.
        for part in partials[1:]:
            self.show(part, partial=True)
            self.assertEqual({name: id(item) for name, item in self.folders().items()},
                             {name: id(item) for name, item in first.items()})
        self.show(result.snapshot, partial=False)
        folders = self.folders()
        self.assertIs(folders["a"], first["a"])
        self.assertTrue(folders["a"].isExpanded())
        self.assertIs(self.navigator.tree.currentItem(), folders["a"])
        self.assertFalse(any(item.data(0, ITEM_LOADING_ROLE) for item in folders.values()))
        self.assertFalse(self.navigator.tree._loading_timer.isActive())
        series = [row for row in self.navigator.tree_items() if row.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_SERIES]
        self.assertEqual(len(series), 1)  # In the opened folder "a"; "b" is still closed.

    def test_attachment_paths_are_resolved_in_the_background_read(self):
        result = build_navigator_snapshot(self.root, None, rescan=True)
        links = result.snapshot.links[self.root / "a"]
        self.assertEqual(links.images, ((self.root / "a" / "photo.png").resolve(),))
        self.assertIs(links.info, result.snapshot.folder_infos[self.root / "a"])
        # Unchanged folder info: the resolved links are kept on a refresh.
        again = build_navigator_snapshot(self.root, result.snapshot, rescan=False)
        self.assertIs(again.snapshot.links[self.root / "a"], links)


if __name__ == "__main__":
    unittest.main()
