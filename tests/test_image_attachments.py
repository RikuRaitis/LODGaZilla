"""Image attachment workflows and actual Qt image-card rendering."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, Qt, QThreadPool
from PySide6.QtGui import QColor, QImage
from tests.qt_wait import qt_wait
from PySide6.QtWidgets import QApplication, QMenu, QTreeWidgetItemIterator

from src.app.lodgazilla import LODGaZillaWindow, PlotWorkspace
from src.data.folder_info import FolderInfoData, attachment_path, read_info_file, write_info_file
from src.gui.navigator.widget import (
    DataNavigatorWidget, IMAGE_MIME_TYPE, ITEM_IMAGE_PATH_ROLE, ITEM_TYPE_ROLE, ROW_TYPE_IMAGE,
)
from src.gui.plots.image import ImagePlotWidget


class ImageAttachmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.folder = root / "Data" / "Session"
        self.folder.mkdir(parents=True)
        self.first = self.folder / "photo.png"
        self.second = root / "photo.png"
        image = QImage(200, 100, QImage.Format.Format_ARGB32)
        image.fill(QColor("red"))
        for path in (self.first, self.second):
            self.assertTrue(image.save(str(path)))
        write_info_file(self.folder, FolderInfoData(
            editable={"comment": "keep"}, plugin={"PluginField": "keep"},
            videos=[{"path": "clip.mp4", "offset_seconds": "2.5"}],
        ))
        self.navigator = DataNavigatorWidget(self.folder.parent, settings_path=root / "settings.json")
        self.workspace = PlotWorkspace(settings_path=root / "settings.json")
        self.navigator.image_activated.connect(self.workspace.add_image_plot)
        self.window = SimpleNamespace(
            navigator=self.navigator, plot_workspace=self.workspace,
            internal_logger=mock.Mock(), on_metadata_write_failed=mock.Mock(),
        )
        self.addCleanup(self.close_widgets)

    def wait_decoded(self, card):
        """Image cards decode in the thread pool."""
        for _ in range(500):
            if not card.canvas.loading:
                return
            qt_wait(10)
        self.fail("Image did not finish decoding")

    def close_widgets(self):
        QThreadPool.globalInstance().waitForDone(3000)
        qt_wait(50)
        self.workspace.close_all_plots()
        self.workspace.close()
        self.navigator.close()
        self.app.processEvents()
        self.workspace.deleteLater()
        self.navigator.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def attach(self, paths):
        with mock.patch("src.app.lodgazilla.QFileDialog.getOpenFileNames", return_value=([str(p) for p in paths], "")):
            LODGaZillaWindow.attach_images_to_folder(self.window, self.folder)

    def image_rows(self):
        # Folder contents are made when a folder is opened; open them all, as a user would.
        self.navigator.ensure_all_folder_contents(expand=True)
        rows = []
        iterator = QTreeWidgetItemIterator(self.navigator.tree)
        while iterator.value():
            item = iterator.value()
            if item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_IMAGE:
                rows.append(item)
            iterator += 1
        return rows

    def test_attach_multiple_relative_paths_and_skip_duplicates(self):
        self.attach([self.first])
        self.attach([self.first, self.second, self.second])
        info = read_info_file(self.folder)
        self.assertEqual(info.images, [{"path": "photo.png"}, {"path": "../../photo.png"}])
        self.assertEqual(info.videos, [{"path": "clip.mp4", "offset_seconds": "2.5"}])
        self.assertEqual(info.editable, {"comment": "keep"})
        self.assertEqual(info.plugin, {"PluginField": "keep"})
        rows = self.image_rows()
        self.assertEqual([row.text(0) for row in rows], ["photo.png", "photo.png"])
        self.assertEqual({row.data(0, ITEM_IMAGE_PATH_ROLE) for row in rows}, {str(self.first), str(self.second)})
        self.navigator.invalidate_source_snapshot()
        self.navigator.refresh_tree()
        self.assertEqual(len(self.image_rows()), 2)

    def test_media_group_menu_attaches_to_its_folder(self):
        self.attach([self.first])
        media = self.image_rows()[0].parent()
        self.assertEqual(media.text(0), "Media")
        requested = []
        self.navigator.media_group_context_menu_requested.connect(
            lambda folder, entries, _position: requested.append((folder, entries)))
        self.navigator.tree.scrollToItem(media)
        self.navigator.on_context_menu_requested(self.navigator.tree.visualItemRect(media).center())
        self.assertEqual(requested, [(self.folder.resolve(), [])])
        # The menu offers the folder's Attach submenu, which attaches to that folder.
        window = SimpleNamespace(plot_workspace=self.workspace, navigator=self.navigator,
                                 attach_video_to_folder=mock.Mock(), attach_images_to_folder=mock.Mock(),
                                 attach_audio_to_folder=mock.Mock())
        window.add_attach_menu = lambda menu, folder: LODGaZillaWindow.add_attach_menu(window, menu, folder)
        shown = {}

        class RecordingMenu(QMenu):
            def __init__(self, *args):
                # The stand-in window is not a widget; keep a title and a menu parent.
                super().__init__(*(arg for arg in args if isinstance(arg, (str, QMenu))))

            def exec(self, *args):  # noqa: A003 - records instead of opening the menu
                attach = self.actions()[0].menu()
                shown["attach"] = [action.text() for action in attach.actions()]
                attach.actions()[1].trigger()

        with mock.patch("src.app.lodgazilla.QMenu", RecordingMenu):
            LODGaZillaWindow.show_media_group_context_menu(window, self.folder, [], QPoint())
        self.assertEqual(shown["attach"], ["Video…", "Images…", "Audio…", "3D model…"])
        window.attach_images_to_folder.assert_called_once_with(self.folder)

    def test_cancel_and_failed_save_leave_links_unchanged(self):
        self.attach([])
        self.assertEqual(read_info_file(self.folder).images, [])
        with mock.patch("src.app.lodgazilla.write_info_file", side_effect=OSError("read only")):
            self.attach([self.first])
        self.assertEqual(read_info_file(self.folder).images, [])
        self.assertEqual(self.image_rows(), [])
        self.window.on_metadata_write_failed.assert_called_once()

    def test_activation_opens_real_card_and_rendering_preserves_aspect_ratio(self):
        self.attach([self.first])
        self.navigator.on_item_activated(self.image_rows()[0], 0)
        card = self.workspace.splitter.widget(0)
        self.assertIsInstance(card, ImagePlotWidget)
        self.assertEqual(card.title_label.text(), "photo.png")
        self.wait_decoded(card)
        self.assertFalse(card.canvas.image.isNull())
        self.assertIsNone(self.workspace._plot_time_range(card))
        self.workspace.resize(700, 450)
        self.workspace.show()
        self.app.processEvents()
        for width, height in ((600, 300), (200, 400)):
            card.canvas.resize(width, height)
            rect = card.canvas.image_rect()
            self.assertAlmostEqual(rect.width() / rect.height(), 2.0, places=2)
            rendered = card.canvas.grab().toImage()
            self.assertEqual(rendered.pixelColor(rendered.width() // 2, rendered.height() // 2), QColor("red"))
        other = self.workspace.add_image_plot(self.folder, self.second)
        self.workspace.reorder_plot(other, card, after=False)
        self.assertIs(self.workspace.splitter.widget(0), other)
        card.close_button.click()
        self.assertEqual(self.workspace.splitter.count(), 1)

    def test_dragged_image_row_opens_card(self):
        self.attach([self.first])
        self.navigator.tree.setCurrentItem(self.image_rows()[0])
        with mock.patch("src.gui.navigator.widget.QDrag") as drag:
            self.navigator.tree.startDrag(Qt.DropAction.CopyAction)
            mime = drag.return_value.setMimeData.call_args.args[0]
            self.assertTrue(mime.hasFormat(IMAGE_MIME_TYPE))
            event = mock.Mock()
            event.mimeData.return_value = mime
            self.workspace.dragEnterEvent(event)
            self.workspace.dropEvent(event)
        event.ignore.assert_not_called()
        self.assertEqual(self.workspace.splitter.widget(0).image_path, self.first)

    def test_remove_link_only_closes_matching_folder_cards_and_preserves_file(self):
        self.attach([self.first, self.second])
        self.workspace.add_image_plot(self.folder, self.first)
        self.workspace.add_image_plot(self.folder, self.first)
        other_image = self.workspace.add_image_plot(self.folder, self.second)
        other_folder = self.workspace.add_image_plot(self.folder.parent, self.first)
        before = self.first.read_bytes()
        LODGaZillaWindow.remove_image_link(self.window, self.folder, self.first)
        self.assertEqual(self.first.read_bytes(), before)
        self.assertEqual(read_info_file(self.folder).images, [{"path": "../../photo.png"}])
        self.assertEqual(self.workspace.splitter.count(), 2)
        self.assertIs(self.workspace.splitter.widget(0), other_image)
        self.assertIs(self.workspace.splitter.widget(1), other_folder)
        LODGaZillaWindow.remove_image_link(self.window, self.folder, self.second)
        self.assertEqual(read_info_file(self.folder).images, [])
        self.assertEqual(self.image_rows(), [])

    def test_failed_removal_leaves_link_and_card(self):
        self.attach([self.first])
        card = self.workspace.add_image_plot(self.folder, self.first)
        with mock.patch("src.app.lodgazilla.write_info_file", side_effect=OSError("read only")):
            LODGaZillaWindow.remove_image_link(self.window, self.folder, self.first)
        self.assertIs(self.workspace.splitter.widget(0), card)
        self.assertEqual(len(read_info_file(self.folder).images), 1)

    def test_context_menu_identifies_clicked_image_and_offers_remove_link(self):
        self.attach([self.first, self.second])
        self.navigator.resize(1000, 700)
        self.navigator.show()
        self.navigator.tree.expandAll()
        self.app.processEvents()
        row = next(row for row in self.image_rows() if row.data(0, ITEM_IMAGE_PATH_ROLE) == str(self.second))
        self.navigator.tree.scrollToItem(row)
        self.app.processEvents()
        received = []
        self.navigator.image_context_menu_requested.connect(lambda folder, image, _: received.append((folder, image)))
        position = self.navigator.tree.visualItemRect(row).center()
        self.navigator.on_context_menu_requested(position)
        self.assertEqual(received, [(self.folder, self.second)])
        self.window.remove_image_link = mock.Mock()
        menu = QMenu()
        with mock.patch("src.app.lodgazilla.QMenu", return_value=menu), mock.patch.object(menu, "exec"):
            LODGaZillaWindow.show_image_context_menu(self.window, self.folder, self.second, position)
            next(action for action in menu.actions() if action.text() == "Remove link").trigger()
        self.window.remove_image_link.assert_called_once_with(self.folder, self.second)
        menu.deleteLater()

    def test_missing_and_corrupt_images_show_errors(self):
        corrupt = self.folder / "corrupt.png"
        corrupt.write_bytes(b"invalid image")
        for path in (self.folder / "missing.png", corrupt):
            with self.subTest(path=path):
                card = self.workspace.add_image_plot(self.folder, path)
                self.wait_decoded(card)
                self.assertTrue(card.canvas.image.isNull())
                self.assertIn("Could not load image", card.canvas.error)
                card.canvas.grab()

    def test_relative_link_resolves_after_folder_relocation(self):
        self.attach([self.first])
        moved = self.folder.parent / "Relocated"
        info = read_info_file(self.folder)
        write_info_file(moved, info)
        (moved / self.first.name).write_bytes(self.first.read_bytes())
        resolved = attachment_path(moved, read_info_file(moved).images[0])
        card = self.workspace.add_image_plot(moved, resolved)
        self.wait_decoded(card)
        self.assertFalse(card.canvas.image.isNull())


if __name__ == "__main__":
    unittest.main()
