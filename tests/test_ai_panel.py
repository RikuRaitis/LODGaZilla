"""The AI panel's UI: AI Integration row, provider and sign-in, drop boxes, AI conversation and AI input."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtTest import QTest

from src.gui.ai.panel import (
    CREATE_PLUGIN,
    INPUT_PLACEHOLDER,
    MAGIC_DROP,
    MAX_INPUT_LINES,
    NOT_CONNECTED_REPLY,
    PLUGIN_GREETING,
    PLUGIN_INPUT_PLACEHOLDER,
    AIPanel,
)
from tests.test_review_plots import _QtTestCase


class AIPanelTests(_QtTestCase):
    def panel(self, expanded=True, conversation=True):
        """A shown panel; ``conversation`` opens the AI conversation as using a drop box does."""
        panel = AIPanel()
        self.widgets.append(panel)
        panel.resize(400, 500)
        panel.show()
        panel.set_expanded(expanded)
        if conversation:
            panel._open_conversation(MAGIC_DROP)
        self.app.processEvents()
        return panel

    @staticmethod
    def drag_events(path, position=QPoint(10, 10)):
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(path))])
        enter = QDragEnterEvent(position, Qt.DropAction.CopyAction, data,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        drop = QDropEvent(QPointF(position), Qt.DropAction.CopyAction, data,
                          Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        return enter, drop, data

    def sample(self, name="logger.csv"):
        path = self.directory / name
        path.write_text("time,value\n")
        return path

    def test_minimized_panel_is_one_row_that_expands_when_clicked(self):
        panel = self.panel(expanded=False)
        self.assertTrue(panel.mini_row.isVisible())
        self.assertEqual(panel.mini_row.text(), "AI Integration")
        self.assertFalse(panel.body.isVisible())
        self.assertEqual(panel.height(), AIPanel.MINI_HEIGHT)
        changes = []
        panel.expanded_changed.connect(changes.append)
        panel.mini_row.click()
        self.assertTrue(panel.body.isVisible())
        panel.collapse_button.click()
        self.assertFalse(panel.body.isVisible())
        self.assertEqual(changes, [True, False])

    def test_provider_selector_and_sign_in(self):
        panel = self.panel()
        self.assertEqual([panel.provider_combo.itemText(i) for i in range(panel.provider_combo.count())],
                         ["Claude", "Codex"])
        self.assertEqual(panel.status_label.text(), "Claude: not signed in")
        requests = []
        panel.sign_in_requested.connect(requests.append)
        panel.provider_combo.setCurrentText("Codex")
        self.assertEqual(panel.status_label.text(), "Codex: not signed in")
        panel.sign_in_button.click()
        self.assertEqual(requests, ["Codex"])
        panel.set_signed_in("Codex", True)
        self.assertEqual(panel.status_label.text(), "Codex: signed in")
        self.assertFalse(panel.sign_in_button.isVisible())
        panel.provider_combo.setCurrentText("Claude")
        self.assertTrue(panel.sign_in_button.isVisible())

    def test_send_button_becomes_stop_while_the_agent_is_busy(self):
        panel = self.panel()
        stops, questions = [], []
        panel.stop_requested.connect(lambda: stops.append(True))
        panel.question_submitted.connect(lambda text, files: questions.append(text))
        self.assertEqual(panel.send_button.toolTip(), "Send")
        panel.set_busy(True)
        self.assertEqual(panel.send_button.toolTip(), "Stop")
        self.assertFalse(panel.provider_combo.isEnabled())
        panel.input.setPlainText("Hello")
        panel.send_button.click()
        QTest.keyClick(panel.input, Qt.Key.Key_Return)
        self.assertEqual((stops, questions), ([True], []))
        panel.set_busy(False)
        self.assertEqual(panel.send_button.toolTip(), "Send")
        panel.send_button.click()
        self.assertEqual(questions, ["Hello"])

    def test_input_grows_to_ten_lines_over_the_conversation(self):
        panel = self.panel()
        frame, conversation = panel.input_frame, panel.conversation
        one_line = panel.input.height()
        self.assertGreaterEqual(frame.y(), conversation.geometry().bottom())
        QTest.keyClicks(panel.input, "line")
        for _ in range(3):
            QTest.keyClick(panel.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
            QTest.keyClicks(panel.input, "line")
        self.app.processEvents()
        self.assertEqual(panel.input.toPlainText().count("\n"), 3)
        self.assertGreater(panel.input.height(), one_line)
        # The conversation keeps its size; the input frame overlays its bottom.
        self.assertLess(frame.y(), conversation.geometry().bottom())
        panel.input.setPlainText("\n".join(["line"] * 30))
        self.app.processEvents()
        self.assertEqual(panel.input.height(), frame.input_height(MAX_INPUT_LINES))
        panel.input.clear()
        self.app.processEvents()
        self.assertEqual(panel.input.height(), one_line)

    def test_files_attach_once_and_chips_remove_them(self):
        panel = self.panel()
        sample = self.sample()
        panel.add_attachments([sample, sample, self.directory, self.directory / "missing.csv"])
        self.assertEqual(panel.attached_files(), [sample])
        self.assertTrue(panel.chip_row.isVisible())
        chips = panel._chips()
        self.assertEqual([chip.label.text() for chip in chips], ["logger.csv"])
        chips[0].remove_button.click()
        self.app.processEvents()
        self.assertEqual(panel.attached_files(), [])
        self.assertFalse(panel.chip_row.isVisible())

    def test_dragging_files_over_the_minimized_row_opens_the_drop_boxes(self):
        panel = self.panel(expanded=False, conversation=False)
        enter, drop, _data = self.drag_events(self.sample("logger.bin"))
        panel.dragEnterEvent(enter)
        self.assertTrue(enter.isAccepted())
        self.assertTrue(panel.is_expanded)
        # Expanded, only the two drop boxes show; the AI conversation waits until one is used.
        self.app.processEvents()
        self.assertTrue(panel.magic_drop_zone.isVisible() and panel.plugin_zone.isVisible())
        self.assertFalse(panel.conversation_area.isVisible())
        self.assertIsNone(panel.mode)
        self.assertGreaterEqual(panel.minimumHeight(), AIPanel.EXPANDED_MIN_HEIGHT)
        # Outside the boxes nothing takes the files.
        dropped = []
        panel.files_dropped.connect(lambda files, hints: dropped.append(files))
        panel.dropEvent(drop)
        self.assertEqual((dropped, panel.attached_files()), ([], []))

    def test_a_drop_box_is_highlighted_while_files_are_dragged_over_it(self):
        panel = self.panel(conversation=False)
        zone = panel.magic_drop_zone
        enter, _drop, data = self.drag_events(self.sample())
        zone.dragEnterEvent(enter)
        self.assertTrue(enter.isAccepted())
        self.assertTrue(zone.is_highlighted)
        self.assertFalse(panel.plugin_zone.is_highlighted)
        zone.dragLeaveEvent(QDragLeaveEvent())
        self.assertFalse(zone.is_highlighted)
        # Text, not files, is not taken.
        text = QMimeData()
        text.setText("hello")
        enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, text,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        zone.dragEnterEvent(enter)
        self.assertFalse(enter.isAccepted())
        self.assertFalse(zone.is_highlighted)

    def test_magic_drop_box_starts_magic_drop_with_the_typed_hints(self):
        panel = self.panel(conversation=False)
        sample = self.sample("logger.bin")
        dropped, started = [], []
        panel.files_dropped.connect(lambda files, hints: dropped.append((files, hints)))
        panel.conversation_started.connect(lambda: started.append(True))
        panel.input.setPlainText("Column 2 is mbar")
        enter, drop, _data = self.drag_events(sample)
        panel.magic_drop_zone.dragEnterEvent(enter)
        panel.magic_drop_zone.dropEvent(drop)
        self.app.processEvents()
        self.assertFalse(panel.magic_drop_zone.is_highlighted)
        self.assertEqual(dropped, [([sample], "Column 2 is mbar")])
        self.assertEqual((panel.attached_files(), panel.input.toPlainText()), ([], ""))
        self.assertIn("Magic drop: Column 2 is mbar", panel.conversation.toPlainText())
        # The boxes shrink to one row above the AI conversation, which opens once.
        self.assertEqual(panel.mode, MAGIC_DROP)
        self.assertTrue(panel.conversation_area.isVisible())
        self.assertTrue(panel.magic_drop_zone.is_compact and panel.plugin_zone.is_compact)
        self.assertEqual(panel.input.placeholderText(), INPUT_PLACEHOLDER)
        panel.magic_drop_zone.dropEvent(drop)
        self.assertEqual((len(dropped), started), (2, [True]))

    def test_create_plugin_box_attaches_files_after_a_greeting(self):
        panel = self.panel(conversation=False)
        first, second = self.sample("logger.bin"), self.sample("format.pdf")
        dropped = []
        panel.files_dropped.connect(lambda files, hints: dropped.append(files))
        _enter, drop, _data = self.drag_events(first)
        panel.plugin_zone.dropEvent(drop)
        self.app.processEvents()
        self.assertEqual(panel.mode, CREATE_PLUGIN)
        self.assertEqual(panel.attached_files(), [first])
        self.assertTrue(panel.conversation_area.isVisible() and panel.chip_row.isVisible())
        self.assertEqual(panel.input.placeholderText(), PLUGIN_INPUT_PLACEHOLDER)
        self.assertIn(PLUGIN_GREETING.splitlines()[0], panel.conversation.toPlainText())
        # More files, on the box or on the AI conversation, join the attachments; one greeting.
        panel.attach_for_plugin([second])
        self.assertEqual(panel.attached_files(), [first, second])
        third = self.sample("more.csv")
        position = panel.conversation_area.mapTo(panel, panel.conversation_area.rect().center())
        _enter, drop, _data = self.drag_events(third, position)
        panel.dropEvent(drop)
        self.assertEqual(panel.attached_files(), [first, second, third])
        self.assertEqual(panel.conversation.toPlainText().count("How can I help?"), 1)
        self.assertEqual(dropped, [])
        # After a Magic drop, the AI conversation no longer takes files: only the boxes do.
        panel.start_magic_drop([first])
        panel.dropEvent(drop)
        self.assertEqual(panel.attached_files(), [first, second, third])

    def test_a_question_and_its_files_go_to_the_conversation(self):
        panel = self.panel()
        sample = self.sample()
        panel.add_attachments([sample])
        submitted = []
        panel.question_submitted.connect(lambda text, files: submitted.append((text, files)))
        panel.input.setPlainText("What format is this?")
        QTest.keyClick(panel.input, Qt.Key.Key_Return)
        conversation = panel.conversation.toPlainText()
        self.assertIn("What format is this?", conversation)
        self.assertIn("logger.csv", conversation)
        # The main window answers; the panel only shows the question and sends it on.
        self.assertNotIn(NOT_CONNECTED_REPLY, conversation)
        self.assertEqual(submitted, [("What format is this?", [sample])])
        self.assertEqual((panel.input.toPlainText(), panel.attached_files()), ("", []))
        panel.send_button.click()  # Nothing to send.
        self.assertEqual(len(submitted), 1)


if __name__ == "__main__":
    unittest.main()
