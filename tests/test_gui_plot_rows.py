"""Row layout, card drop routing and workspace behavior without GPU renderers."""
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from src.app.lodgazilla import LODGaZillaWindow, PlotWorkspace
from src.gui.plots.base import PLOT_MIME_TYPE, SERIES_MIME_TYPE, PlotBaseWidget
from src.gui.plots.gnss import GnssPlotWidget
from src.gui.plots.workspace_layout import PlotRow


class PlotRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.workspace = PlotWorkspace()
        self.workspace.resize(1400, 800)
        self.workspace.show()
        self.app.processEvents()
        self.cards = [self.workspace.add_image_plot(Path("."), Path(f"image-{index}.png"))
                      for index in range(4)]
        self.app.processEvents()

    def tearDown(self):
        self.workspace._drop_controller.finish()
        self.workspace.close_all_plots()
        self.workspace.close()
        self.workspace.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_group_resize_reorder_and_restore_standalone_height(self):
        a, b, c, d = self.cards
        self.workspace.resize_plot(a, 40)
        self.workspace.resize_plot(b, 100)
        self.assertTrue(self.workspace.move_plot(b, a, "right"))
        self.app.processEvents()
        row = self.workspace.splitter.row_for(a)
        self.assertIsInstance(row, PlotRow)
        self.assertEqual(row.plots(), [a, b])
        self.assertEqual(self.workspace.splitter.sizes(), [240, 200, 200])
        self.assertEqual(a.height(), b.height())
        self.assertEqual(self.workspace.plots(), [a, b, c, d])
        self.assertIs(self.workspace.active_plot, b)
        row.setSizes([800, 400])
        self.app.processEvents()
        self.assertGreater(a.width(), b.width())
        self.workspace.resize_plot(b, 70)
        self.assertEqual(self.workspace.splitter.sizes(), [310, 200, 200])
        self.assertTrue(self.workspace.move_plot(b, a, "left"))
        self.assertEqual(row.plots(), [b, a])
        self.workspace.reorder_plot(b, a, after=True)
        self.app.processEvents()
        self.assertEqual(self.workspace.plots(), [a, b, c, d])
        self.assertEqual(self.workspace.splitter.sizes(), [310, 300, 200, 200])
        self.assertIs(self.workspace.splitter.row_for(a), a)
        self.assertGreater(a.width(), 1300)

    def test_move_between_groups_and_close_collapses_rows(self):
        a, b, c, d = self.cards
        self.workspace.move_plot(b, a, "right")
        self.workspace.move_plot(d, c, "right")
        self.app.processEvents()
        self.assertTrue(self.workspace.move_plot(b, c, "left"))
        self.assertIs(self.workspace.splitter.row_for(a), a)
        self.assertEqual(self.workspace.splitter.row_for(c).plots(), [b, c, d])
        self.workspace.remove_plot(c)
        self.assertEqual(self.workspace.plots(), [a, b, d])
        d.close_button.click()
        self.assertEqual(self.workspace.plots(), [a, b])
        self.assertIs(self.workspace.splitter.row_for(b), b)
        self.workspace.close_all_plots()
        self.assertEqual(self.workspace.plots(), [])
        self.assertEqual(self.workspace.splitter.count(), 0)
        self.assertIsNone(self.workspace.active_plot)

    def test_time_plots_and_narrow_targets_reject_horizontal_drop(self):
        a, b, _, _ = self.cards
        time_plot = PlotBaseWidget(Path("none.h5"), "series")
        self.workspace._register_plot(time_plot, plot_type="line")
        self.assertFalse(self.workspace.move_plot(a, time_plot, "left"))
        self.assertFalse(self.workspace.move_plot(time_plot, a, "right"))
        self.workspace.resize(320, 800)
        self.app.processEvents()
        self.assertFalse(self.workspace.move_plot(b, a, "right"))
        self.assertTrue(self.workspace.move_plot(b, a, "above"))

    def test_row_height_respects_both_cards_minimum(self):
        a, b, _, _ = self.cards
        b.setMinimumHeight(300)
        self.assertTrue(self.workspace.move_plot(b, a, "right"))
        self.app.processEvents()
        self.assertEqual(self.workspace.splitter.sizes()[0], 300)
        self.workspace.resize_plot(a, -500)
        self.assertEqual(self.workspace.splitter.sizes()[0], 300)

    def test_drop_preview_selects_sides_and_vertical_rows_and_clears_on_cancel(self):
        a, b, _, _ = self.cards
        controller = self.workspace._drop_controller
        controller.start(b)
        for point, position in ((QPoint(5, a.height() // 2), "left"),
                                (QPoint(a.width() - 5, a.height() // 2), "right"),
                                (QPoint(a.width() // 2, 5), "above"),
                                (QPoint(a.width() // 2, a.height() - 5), "below")):
            destination = controller.update_preview(a.mapTo(self.workspace.viewport(), point))
            self.assertIs(destination[0], a)
            self.assertEqual(destination[1], position)
            self.assertTrue(controller.preview.isVisible())
        controller.finish()
        self.assertFalse(controller.preview.isVisible())
        self.assertFalse(controller._scroll_timer.isActive())
        self.assertEqual(self.workspace.plots(), self.cards)

    def test_card_drag_is_intercepted_over_child_content_and_series_drag_is_not(self):
        a, b, _, _ = self.cards
        controller = self.workspace._drop_controller
        mime = QMimeData()
        mime.setData(PLOT_MIME_TYPE, b"plot")
        controller.start(b)
        child = a.canvas
        point = QPoint(child.width() - 5, child.height() // 2)
        for event_class in (QDragEnterEvent, QDragMoveEvent, QDropEvent):
            class CardEvent(event_class):
                def source(self):
                    return b
            event = CardEvent(QPointF(point) if event_class is QDropEvent else point,
                              Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton,
                              Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(child, event)
            self.assertTrue(event.isAccepted())
        self.assertEqual(self.workspace.splitter.row_for(a).plots(), [a, b])
        self.assertFalse(controller.preview.isVisible())
        controller.start(b)
        series_mime = QMimeData()
        series_mime.setData(SERIES_MIME_TYPE, b"{}")
        event = mock.Mock()
        event.type.return_value = QEvent.Type.DragEnter
        event.source.return_value = b
        event.mimeData.return_value = series_mime
        self.assertFalse(controller.eventFilter(child, event))

    def test_own_row_edge_can_ungroup_and_empty_workspace_space_accepts_drop(self):
        a, b, _, _ = self.cards
        self.workspace.move_plot(b, a, "right")
        self.app.processEvents()
        controller = self.workspace._drop_controller
        controller.start(b)
        destination = controller.destination(b.mapTo(self.workspace.splitter, QPoint(b.width() // 2, 3)))
        self.assertEqual(destination[:2], (b, "above"))
        controller.finish()
        self.assertTrue(self.workspace.move_plot(b, b, "above"))
        self.assertIs(self.workspace.splitter.row_for(b), b)
        self.workspace.resize(1400, 1200)
        self.app.processEvents()
        controller.start(b)
        destination = controller.update_preview(QPoint(200, 1100))
        self.assertEqual(destination[:2], (self.cards[-1], "below"))

    def test_attachment_removal_finds_nested_cards(self):
        a, b, c, d = self.cards
        self.workspace.move_plot(b, a, "right")
        window = SimpleNamespace(plot_workspace=self.workspace, navigator=mock.Mock(),
                                 internal_logger=mock.Mock(), on_metadata_write_failed=mock.Mock())
        with mock.patch("src.app.lodgazilla.read_info_file", return_value=SimpleNamespace(images=[])), \
                mock.patch("src.app.lodgazilla.write_info_file"):
            LODGaZillaWindow.remove_image_link(window, b.folder, b.image_path)
        self.assertEqual(self.workspace.plots(), [a, c, d])
        self.assertIs(self.workspace.splitter.row_for(a), a)

    def test_navigator_order_and_activation_include_nested_cards(self):
        a, b, c, d = self.cards
        emitted = []
        self.workspace.plot_navigator_changed.connect(emitted.append)
        with mock.patch.object(self.workspace, "_plot_time_range", return_value=(1, 2)):
            self.workspace.move_plot(d, a, "left")
            self.assertEqual([entry.plot for entry in emitted[-1]], [d, a, b, c])
            self.workspace.activate_plot(a)
            self.assertIs(self.workspace.active_plot, a)
            self.workspace.reorder_plot(d, c, after=True)
            self.assertEqual([entry.plot for entry in emitted[-1]], [a, b, c, d])

    def test_linked_markers_and_theme_updates_reach_nested_maps(self):
        class MapCard(GnssPlotWidget):
            def __init__(self):
                # Exercise workspace routing without creating Chromium.
                PlotBaseWidget.__init__(self, Path("none.h5"), "latitude")
                for name in ("set_vertical_marker", "clear_vertical_marker", "show_marker_data",
                             "hide_marker_data", "apply_theme", "dispose", "apply_marker_style"):
                    setattr(self, name, mock.Mock())
                self.marker_values = mock.Mock(return_value=(None, None))

        first, second = MapCard(), MapCard()
        for card in (first, second):
            self.workspace._register_plot(card, plot_type="gnss")
        self.app.processEvents()
        self.assertTrue(self.workspace.move_plot(second, first, "right"))
        self.workspace.set_markers_linked(True)
        self.workspace.on_marker_placed(first, 123.0)
        second.set_vertical_marker.assert_called_once_with(123.0)
        self.workspace.set_marker_info_display("all")
        second.show_marker_data.assert_called_with(123.0, 0)
        self.workspace.apply_theme()
        second.apply_theme.assert_called_once_with()
        self.workspace.clear_marker_from_plot_navigator()
        second.clear_vertical_marker.assert_called_once_with()

    def test_drag_auto_scroll_updates_preview(self):
        self.workspace.resize(1400, 300)
        self.app.processEvents()
        controller = self.workspace._drop_controller
        controller.start(self.cards[0])
        viewport = self.workspace.viewport()
        point = QPoint(200, viewport.height() - 3)
        with mock.patch("src.gui.plots.workspace_layout.QCursor.pos", return_value=viewport.mapToGlobal(point)):
            controller._auto_scroll()
        self.assertGreater(self.workspace.verticalScrollBar().value(), 0)
        self.assertTrue(controller.preview.isVisible())


if __name__ == "__main__":
    unittest.main()
