"""Data drag feedback, legend ordering and creation at workspace gaps."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import h5py
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QRect, Qt, QThreadPool, qInstallMessageHandler
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDragLeaveEvent, QDropEvent
from tests.qt_wait import qt_wait
from PySide6.QtWidgets import QApplication

from src.app.lodgazilla import PlotWorkspace
from src.app.settings import load_lodgazilla_settings, sanitize_lodgazilla_settings
from src.gui.plots.base import SERIES_MIME_TYPE, PlotBaseWidget
from src.gui.plots.data_drop import IMAGE_MIME_TYPE, VIDEO_MIME_TYPE, exec_data_drag
from src.gui.plots.event_state import EventStatePlotWidget
from src.gui.plots.gnss import GnssPlotWidget
from src.gui.plots.legend import LegendDragSource, _LegendLabel
from src.gui.plots.legend_groups import SeriesGroup
from src.data.plot_policy import PlotSource, SELECTION_MIME_TYPE
from src.data.xyz import AxisSource
from src.gui.settings_widget import SettingsEditorWidget
from src.jobs.plot_drop import series_card_types


class DataDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "data.h5"
        self.settings = Path(self.temp.name) / "settings.json"
        with h5py.File(self.path, "w") as cache:
            for name, unit in (("a", "V"), ("b", "V"), ("c", "V"), ("state", "bool"),
                               ("GNSS/latitude", "deg"), ("GNSS/longitude", "deg")):
                group = cache.create_group("series/" + name)
                group.attrs.update(time_dataset="time_s", time_unit="s", unit=unit, navigator_name=name)
                group.create_dataset("time_s", data=[0., 1., 2.])
                group.create_dataset("value", data=[0., 1., 0.])
        self.workspace = PlotWorkspace(settings_path=self.settings)
        self.workspace.resize(1000, 800)
        self.workspace.show()
        self.app.processEvents()
        self.controller = self.workspace._data_drop_controller
        self.controller.start()

    def tearDown(self):
        self.controller.stop()
        self.workspace.close_all_plots()
        QThreadPool.globalInstance().waitForDone(3000)
        # Let queued scene/axis updates finish before deleting native Qt views.
        qt_wait(100)
        self.workspace.close()
        self.app.processEvents()
        self.workspace.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def test_native_drag_lifetime_cleans_up_on_cancel(self):
        mime = self.mime(kind=IMAGE_MIME_TYPE)
        def drag_events(_actions):
            point = QPoint(100, 100)
            self.send("enter", point, mime)
            self.assertTrue(self.controller.line.isVisible())
            return Qt.DropAction.IgnoreAction
        self.controller.stop()
        drag = mock.Mock()
        drag.exec.side_effect = drag_events
        self.assertEqual(exec_data_drag(drag, Qt.DropAction.CopyAction), Qt.DropAction.IgnoreAction)
        self.assertFalse(self.controller.line.isVisible())
        self.assertIsNone(self.controller.payload)

    def test_metadata_results_follow_latest_drag_and_do_not_reopen_after_cancel(self):
        self.controller._read_payload(self.mime("a"))
        self.prime(self.mime("state"))
        self.assertNotIn("frequency", self.controller.options)
        self.controller._read_payload(self.mime("a"))
        self.controller.update_preview(QPoint(100, 100))
        self.controller.finish()
        QThreadPool.globalInstance().waitForDone(3000)
        self.app.processEvents()
        self.assertFalse(self.controller.selector.isVisible())
        self.assertEqual(self.controller.options, ())

    def test_image_selector_inserts_before_horizontal_row(self):
        cards = [self.workspace.add_image_plot(Path(self.temp.name), self.path.with_suffix(".png"))
                 for _ in range(3)]
        self.workspace.move_plot(cards[2], cards[1], "right")
        self.app.processEvents()
        mime = self.mime(kind=IMAGE_MIME_TYPE)
        self.prime(mime)
        point = cards[0].mapTo(self.workspace.viewport(), QPoint(100, cards[0].height()))
        self.send("enter", point, mime)
        self.controller._show_selector()
        self.assertEqual(self.controller.selector.options, ("image",))
        point = self.controller.selector.geometry().center()
        self.send("drop", point, mime)
        self.assertEqual(self.workspace.splitter.count(), 3)
        result = self.workspace.plots()
        self.assertEqual(result[0], cards[0])
        self.assertEqual(result[2:], cards[1:])
        self.assertEqual(result[1].drop_card_type, "image")

    def mime(self, name="b", kind=SERIES_MIME_TYPE):
        mime = QMimeData()
        payload = ({"h5_path": str(self.path), "series_name": name} if kind == SERIES_MIME_TYPE else
                   {"folder": self.temp.name, "image_path": str(self.path.with_suffix(".png")),
                    "video_path": str(self.path.with_suffix(".mp4"))})
        mime.setData(kind, json.dumps(payload).encode())
        return mime

    def prime(self, mime):
        self.assertTrue(self.controller._read_payload(mime))
        for _ in range(300):
            if self.controller._job is None:
                break
            qt_wait(10)
        self.assertIsNone(self.controller._job)

    def send(self, kind, point, mime, target=None, action=Qt.DropAction.CopyAction):
        target = target or self.workspace.viewport()
        cls = {"enter": QDragEnterEvent, "move": QDragMoveEvent, "drop": QDropEvent}[kind]
        event = cls(QPointF(point) if kind == "drop" else point, action, mime,
                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(target, event)
        return event

    def test_metadata_options_and_settings_round_trip(self):
        numeric = series_card_types(self.path, "a")
        self.assertEqual(numeric, ("multi", "line", "frequency", "event", "interval", "xy", "xyz", "gnss", "gnss_3d"))
        self.assertNotIn("frequency", series_card_types(self.path, "state"))
        self.assertTrue({"xy", "xyz", "gnss", "gnss_3d"}.issubset(series_card_types(self.path, "state")))
        self.assertIn("gnss", series_card_types(self.path, "GNSS/latitude"))
        self.assertIn("gnss_3d", series_card_types(self.path, "GNSS/latitude"))
        self.assertEqual(series_card_types(self.path, "missing"), ())
        self.assertEqual(sanitize_lodgazilla_settings({})["card_drop_selector_delay_ms"], 1000)
        self.assertEqual(sanitize_lodgazilla_settings({"card_drop_selector_delay_ms": -1})[
            "card_drop_selector_delay_ms"], 0)
        editor = SettingsEditorWidget(self.settings)
        editor.card_drop_selector_delay_spin.setValue(1700)
        editor.save()
        self.assertEqual(load_lodgazilla_settings(self.settings)["card_drop_selector_delay_ms"], 1700)
        editor.deleteLater()

    def test_numeric_on_image_and_attachments_on_numeric_are_rejected(self):
        image = self.workspace.add_image_plot(Path(self.temp.name), self.path.with_suffix(".png"))
        line = self.workspace.add_line_plot(self.path, "a")
        self.app.processEvents()
        for target, mime in ((image.canvas, self.mime()),
                             (line.series_plot.plot_widget.viewport(), self.mime(kind=IMAGE_MIME_TYPE)),
                             (line.series_plot.plot_widget.viewport(), self.mime(kind=VIDEO_MIME_TYPE))):
            with self.subTest(target=type(target).__name__, formats=mime.formats()):
                self.prime(mime)
                point = target.rect().center()
                self.send("enter", point, mime, target)
                self.assertFalse(self.send("move", point, mime, target).isAccepted())
                self.assertFalse(self.send("drop", point, mime, target).isAccepted())
                self.assertEqual(len(self.workspace.plots()), 2)
        self.assertEqual(line.series, [(self.path, "a")])

    def test_legend_drop_inserts_and_canvas_drop_appends_with_copy_action(self):
        card = self.workspace.add_line_plot(self.path, "a")
        card.add_series(self.path, "c")
        card.series_plot.set_series_color(0, "#123456")
        card.series_plot.set_series_color(1, "#654321")
        self.app.processEvents()
        mime = self.mime("b")
        self.prime(mime)
        legend = card.legend
        point = legend.mapTo(self.workspace.viewport(), QPoint(8, legend.rows[1].y()))
        self.send("enter", point, mime)
        self.assertTrue(self.send("move", point, mime).isAccepted())
        self.assertTrue(legend.drop_hint.isVisible())
        event = self.send("drop", point, mime)
        self.assertTrue(event.isAccepted())
        self.assertEqual(event.dropAction(), Qt.DropAction.CopyAction)
        self.assertEqual([name for _, name in card.series], ["a", "b", "c"])
        self.assertEqual(card.series_plot.series_colors()[::2], ["#123456", "#654321"])
        self.assertFalse(legend.drop_hint.isVisible())
        mime = self.mime("state")
        self.prime(mime)
        point = card.mapTo(self.workspace.viewport(), QPoint(100, card.height() // 2))
        self.send("enter", point, mime)
        self.send("drop", point, mime)
        self.assertEqual(card.series[-1], (self.path, "state"))

    def test_legend_drop_from_nested_widgets_in_scrolled_workspace(self):
        preceding = self.workspace.add_image_plot(Path(self.temp.name), self.path.with_suffix(".png"))
        self.workspace.resize_plot(preceding, 300)
        card = self.workspace.add_line_plot(self.path, "a")
        card.add_series(self.path, "c")
        self.workspace.resize(1000, 400)
        self.app.processEvents()
        self.workspace.verticalScrollBar().setValue(400)
        self.assertGreater(self.workspace.verticalScrollBar().value(), 0)
        for target, name, index, expected in (
                (card.series_plot.plot_widget.viewport(), "b", 1, ["a", "b", "c"]),
                (card, "state", 0, ["state", "a", "b", "c"])):
            with self.subTest(target=type(target).__name__):
                self.app.processEvents()
                mime = self.mime(name)
                self.prime(mime)
                legend = card.legend
                # Native Qt events use the receiving child's coordinates.
                point = target.mapFromGlobal(legend.mapToGlobal(QPoint(8, legend.rows[index].y())))
                messages = []
                previous = qInstallMessageHandler(lambda _kind, _context, message: messages.append(message))
                try:
                    self.send("enter", point, mime, target)
                    move = self.send("move", point, mime, target)
                    self.assertFalse([message for message in messages if "QWidget::map" in message])
                    self.assertTrue(move.isAccepted())
                    self.assertTrue(legend.drop_hint.isVisible())
                    drop = self.send("drop", point, mime, target)
                finally:
                    qInstallMessageHandler(previous)
                self.assertTrue(drop.isAccepted())
                self.assertEqual([series for _, series in card.series], expected)

    def test_intercepted_canvas_drag_does_not_forward_unmatched_leave(self):
        card = self.workspace.add_line_plot(self.path, "a")
        self.app.processEvents()
        target = card.series_plot.plot_widget.viewport()
        mime = self.mime()
        self.prime(mime)
        messages = []
        previous = qInstallMessageHandler(lambda _kind, _context, message: messages.append(message))
        try:
            self.send("enter", target.rect().center(), mime, target)
            QApplication.sendEvent(target, QDragLeaveEvent())
            self.app.processEvents()
        finally:
            qInstallMessageHandler(previous)
        self.assertFalse([message for message in messages if "dragLeaveEvent" in message])
        self.assertIsNone(self.controller.payload)

    def test_delayed_selector_creates_card_between_rows(self):
        first = self.workspace.add_image_plot(Path(self.temp.name), self.path.with_suffix(".png"))
        last = self.workspace.add_image_plot(Path(self.temp.name), self.path.with_suffix(".png"))
        self.app.processEvents()
        mime = self.mime()
        self.prime(mime)
        self.controller.delay_ms = 80
        point = first.mapTo(self.workspace.viewport(), QPoint(150, first.height()))
        self.send("enter", point, mime)
        self.send("move", point, mime)
        self.assertTrue(self.controller.line.isVisible())
        self.assertFalse(self.controller.selector.isVisible())
        qt_wait(120)
        selector = self.controller.selector
        self.assertTrue(selector.isVisible())
        choice = selector.mapTo(self.workspace.viewport(), QPoint(
            selector.width() // (2 * len(selector.options)), selector.height() // 2))
        self.send("move", choice, mime)
        self.assertTrue(self.send("drop", choice, mime).isAccepted())
        cards = self.workspace.plots()
        self.assertEqual([card.drop_card_type for card in cards], ["image", "multi", "image"])
        self.assertIs(cards[0], first)
        self.assertIs(cards[-1], last)
        self.assertFalse(selector.isVisible())

    def test_early_drop_retains_default_and_cancel_clears_timer(self):
        mime = self.mime()
        self.prime(mime)
        self.controller.delay_ms = 100
        point = QPoint(100, 100)
        self.send("enter", point, mime)
        self.send("drop", point, mime)
        self.assertEqual(self.workspace.plots()[0].drop_card_type, "multi")
        self.prime(mime)
        point = QPoint(100, 600)
        self.send("enter", point, mime)
        QApplication.sendEvent(self.workspace.viewport(), QDragLeaveEvent())
        qt_wait(150)
        self.assertFalse(self.controller.line.isVisible())
        self.assertFalse(self.controller.selector.isVisible())
        self.assertFalse(self.controller.timer.isActive())

    def test_selector_survives_child_transition_and_resets_for_new_gap(self):
        first = self.workspace.add_image_plot(Path(self.temp.name), self.path.with_suffix(".png"))
        self.workspace.add_image_plot(Path(self.temp.name), self.path.with_suffix(".png"))
        self.app.processEvents()
        mime = self.mime()
        self.prime(mime)
        self.controller.delay_ms = 10
        point = first.mapTo(self.workspace.viewport(), QPoint(100, first.height()))
        self.send("enter", point, mime)
        for _ in range(100):
            if self.controller.selector.isVisible():
                break
            qt_wait(10)
        self.assertTrue(self.controller.selector.isVisible())
        QApplication.sendEvent(self.workspace.viewport(), QDragLeaveEvent())
        self.send("enter", point, mime)
        self.app.processEvents()
        self.assertTrue(self.controller.selector.isVisible())
        self.controller.delay_ms = 1000
        self.send("move", QPoint(100, 650), mime)
        self.assertFalse(self.controller.selector.isVisible())
        self.assertEqual(self.controller.gap, 2)

    def test_event_and_map_insertion_keep_parallel_state_order(self):
        card = EventStatePlotWidget(self.path, "state")
        card.insert_series(self.path, "a", 0)
        self.assertEqual([name for _, name in card.series], ["a", "state"])
        self.assertEqual(len(card._colors), 2)
        colors = list(card._colors)
        card.reorder_series(0, 2)
        self.assertEqual([name for _, name in card.series], ["state", "a"])
        self.assertEqual(card._colors, list(reversed(colors)))
        card.deleteLater()
        # Exercise map ordering without starting Chromium or loading tracks.
        class MapCard(GnssPlotWidget):
            def __init__(self):
                PlotBaseWidget.__init__(self, self_path, "a")
                self.configure_legend_groups(("Lat", "Lon"))
                self.grouped_series.groups.append(SeriesGroup((AxisSource(self_path, "a"), AxisSource(self_path, "b")), "#123456"))
                self.on_series_changed = mock.Mock()
        self_path = self.path
        card = MapCard()
        with mock.patch("src.gui.plots.gnss.gnss_pair_for_series", return_value=(self.path, "c", self.path, "b")):
            card.insert_series(self.path, "b", 0)
        self.assertEqual([pair[1] for pair in card.tracks], ["c", "a"])
        self.assertEqual(card.series, [(self.path, "c"), (self.path, "b"), (self.path, "a")])
        self.assertEqual(card._track_colors[1], "#123456")
        card.grouped_series.reorder(0, 2)
        self.assertEqual([pair[1] for pair in card.tracks], ["a", "c"])
        self.assertEqual(card.series, [(self.path, "a"), (self.path, "b"), (self.path, "c")])
        self.assertEqual(card._track_colors[0], "#123456")
        card.deleteLater()

    def internal_drop(self, card, source_key, point, modifiers=Qt.KeyboardModifier.NoModifier):
        entry = next(entry for entry in card.legend.entries if entry.key == source_key)
        source = LegendDragSource(card, entry)
        mime = self.mime(entry.source[1])
        self.prime(mime)
        destination = self.controller.update_preview(point, source, modifiers)
        self.assertIsNotNone(destination)
        self.assertTrue(card.legend.drop_hint.isVisible())
        # Exercise the same receiver mapping and dispatch as a native Qt drop.
        event = mock.Mock()
        event.type.return_value = QEvent.Type.Drop
        event.mimeData.return_value = mime
        event.position.return_value = QPointF(point)
        event.source.return_value = source
        event.modifiers.return_value = modifiers
        event.possibleActions.return_value = Qt.DropAction.MoveAction | Qt.DropAction.LinkAction
        self.controller.eventFilter(self.workspace.viewport(), event)
        if entry.group_index is None:
            event.acceptProposedAction.assert_called_once()
        else:
            event.accept.assert_called_once()
        self.assertTrue(source.handled)

    def test_internal_reorder_both_directions_preserves_colors_and_card(self):
        for multi in (False, True):
            with self.subTest(multi=multi):
                card = (self.workspace.add_multi_plot([(self.path, "a")]) if multi else
                        self.workspace.add_line_plot(self.path, "a"))
                card.add_series(self.path, "b")
                card.add_series(self.path, "c")
                colors = ["#123456", "#654321", "#abcdef"]
                for index, color in enumerate(colors):
                    card.series_plot.set_series_color(index, color)
                self.app.processEvents()
                point = card.legend.mapTo(self.workspace.viewport(),
                                           QPoint(8, card.legend.rows[-1].geometry().bottom()))
                self.internal_drop(card, 0, point)
                self.assertEqual([name for _, name in card.series], ["b", "c", "a"])
                self.assertEqual(card.series_plot.series_colors(), colors[1:] + colors[:1])
                self.app.processEvents()
                point = card.legend.mapTo(self.workspace.viewport(), QPoint(8, card.legend.rows[0].y()))
                self.internal_drop(card, 2, point)
                self.assertEqual([name for _, name in card.series], ["a", "b", "c"])
                self.assertEqual(card.series_plot.series_colors(), colors)
                self.assertEqual(card.legend._headers, [])
                self.workspace.remove_plot(card)

    def test_xy_group_label_replacement_and_axis_swap(self):
        card = self.workspace.add_xy_plot((self.path, "a"), (self.path, "b"))
        self.app.processEvents()
        self.assertEqual([entry.role for entry in card.legend.entries], ["X", "Y"])
        self.assertEqual(len(card.legend.group_headers), 1)
        row = card.legend.rows[0]
        label = next(label for label in row.findChildren(_LegendLabel) if label.text() == "X")
        point = label.rect().center()
        mime = self.mime("c")
        self.prime(mime)
        self.send("enter", point, mime, label)
        self.assertTrue(self.send("move", point, mime, label).isAccepted())
        self.assertGreater(card.legend.drop_hint.height(), 2)
        self.assertTrue(self.send("drop", point, mime, label).isAccepted())
        self.assertEqual(card.series, [(self.path, "c"), (self.path, "b")])
        self.app.processEvents()
        point = card.legend.rows[1].mapTo(self.workspace.viewport(), card.legend.rows[1].rect().center())
        self.internal_drop(card, 0, point, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(card.series, [(self.path, "b"), (self.path, "c")])
        # Canvas drops now create a new group with the remaining axis empty.
        self.prime(mime)
        point = card.plot_widget.mapTo(self.workspace.viewport(), QPoint(20, 100))
        self.assertEqual(self.controller.update_preview(point), ("groups", card, 1))
        self.assertTrue(self.controller.card_hint.isVisible())
        self.assertFalse(self.controller.line.isVisible())

    def test_internal_drag_survives_deleted_source_label_without_removal(self):
        card = self.workspace.add_xy_plot((self.path, "a"), (self.path, "b"))
        label = card.legend.rows[0].findChild(_LegendLabel)
        old = list(card.series)
        def perform(drag, actions):
            self.assertEqual(actions, Qt.DropAction.MoveAction | Qt.DropAction.LinkAction)
            source = drag.source()
            self.controller.apply_drop(("swap", card, 1), {"series": old[0]}, source)
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            return Qt.DropAction.LinkAction
        event = mock.Mock()
        event.buttons.return_value = Qt.MouseButton.LeftButton
        event.position.return_value = QPointF(30, 10)
        label._press_position = QPoint(0, 0)
        with mock.patch("src.gui.plots.legend.exec_data_drag", side_effect=perform):
            label.mouseMoveEvent(event)
        self.assertEqual(card.series, list(reversed(old)))
        self.assertIn(card, self.workspace.plots())

    def test_move_from_other_legend_replaces_xy_axis_and_removes_source(self):
        source_card = self.workspace.add_line_plot(self.path, "c")
        card = self.workspace.add_xy_plot((self.path, "a"), (self.path, "b"))
        self.app.processEvents()
        label = source_card.legend.findChild(_LegendLabel)
        def perform(drag, actions):
            self.assertEqual(actions, Qt.DropAction.MoveAction)
            self.prime(drag.mimeData())
            point = card.legend.rows[1].mapTo(self.workspace.viewport(), card.legend.rows[1].rect().center())
            destination = self.controller.update_preview(point, drag.source())
            self.assertEqual(destination, ("replace", card, 1))
            self.controller.apply_drop(destination, self.controller.payload, drag.source())
            return Qt.DropAction.MoveAction
        event = mock.Mock()
        event.buttons.return_value = Qt.MouseButton.LeftButton
        event.position.return_value = QPointF(30, 10)
        label._press_position = QPoint(0, 0)
        with mock.patch("src.gui.plots.legend.exec_data_drag", side_effect=perform):
            label.mouseMoveEvent(event)
        self.assertEqual(card.series, [(self.path, "a"), (self.path, "c")])
        self.assertEqual(self.workspace.plots(), [card])

    def test_ctrl_changes_axis_preview_and_native_cursor_action(self):
        card = self.workspace.add_xy_plot((self.path, "a"), (self.path, "b"))
        self.app.processEvents()
        source = LegendDragSource(card, card.legend.entries[0])
        mime = self.mime("a")
        self.prime(mime)
        point = card.legend.rows[1].mapTo(self.workspace.viewport(), card.legend.rows[1].rect().center())
        event = mock.Mock()
        event.type.return_value = QEvent.Type.DragMove
        event.mimeData.return_value = mime
        event.position.return_value = QPointF(point)
        event.source.return_value = source
        event.possibleActions.return_value = Qt.DropAction.MoveAction | Qt.DropAction.LinkAction
        for modifiers, action, swap in (
                (Qt.KeyboardModifier.NoModifier, Qt.DropAction.MoveAction, False),
                (Qt.KeyboardModifier.ControlModifier, Qt.DropAction.LinkAction, True),
                (Qt.KeyboardModifier.NoModifier, Qt.DropAction.MoveAction, False)):
            with self.subTest(modifiers=modifiers):
                event.modifiers.return_value = modifiers
                self.controller.eventFilter(self.workspace.viewport(), event)
                event.setDropAction.assert_called_with(action)
                self.assertEqual(card.legend.swap_hint.isVisible(), swap)
                self.assertIn("dashed" if swap else "solid", card.legend.drop_hint.styleSheet())
        self.controller.finish()
        self.assertFalse(card.legend.swap_hint.isVisible())
        self.assertFalse(card.legend.drop_hint.isVisible())
        self.assertEqual(card.series, [(self.path, "a"), (self.path, "b")])

    def test_drop_hints_switch_between_card_row_legend_gap_and_workspace_gap(self):
        card = self.workspace.add_line_plot(self.path, "a")
        card.add_series(self.path, "b")
        image = self.workspace.add_image_plot(Path(self.temp.name), self.path.with_suffix(".png"))
        self.app.processEvents()
        self.prime(self.mime("c"))
        body = card.mapTo(self.workspace.viewport(), QPoint(30, card.height() // 2))
        self.assertEqual(self.controller.update_preview(body), ("card", card, -1))
        self.assertTrue(self.controller.card_hint.isVisible())
        self.assertFalse(self.controller.line.isVisible())
        self.assertFalse(card.legend.drop_hint.isVisible())
        self.assertTrue(self.controller.card_hint.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))

        row = card.legend.rows[0]
        point = row.mapTo(self.workspace.viewport(), row.rect().center())
        self.controller.update_preview(point)
        self.assertTrue(card.legend.drop_hint.isVisible())
        self.assertGreater(card.legend.drop_hint.height(), 2)
        self.assertIn("border: 2px solid", card.legend.drop_hint.styleSheet())
        self.assertFalse(self.controller.card_hint.isVisible())
        self.assertFalse(self.controller.line.isVisible())

        point = card.legend.mapTo(self.workspace.viewport(), QPoint(8, card.legend.rows[1].y() - 1))
        self.assertEqual(self.controller.update_preview(point), ("card", card, 1))
        self.assertTrue(card.legend.drop_hint.isVisible())
        self.assertEqual(card.legend.drop_hint.height(), 2)
        self.assertFalse(self.controller.card_hint.isVisible())
        self.assertFalse(self.controller.line.isVisible())

        point = card.mapTo(self.workspace.viewport(), QPoint(30, card.height()))
        self.assertEqual(self.controller.update_preview(point), ("gap", 1, None))
        self.assertTrue(self.controller.line.isVisible())
        self.assertTrue(self.controller.timer.isActive())
        self.assertFalse(self.controller.card_hint.isVisible())
        self.assertFalse(card.legend.drop_hint.isVisible())

        point = image.mapTo(self.workspace.viewport(), QPoint(30, 30))
        self.assertIsNone(self.controller.update_preview(point))
        self.assertFalse(self.controller.line.isVisible())
        self.assertFalse(self.controller.card_hint.isVisible())
        self.assertFalse(self.controller.timer.isActive())
        self.controller.update_preview(body)
        self.controller.finish()
        self.assertFalse(self.controller.card_hint.isVisible())

    def test_card_outline_follows_individual_cards_in_scrolled_horizontal_row(self):
        preceding = self.workspace.add_image_plot(Path(self.temp.name), self.path.with_suffix(".png"))
        self.workspace.resize_plot(preceding, 300)
        cards = [self.workspace.add_line_plot(self.path, name) for name in ("a", "b")]
        self.workspace.move_plot(cards[1], cards[0], "right")
        self.workspace.resize(1000, 400)
        self.app.processEvents()
        self.workspace.verticalScrollBar().setValue(self.workspace.verticalScrollBar().maximum())
        self.assertGreater(self.workspace.verticalScrollBar().value(), 0)
        self.prime(self.mime("c"))
        for card in cards:
            with self.subTest(series=card.series):
                point = card.mapTo(self.workspace.viewport(), QPoint(30, card.height() // 2))
                self.assertEqual(self.controller.update_preview(point), ("card", card, -1))
                self.assertTrue(self.controller.card_hint.isVisible())
                expected = QRect(card.mapTo(self.workspace.viewport(), QPoint()), card.size()).adjusted(1, 1, -1, -1)
                self.assertEqual(self.controller.card_hint.geometry(), expected)
                self.assertFalse(self.controller.line.isVisible())
        self.workspace.verticalScrollBar().setValue(0)
        self.assertFalse(self.controller.card_hint.isVisible())


    def side_point(self, card, position):
        x = 10 if position == "left" else card.width() - 10
        return card.mapTo(self.workspace.viewport(), QPoint(x, card.height() // 2))

    def test_navigator_attachment_drops_beside_a_media_card(self):
        for position in ("left", "right"):
            with self.subTest(position=position):
                self.workspace.close_all_plots()
                target = self.workspace.add_image_plot(Path(self.temp.name), self.path.with_suffix(".png"))
                self.app.processEvents()
                mime = self.mime(kind=IMAGE_MIME_TYPE)
                self.prime(mime)
                point = self.side_point(target, position)
                self.assertEqual(self.controller.update_preview(point), ("beside", target, position))
                self.assertTrue(self.controller.line.isVisible())
                self.assertGreater(self.controller.line.height(), self.controller.line.width())
                self.send("enter", point, mime)
                self.assertTrue(self.send("drop", point, mime).isAccepted())
                self.app.processEvents()
                row = self.workspace.splitter.row_for(target)
                self.assertEqual(self.workspace.splitter.count(), 1)
                new = next(plot for plot in self.workspace.plots() if plot is not target)
                self.assertIs(self.workspace.splitter.row_for(new), row)
                expected = [new, target] if position == "left" else [target, new]
                self.assertEqual(row.plots(), expected)

    def test_navigator_selection_opening_a_map_drops_beside_a_media_card(self):
        target = self.workspace.add_image_plot(Path(self.temp.name), self.path.with_suffix(".png"))
        self.app.processEvents()
        mime = QMimeData()
        sources = [PlotSource("series", self.path, "GNSS/latitude"), PlotSource("series", self.path, "GNSS/longitude")]
        mime.setData(SELECTION_MIME_TYPE, json.dumps([source.payload() for source in sources]).encode())
        self.prime(mime)
        point = self.side_point(target, "right")
        self.send("enter", point, mime)
        self.assertTrue(self.send("drop", point, mime).isAccepted())
        self.app.processEvents()
        new = next(plot for plot in self.workspace.plots() if plot is not target)
        self.assertIsInstance(new, GnssPlotWidget)
        self.assertEqual(self.workspace.splitter.row_for(target).plots(), [target, new])

    def test_side_drop_needs_side_by_side_cards_on_both_ends(self):
        image = self.workspace.add_image_plot(Path(self.temp.name), self.path.with_suffix(".png"))
        line = self.workspace.add_line_plot(self.path, "a")
        self.app.processEvents()
        # A numeric series opens a full-width card, so an image's edge stays a plain card drop.
        mime = self.mime("b")
        self.prime(mime)
        self.assertIsNone(self.controller.update_preview(self.side_point(image, "right")))
        # Line cards always stay full-width, even for attachments.
        mime = self.mime(kind=IMAGE_MIME_TYPE)
        self.prime(mime)
        self.assertIsNone(self.controller.update_preview(self.side_point(line, "right")))
        self.assertEqual(self.workspace.splitter.row_for(image), image)

if __name__ == "__main__":
    unittest.main()
