import ctypes
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.app.internal_log import InternalLogPanel, InternalLogger, NativeStderrLogCapture  # noqa: E402


class InternalLogPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_plain_text_panel_evicts_oldest_lines_at_configured_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = InternalLogger(Path(temp_dir) / "log.txt")
            panel = InternalLogPanel(logger, maximum_lines=3)
            for index in range(5):
                panel.append_message("12:00:00.000", f"message {index}")

            self.assertLessEqual(panel.entries.document().blockCount(), 3)
            self.assertNotIn("message 0", panel.entries.toPlainText())
            self.assertIn("message 4", panel.entries.toPlainText())
            # A new length from Settings applies at once.
            panel.set_maximum_lines(2)
            self.assertLessEqual(panel.entries.document().blockCount(), 2)
            self.assertIn("message 4", panel.entries.toPlainText())
            logger.close()

    def test_a_long_message_never_widens_the_panel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = InternalLogger(Path(temp_dir) / "log.txt")
            self.addCleanup(logger.close)
            panel = InternalLogPanel(logger)
            self.addCleanup(panel.close)
            panel.set_expanded(False)
            panel.resize(300, panel.MINI_HEIGHT)
            panel.show()
            self.app.processEvents()
            widths = (panel.minimumSizeHint().width(), panel.sizeHint().width())
            message = "very long message " * 200
            panel.append_message("12:00:00.000", message)
            self.app.processEvents()
            self.assertLessEqual((panel.minimumSizeHint().width(), panel.sizeHint().width()), widths)
            # The collapsed row shows the start, cut to fit; the tooltip and the full text keep it all.
            label = panel.last_message
            self.assertTrue(label.text().startswith("12:00:00.000  very"))
            self.assertTrue(label.text().endswith("…"))
            self.assertLessEqual(label.fontMetrics().horizontalAdvance(label.text()), label.width())
            self.assertEqual(label.toolTip(), message)
            self.assertEqual(label.full_text(), f"12:00:00.000  {message.splitlines()[-1]}")
            # A wider panel shows more of it.
            shown = len(label.text())
            panel.resize(600, panel.MINI_HEIGHT)
            self.app.processEvents()
            self.assertGreater(len(label.text()), shown)
            panel.close()
            logger.close()


class NativeStderrLogCaptureTests(unittest.TestCase):
    def test_stop_ends_the_reader_and_restores_valid_standard_handles(self):
        logger = mock.Mock()
        capture = NativeStderrLogCapture(logger)
        capture.start()
        reader = capture._thread
        try:
            os.write(2, b"native line\n")
        finally:
            capture.stop()
        # The reader leaves on end-of-file before its descriptor is closed, so
        # it can never read from a reused descriptor number.
        self.assertIsNotNone(reader)
        self.assertFalse(reader.is_alive())
        self.assertIsNone(capture._read_fd)
        logger.info.assert_called_with("native console: native line")
        if os.name == "nt":
            import msvcrt

            kernel32 = ctypes.windll.kernel32
            kernel32.GetStdHandle.restype = ctypes.c_void_p
            for std_id, fd in ((-11, 1), (-12, 2)):
                handle = kernel32.GetStdHandle(std_id)
                # Processes started later inherit these; they must stay valid.
                self.assertEqual(handle, msvcrt.get_osfhandle(fd))


if __name__ == "__main__":
    unittest.main()


class QtMessageFilterTests(unittest.TestCase):
    def test_only_the_map_style_warning_is_dropped(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QtMsgType
        from src.app import lodgazilla

        logger = mock.Mock()
        with mock.patch.object(lodgazilla, "qInstallMessageHandler"):
            lodgazilla.install_qt_message_logging(logger)
        handle = logger._qt_message_handler
        handle(QtMsgType.QtWarningMsg, SimpleNamespace(category="js"),
               "Expected value to be of type number, but found null instead")
        logger.warning.assert_not_called()
        handle(QtMsgType.QtWarningMsg, SimpleNamespace(category="js"), "Uncaught TypeError: x is undefined")
        handle(QtMsgType.QtWarningMsg, SimpleNamespace(category="qt.multimedia"),
               "Expected value to be of type number, but found null instead")
        self.assertEqual([call.args[0] for call in logger.warning.call_args_list], [
            "js: Uncaught TypeError: x is undefined",
            "qt.multimedia: Expected value to be of type number, but found null instead",
        ])


class CollapseArrowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_the_collapse_arrow_sits_on_top_of_the_scroll_bar(self):
        from PySide6.QtCore import QPoint
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = InternalLogger(Path(temp_dir) / "log.txt")
            panel = InternalLogPanel(logger)
            panel.resize(500, 200)
            panel.show()
            self.app.processEvents()
            button, bar, text = panel.expanded_button, panel.entries.verticalScrollBar(), panel.entries.viewport()
            self.assertTrue(button.isVisible() and bar.isVisible())
            top_left = button.mapTo(panel, QPoint(0, 0))
            # Right of the text, at the top, above the scroll bar.
            self.assertGreaterEqual(top_left.x(), text.mapTo(panel, QPoint(text.width(), 0)).x())
            self.assertLessEqual(top_left.y(), panel.entries.mapTo(panel, QPoint(0, 0)).y() + 2)
            self.assertLessEqual(top_left.y() + button.height(), bar.mapTo(panel, QPoint(0, 0)).y() + 1)
            button.click()
            self.assertFalse(panel.is_expanded)
            panel.close()
            logger.close()
