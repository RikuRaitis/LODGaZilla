"""Audio conversion, audio attachments and the Navigator's Media group."""
import os
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import wave

import h5py
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTreeWidgetItemIterator

from src.app.lodgazilla import LODGaZillaWindow
from src.data.folder_info import FolderInfoData, read_info_file, write_info_file
from src.data.media import set_audio_offset
from src.gui.navigator.widget import (
    DataNavigatorWidget, ITEM_PATH_ROLE, ITEM_SERIES_ROLE, ITEM_TYPE_ROLE, ROW_TYPE_GROUP, ROW_TYPE_IMAGE, ROW_TYPE_SERIES,
    ROW_TYPE_VIDEO,
)
from src.gui.plots.series import load_plot_series
from src.gui.plots.video.widget import folder_start_unix_seconds
from src.jobs.import_coordinator import request_operation
from src.jobs.import_data import _safe_hdf5_name
from src.jobs.media_import import MediaItem, MediaJobRequest, convert_audio, run_media_process

LOG_START = 1_700_000_000.0
RATE = 8000


def write_wav(path: Path, seconds: float = 2.0) -> np.ndarray:
    count = int(RATE * seconds)
    samples = np.empty((count, 2), dtype=np.int16)
    samples[:, 0] = (np.arange(count) % 2000) - 1000
    samples[:, 1] = -samples[:, 0]
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(samples.tobytes())
    return samples


def write_measurement(folder: Path) -> None:
    with h5py.File(folder / "log.csv.h5", "w") as h5_file:
        h5_file.attrs["log_start_unix_seconds"] = LOG_START


def convert(source: Path, output: Path, offset: float = 0.0) -> list:
    events = queue.Queue()
    run_media_process(MediaJobRequest(source.parent, (MediaItem(str(source), str(output), LOG_START, offset),)), events)
    return [events.get_nowait() for _ in range(events.qsize())]


class AudioConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_wav_converts_to_counts_with_lod_and_offset(self):
        samples = write_wav(self.folder / "talk.wav")
        output = self.folder / "talk.wav.audio.h5"
        # Left by a conversion whose process was stopped; the next conversion removes it.
        (self.folder / ".talk.wav.audio.h5.stale1.partial").write_bytes(b"x")
        events = convert(self.folder / "talk.wav", output, offset=5.0)
        self.assertEqual(events[-1][:3], ("finished", 1, 0))
        with h5py.File(output, "r") as h5_file:
            left = h5_file[f"series/Media/{_safe_hdf5_name('Audio left')}"]
            np.testing.assert_array_equal(left["value"][:], samples[:, 0])
            self.assertEqual(left.attrs["unit"], "counts")
            self.assertIn("lod", left)
            self.assertEqual(h5_file.attrs["log_start_unix_seconds"], LOG_START + 5.0)
        self.assertFalse(list(self.folder.glob("*.partial")))
        data = load_plot_series(output, "Media/Audio right")
        self.assertAlmostEqual(float(data.x[0]), LOG_START + 5.0, places=3)

    def test_compression_none_writes_uncompressed_datasets(self):
        samples = write_wav(self.folder / "talk.wav", seconds=0.2)
        output = self.folder / "talk.wav.audio.h5"
        item = MediaItem(str(self.folder / "talk.wav"), str(output), LOG_START, 0.0)
        convert_audio(item, {"compression": "none", "chunk_rows": 1024}, lambda _: None, lambda: False)
        with h5py.File(output, "r") as h5_file:
            left = h5_file[f"series/Media/{_safe_hdf5_name('Audio left')}/value"]
            self.assertIsNone(left.compression)
            np.testing.assert_array_equal(left[:], samples[:, 0])

    def test_failed_conversion_reports_and_leaves_no_file(self):
        (self.folder / "broken.wav").write_bytes(b"not audio")
        events = convert(self.folder / "broken.wav", self.folder / "broken.wav.audio.h5")
        self.assertEqual(events[-1][:3], ("finished", 0, 1))
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ["broken.wav"])

    def test_offset_change_moves_log_start_only(self):
        write_wav(self.folder / "talk.wav", seconds=0.5)
        output = self.folder / "talk.wav.audio.h5"
        convert(self.folder / "talk.wav", output)
        self.assertTrue(set_audio_offset(output, -1.5))
        with h5py.File(output, "r") as h5_file:
            self.assertEqual(h5_file.attrs["log_start_unix_seconds"], LOG_START - 1.5)
            self.assertEqual(h5_file[f"series/Media/{_safe_hdf5_name('Audio left')}/time_s"][0], 0.0)

    def test_busy_audio_is_retried_later_without_blocking(self):
        from PySide6.QtCore import QObject
        from src.app import lodgazilla
        from tests.qt_wait import qt_wait_until

        owner, results = QObject(), []
        answers = iter([False, False, True])
        with mock.patch.object(lodgazilla, "set_audio_offset", side_effect=lambda *_: next(answers)) as attempt:
            lodgazilla.retry_audio_offset(owner, Path("a.audio.h5"), 1.0, results.append, delay_ms=1)
            # The first attempt failed and returned at once; later ones run from the event loop.
            self.assertEqual((attempt.call_count, results), (1, []))
            self.assertTrue(qt_wait_until(lambda: results, 10_000))
        self.assertEqual((attempt.call_count, results), (3, [True]))
        with mock.patch.object(lodgazilla, "set_audio_offset", return_value=False):
            results.clear()
            lodgazilla.retry_audio_offset(owner, Path("a.audio.h5"), 1.0, results.append, attempts=2, delay_ms=1)
            self.assertTrue(qt_wait_until(lambda: results, 10_000))
        self.assertEqual(results, [False])

    def test_folder_log_start_ignores_audio_files(self):
        write_wav(self.folder / "a.wav", seconds=0.2)
        convert(self.folder / "a.wav", self.folder / "a.wav.audio.h5", offset=30.0)
        write_measurement(self.folder)
        self.assertEqual(folder_start_unix_seconds(self.folder), LOG_START)

    def test_audio_jobs_queue_as_their_own_operation(self):
        self.assertEqual(request_operation(MediaJobRequest(self.folder, ())), "Audio conversion")


class AudioAttachmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.folder = root / "Data" / "Session"
        self.folder.mkdir(parents=True)
        write_measurement(self.folder)
        self.navigator = DataNavigatorWidget(self.folder.parent, settings_path=root / "settings.json")
        self.addCleanup(self.navigator.close)
        self.window = SimpleNamespace(
            navigator=self.navigator, internal_logger=mock.Mock(), on_metadata_write_failed=mock.Mock(),
            start_import=mock.Mock(), _relative_link=LODGaZillaWindow._relative_link,
        )
        for name in ("_audio_link", "_queue_audio", "_video_audio_links"):
            setattr(self.window, name, getattr(LODGaZillaWindow, name).__get__(self.window))

    def tree_rows(self):
        # Folder contents are made when a folder is opened; open them all, as a user would.
        self.navigator.ensure_all_folder_contents(expand=True)
        iterator = QTreeWidgetItemIterator(self.navigator.tree)
        rows = []
        while iterator.value():
            rows.append(iterator.value())
            iterator += 1
        return rows

    def test_audio_file_is_linked_and_queued_for_conversion(self):
        write_wav(self.folder / "talk.wav", seconds=0.2)
        LODGaZillaWindow.attach_audio_files(self.window, self.folder, [self.folder / "talk.wav"])
        self.assertEqual(read_info_file(self.folder).audio, [
            {"path": "talk.wav.audio.h5", "source": "talk.wav", "offset_seconds": "0"},
        ])
        request = self.window.start_import.call_args.args[0]
        self.assertIsInstance(request, MediaJobRequest)
        self.assertEqual(request.items, (MediaItem(
            str((self.folder / "talk.wav").resolve()), str((self.folder / "talk.wav.audio.h5").resolve()), LOG_START, 0.0),))
        # Attaching the same source again neither duplicates the link nor queues it again.
        LODGaZillaWindow.attach_audio_files(self.window, self.folder, [self.folder / "talk.wav"])
        self.assertEqual(len(read_info_file(self.folder).audio), 1)
        self.assertEqual(self.window.start_import.call_count, 1)

    def test_h5_audio_is_linked_without_conversion(self):
        LODGaZillaWindow.attach_audio_files(self.window, self.folder, [self.folder.parent / "old.audio.h5"])
        self.assertEqual(read_info_file(self.folder).audio, [{"path": "../old.audio.h5", "offset_seconds": "0"}])
        self.window.start_import.assert_not_called()

    def test_video_without_soundtrack_is_not_linked(self):
        (self.folder / "clip.mp4").write_bytes(b"not a video")
        LODGaZillaWindow.extract_video_audio(self.window, self.folder, [self.folder / "clip.mp4"])
        self.assertEqual(read_info_file(self.folder).audio, [])
        self.window.start_import.assert_not_called()

    def test_media_group_holds_videos_images_and_linked_audio(self):
        outside = self.folder.parent.parent / "Recordings"
        outside.mkdir()
        write_wav(outside / "talk.wav", seconds=0.2)
        convert(outside / "talk.wav", outside / "talk.wav.audio.h5")
        # An audio file whose link was removed stays on disk but leaves the tree.
        write_wav(self.folder / "old.wav", seconds=0.2)
        convert(self.folder / "old.wav", self.folder / "old.wav.audio.h5")
        (self.folder / "clip.mp4").write_bytes(b"video")
        (self.folder / "photo.png").write_bytes(b"image")
        info = read_info_file(self.folder)
        info.videos = [{"path": "clip.mp4", "offset_seconds": "0"}]
        info.images = [{"path": "photo.png"}]
        info.audio = [{"path": "../../Recordings/talk.wav.audio.h5", "offset_seconds": "0"}]
        write_info_file(self.folder, info)
        self.navigator.refresh_tree(rescan=True)
        self.app.processEvents()

        media = [row for row in self.tree_rows()
                 if row.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_GROUP and row.text(0) == "Media"]
        self.assertEqual(len(media), 1)
        children = [media[0].child(index) for index in range(media[0].childCount())]
        self.assertEqual(sorted(child.data(0, ITEM_TYPE_ROLE) for child in children),
                         sorted([ROW_TYPE_VIDEO, ROW_TYPE_IMAGE, ROW_TYPE_SERIES, ROW_TYPE_SERIES]))
        audio = [child for child in children if child.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_SERIES]
        self.assertEqual(sorted(child.data(0, ITEM_SERIES_ROLE) for child in audio),
                         ["Media/Audio left", "Media/Audio right"])
        self.assertEqual({Path(str(child.data(0, ITEM_PATH_ROLE))) for child in audio},
                         {(outside / "talk.wav.audio.h5").resolve()})
        series_paths = [str(row.data(0, ITEM_PATH_ROLE)) for row in self.tree_rows()
                        if row.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_SERIES]
        self.assertFalse(any("old.wav" in path for path in series_paths))

    def test_scanned_audio_beside_parent_video_joins_linking_folder(self):
        # Video soundtrack audio is written beside a video in the scanned parent folder.
        write_wav(self.folder.parent / "clip.wav", seconds=0.2)
        convert(self.folder.parent / "clip.wav", self.folder.parent / "clip.insv.audio.h5")
        info = read_info_file(self.folder)
        info.audio = [{"path": "../clip.insv.audio.h5", "offset_seconds": "0"}]
        write_info_file(self.folder, info)
        self.navigator.refresh_tree(rescan=True)
        self.app.processEvents()

        audio = [row for row in self.tree_rows() if row.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_SERIES
                 and str(row.data(0, ITEM_PATH_ROLE)).endswith("clip.insv.audio.h5")]
        self.assertEqual(len(audio), 2)
        for row in audio:
            self.assertEqual(row.parent().text(0), "Media")
            self.assertEqual(row.parent().parent().data(0, ITEM_PATH_ROLE), str(self.folder))
        root = self.navigator.tree.invisibleRootItem()
        self.assertEqual([root.child(index).text(0) for index in range(root.childCount())], ["Session"])

if __name__ == "__main__":
    unittest.main()
