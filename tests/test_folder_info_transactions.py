"""Concurrent metadata writers must preserve edits and attachment links."""

import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.data.folder_info import FolderInfoData, read_info_file, write_info_file


def _edit_in_process(folder, key, ready, release):
    info = read_info_file(folder)
    info.editable[key] = key
    ready.put(key)
    if not release.wait(15):
        raise TimeoutError("Metadata test did not release its writer")
    write_info_file(folder, info)


class FolderInfoTransactionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        write_info_file(self.folder, FolderInfoData(editable={"comment": "before"},
            videos=[{"path": "a.mp4", "offset_seconds": "0"}]))

    def test_export_snapshot_preserves_new_user_edit_and_attachment(self):
        exporter, user = read_info_file(self.folder), read_info_file(self.folder)
        user.editable["comment"] = "user edit"
        user.images.append({"path": "photo.png"})
        write_info_file(self.folder, user)
        exporter.plugin["LOG CSV Exporter output"] = "done"
        write_info_file(self.folder, exporter)
        saved = read_info_file(self.folder)
        self.assertEqual(saved.editable["comment"], "user edit")
        self.assertEqual(saved.images, [{"path": "photo.png"}])
        self.assertEqual(saved.plugin, {"LOG CSV Exporter output": "done"})

    def test_attachment_add_remove_and_sync_do_not_overwrite_each_other(self):
        sync, attach = read_info_file(self.folder), read_info_file(self.folder)
        sync.videos[0]["offset_seconds"] = "3.5"
        attach.videos.append({"path": "b.mp4", "offset_seconds": "0"})
        write_info_file(self.folder, attach)
        write_info_file(self.folder, sync)
        self.assertEqual(read_info_file(self.folder).videos, [
            {"path": "a.mp4", "offset_seconds": "3.5"}, {"path": "b.mp4", "offset_seconds": "0"}])
        remove, edit = read_info_file(self.folder), read_info_file(self.folder)
        remove.videos.pop(0)
        edit.videos[1]["offset_seconds"] = "8"
        write_info_file(self.folder, edit)
        write_info_file(self.folder, remove)
        self.assertEqual(read_info_file(self.folder).videos, [{"path": "b.mp4", "offset_seconds": "8"}])

    def test_conflicting_edit_or_delete_fails_without_losing_saved_value(self):
        for delete in (False, True):
            with self.subTest(delete=delete):
                first, second = read_info_file(self.folder), read_info_file(self.folder)
                first.editable["comment"] += " first"
                if delete:
                    del second.editable["comment"]
                else:
                    second.editable["comment"] += " second"
                write_info_file(self.folder, first)
                with self.assertRaisesRegex(ValueError, "changed in another writer"):
                    write_info_file(self.folder, second)
                self.assertEqual(read_info_file(self.folder).editable, first.editable)

    def test_simultaneous_processes_preserve_both_changes(self):
        context = multiprocessing.get_context("spawn")
        ready, release = context.Queue(), context.Event()
        processes = [context.Process(target=_edit_in_process,
            args=(str(self.folder), key, ready, release)) for key in ("one", "two")]
        try:
            for process in processes:
                process.start()
            self.assertEqual({ready.get(timeout=20), ready.get(timeout=20)}, {"one", "two"})
            release.set()
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(read_info_file(self.folder).editable,
                             {"comment": "before", "one": "one", "two": "two"})
            self.assertEqual(list(self.folder.glob("*.tmp")), [])
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            ready.close()

    def test_failed_replace_keeps_original_and_cleans_unique_temp(self):
        info = read_info_file(self.folder)
        info.editable["comment"] = "changed"
        with mock.patch("pathlib.Path.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                write_info_file(self.folder, info)
        self.assertEqual(read_info_file(self.folder).editable["comment"], "before")
        self.assertEqual(list(self.folder.glob("*.tmp")), [])

    def test_reusing_a_saved_object_and_copying_to_another_folder(self):
        user = read_info_file(self.folder)
        user.editable["comment"] = "updated"
        write_info_file(self.folder, user)
        other = read_info_file(self.folder)
        other.plugin["result"] = "done"
        write_info_file(self.folder, other)
        user.images.append({"path": "photo.png"})
        write_info_file(self.folder, user)
        self.assertEqual(user.plugin, {"result": "done"})
        destination = self.folder / "copy"
        write_info_file(destination, user)
        self.assertEqual(read_info_file(destination), read_info_file(self.folder))
