"""Attachment UI and persistence regressions without starting video decoders."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QTreeWidgetItemIterator

from src.app.lodgazilla import LODGaZillaWindow, PlotWorkspace
from src.data.folder_info import FolderInfoData, read_info_file, write_info_file
from src.gui.navigator.widget import (
    DataNavigatorWidget, ITEM_TYPE_ROLE, ITEM_VIDEO_PATH_ROLE,
    ITEM_VIDEO_OFFSET_ROLE, ROW_TYPE_VIDEO,
)
from src.gui.plots.video import VideoPlotWidget
from src.gui.plots.base import PlotBaseWidget


class HeadlessVideoCard(PlotBaseWidget):
    """Use the real sync logic with a plain Qt card, without native rendering."""
    marker_timestamp_changed = Signal(float)
    duration_changed = Signal()
    sync_data_requested = Signal(object)
    sync_video_requested = Signal(object)
    offset_edited = Signal(object)
    timeline_alignment_requested = Signal()
    has_sync_anchor = VideoPlotWidget.has_sync_anchor
    has_sync_video_position = VideoPlotWidget.has_sync_video_position
    capture_sync_anchor = VideoPlotWidget.capture_sync_anchor
    capture_sync_video_position = VideoPlotWidget.capture_sync_video_position
    apply_sync_anchor = VideoPlotWidget.apply_sync_anchor
    set_offset = VideoPlotWidget.set_offset
    _show_sync_state = VideoPlotWidget._show_sync_state
    set_sync_marker = VideoPlotWidget.set_sync_marker
    show_sync_message = VideoPlotWidget.show_sync_message
    _status_text = VideoPlotWidget._status_text
    set_timeline_markers = VideoPlotWidget.set_timeline_markers
    _update_timeline_marks = VideoPlotWidget._update_timeline_marks
    _update_playback_status = VideoPlotWidget._update_playback_status
    time_offset = VideoPlotWidget.time_offset

    def __init__(self, folder, video_path, offset_seconds=0.0):
        super().__init__(folder, Path(video_path).name)
        self.folder, self.video_path = Path(folder), Path(video_path)
        self.offset_seconds = offset_seconds
        self.data_start = self._sync_anchor_timestamp = self._sync_video_position = None
        self.position = self.duration = 0.0
        self._closed = False
        self.sync_status_label = mock.Mock()
        self.sync_data_button = mock.Mock()
        self.sync_video_button = mock.Mock()
        self.sync_button = mock.Mock()
        self._marker_link_enabled = False
        self.status_label = mock.Mock()
        self.timeline = mock.Mock()

    def set_marker_link_enabled(self, enabled):
        pass

    def set_marker_timestamp(self, timestamp):
        pass

    def set_timeline_span(self, left, right=None):
        pass

    def shutdown(self):
        self._closed = True


class VideoAttachmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.folder = root / "Data" / "Session"
        self.folder.mkdir(parents=True)
        self.first = self.folder / "camera1" / "clip.mp4"
        self.second = self.folder / "camera2" / "clip.mp4"
        for path in (self.first, self.second):
            path.parent.mkdir()
            path.write_bytes(b"keep video file")
        write_info_file(self.folder, FolderInfoData(
            editable={"comment": "keep"}, plugin={"PluginField": "keep"},
            videos=[{"path": "camera1/clip.mp4", "offset_seconds": "-2.5"}],
        ))
        self.navigator = DataNavigatorWidget(self.folder.parent, settings_path=root / "settings.json")
        self.workspace = PlotWorkspace(settings_path=root / "settings.json")
        self.workspace.video_offset_saved.connect(lambda _: self.navigator.refresh_tree())
        self.window = SimpleNamespace(
            navigator=self.navigator, plot_workspace=self.workspace,
            internal_logger=mock.Mock(), on_metadata_write_failed=mock.Mock(),
            extract_video_audio=mock.Mock(),
        )
        self.window._video_audio_links = lambda *args: LODGaZillaWindow._video_audio_links(self.window, *args)
        patcher = mock.patch("src.app.lodgazilla.VideoPlotWidget", HeadlessVideoCard)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.close_widgets)

    def close_widgets(self):
        self.workspace.close_all_plots()
        self.workspace.close()
        self.navigator.close()
        self.app.processEvents()

    def attach(self, paths):
        with mock.patch("src.app.lodgazilla.QFileDialog.getOpenFileNames", return_value=([str(p) for p in paths], "")):
            LODGaZillaWindow.attach_video_to_folder(self.window, self.folder)

    def video_rows(self):
        rows = []
        iterator = QTreeWidgetItemIterator(self.navigator.tree)
        while iterator.value():
            item = iterator.value()
            if item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_VIDEO:
                rows.append(item)
            iterator += 1
        return rows

    def test_attach_appends_files_and_preserves_existing_offset(self):
        self.attach([self.first, self.second, self.second])
        info = read_info_file(self.folder)
        self.assertEqual(info.videos, [
            {"path": "camera1/clip.mp4", "offset_seconds": "-2.5"},
            {"path": "camera2/clip.mp4", "offset_seconds": "0"},
        ])
        self.assertEqual(info.editable, {"comment": "keep"})
        self.assertEqual(info.plugin, {"PluginField": "keep"})
        rows = self.video_rows()
        self.assertEqual([row.text(0) for row in rows], ["clip.mp4", "clip.mp4"])
        self.assertEqual({row.data(0, ITEM_VIDEO_PATH_ROLE) for row in rows}, {str(self.first), str(self.second)})

    def test_lrv_attachment_activation_sync_and_removal(self):
        path = self.folder / "preview.LRV"
        path.write_bytes(b"keep preview file")
        with mock.patch("src.app.lodgazilla.QFileDialog.getOpenFileNames", return_value=([str(path)], "")) as dialog:
            LODGaZillaWindow.attach_video_to_folder(self.window, self.folder)
            self.assertIn("*.lrv", dialog.call_args.args[3])
        row = next(row for row in self.video_rows() if row.data(0, ITEM_VIDEO_PATH_ROLE) == str(path))
        self.assertEqual(row.text(0), "preview.LRV")
        self.navigator.video_activated.connect(self.workspace.add_video_plot)
        self.navigator.on_item_activated(row, 0)
        card = self.workspace.splitter.widget(0)
        self.assertEqual(card.video_path, path)
        card.data_start, card.position = 1000.0, 3.0
        self.workspace._last_data_marker_timestamp = 1010.0
        self.workspace.request_video_sync(card)
        self.workspace.request_video_sync(card)
        info = read_info_file(self.folder)
        self.assertEqual(info.videos[1], {"path": "preview.LRV", "offset_seconds": "7.000000"})
        self.assertEqual(info.videos[0]["offset_seconds"], "-2.5")
        LODGaZillaWindow.remove_video_link(self.window, self.folder, path)
        self.assertEqual(len(read_info_file(self.folder).videos), 1)
        self.assertEqual(path.read_bytes(), b"keep preview file")
        self.assertTrue(card._closed)

    def test_sync_buttons_work_in_either_order_and_show_the_sync_marker_until_done(self):
        from src.gui.plots.markers import FIRST_MARKER
        line = self.workspace.add_line_plot(Path("data.h5"), "acc_x")
        card = self.workspace.add_video_plot(self.folder, self.first)
        card.data_start, card.position = 1000.0, 2.0
        navigator = []
        self.workspace.sync_marker_changed.connect(navigator.append)
        canvas = line.series_plot.plot_widget
        # Video marker first: nothing is calculated until the data marker is set too.
        self.workspace.set_video_sync_video_marker(card)
        self.assertTrue(card.has_sync_video_position)
        self.assertEqual(card.offset_seconds, 0.0)
        # Unlinked, the data marker is the active card's red marker.
        self.workspace.set_markers_linked(False)
        line.set_vertical_marker(1012.0, FIRST_MARKER)
        self.workspace.activate_plot(line)
        card.position = 5.0
        self.workspace.set_video_sync_data_marker(card)
        self.assertEqual(card.offset_seconds, 10.0)
        self.assertFalse(card.has_sync_anchor or card.has_sync_video_position)
        self.assertEqual(float(read_info_file(self.folder).videos[0]["offset_seconds"]), 10.0)
        self.assertEqual(navigator, [1012.0, None])
        self.assertIsNone(canvas.sync_marker_line)
        # Data marker first: the stored time is shown purple until the video marker is set.
        self.workspace.set_markers_linked(True)
        self.workspace._last_data_marker_timestamp = 1020.0
        self.workspace.set_video_sync_data_marker(card)
        self.assertEqual(canvas.sync_marker_line.value(), 1020.0)
        self.assertEqual(navigator[-1], 1020.0)
        # The red marker can move meanwhile; the stored sync point stays.
        line.set_vertical_marker(1030.0, FIRST_MARKER)
        card.position = 4.0
        self.workspace.set_video_sync_video_marker(card)
        self.assertEqual(card.offset_seconds, 16.0)
        self.assertIsNone(canvas.sync_marker_line)

    def test_cancel_does_not_change_links(self):
        before = read_info_file(self.folder)
        self.attach([])
        self.assertEqual(read_info_file(self.folder), before)

    def test_sync_updates_only_matching_video_and_refreshes_activation_offset(self):
        self.attach([self.second])
        first = self.workspace.add_video_plot(self.folder, self.first, -2.5)
        second = self.workspace.add_video_plot(self.folder, self.second)
        second.data_start = 1000.0
        second.position = 3.0
        self.workspace._last_data_marker_timestamp = 1010.0
        self.workspace.request_video_sync(second)
        self.workspace.request_video_sync(second)
        self.assertEqual(first.offset_seconds, -2.5)
        self.assertEqual(second.offset_seconds, 7.0)
        self.assertEqual([float(v["offset_seconds"]) for v in read_info_file(self.folder).videos], [-2.5, 7.0])
        row = next(row for row in self.video_rows() if row.data(0, ITEM_VIDEO_PATH_ROLE) == str(self.second))
        self.assertEqual(row.data(0, ITEM_VIDEO_OFFSET_ROLE), 7.0)
        activated = []
        self.navigator.video_activated.connect(lambda *args: activated.append(args))
        self.navigator.on_item_activated(row, 0)
        self.assertEqual(activated, [(self.folder, self.second, 7.0)])

    def test_remove_link_preserves_file_other_links_and_other_cards(self):
        self.attach([self.second])
        first = self.workspace.add_video_plot(self.folder, self.first, -2.5)
        second = self.workspace.add_video_plot(self.folder, self.second)
        LODGaZillaWindow.remove_video_link(self.window, self.folder, self.first)
        self.assertTrue(first._closed)
        self.assertIs(self.workspace.splitter.widget(0), second)
        self.assertEqual(self.first.read_bytes(), b"keep video file")
        self.assertEqual(read_info_file(self.folder).videos, [{"path": "camera2/clip.mp4", "offset_seconds": "0"}])
        self.assertEqual(len(self.video_rows()), 1)
        LODGaZillaWindow.remove_video_link(self.window, self.folder, self.second)
        self.assertEqual(read_info_file(self.folder).videos, [])
        self.assertEqual(self.video_rows(), [])
        self.assertTrue(self.second.exists())

    def test_failed_removal_keeps_card_and_link(self):
        card = self.workspace.add_video_plot(self.folder, self.first, -2.5)
        with mock.patch("src.app.lodgazilla.write_info_file", side_effect=OSError("read only")):
            LODGaZillaWindow.remove_video_link(self.window, self.folder, self.first)
        self.assertFalse(card._closed)
        self.assertEqual(len(read_info_file(self.folder).videos), 1)
        self.window.on_metadata_write_failed.assert_called_once()

    def test_video_context_menu_identifies_clicked_file(self):
        self.attach([self.second])
        self.navigator.resize(1000, 700)
        self.navigator.show()
        self.navigator.tree.expandAll()
        self.app.processEvents()
        row = next(row for row in self.video_rows() if row.data(0, ITEM_VIDEO_PATH_ROLE) == str(self.second))
        self.navigator.tree.scrollToItem(row)
        self.app.processEvents()
        received = []
        self.navigator.video_context_menu_requested.connect(lambda folder, video, _: received.append((folder, video)))
        self.navigator.on_context_menu_requested(self.navigator.tree.visualItemRect(row).center())
        self.assertEqual(received, [(self.folder, self.second)])


if __name__ == "__main__":
    unittest.main()
