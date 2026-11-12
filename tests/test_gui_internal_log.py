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
