"""Timestamped in-application logging with a persistent text log."""

from __future__ import annotations

import ctypes
import logging
import logging.handlers
import queue
import os
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.navigator.controls import make_expand_icon


class InternalLogger(QObject):
    """Queue disk logging off the producer thread and notify the Qt GUI."""

    message_logged = Signal(str, str, str)

    def __init__(self, log_path: str | Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(f"lodgazilla.internal.{path.resolve()}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self._listener: logging.handlers.QueueListener | None = None
        self._queue_handler: logging.Handler | None = None
        self._file_handler: logging.Handler | None = None
        if not self.logger.handlers:
            # Native Qt/FFmpeg output can arrive in bursts. A QueueListener
            # keeps its synchronous filesystem flushes off both the UI and
            # native-stderr reader threads; rotation bounds disk use as well.
            file_handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s.%(msecs)03d %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"
            ))
            write_queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
            queue_handler = logging.handlers.QueueHandler(write_queue)
            self.logger.addHandler(queue_handler)
            self._queue_handler = queue_handler
            self._file_handler = file_handler
            self._listener = logging.handlers.QueueListener(write_queue, file_handler)
            self._listener.start()

    def info(self, message: str) -> None:
        self._log(logging.INFO, message)

    def warning(self, message: str) -> None:
        self._log(logging.WARNING, message)

    def error(self, message: str) -> None:
        self._log(logging.ERROR, message)

    def _log(self, level: int, message: str, *, message_type: str = "message") -> None:
        text = str(message)
        self.logger.log(level, text)
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S.%f")[:-3]
        self.message_logged.emit(timestamp, text, message_type)

    def close(self) -> None:
        """Flush queued records once, during orderly application shutdown."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        if self._queue_handler is not None:
            self.logger.removeHandler(self._queue_handler)
            self._queue_handler = None
        if self._file_handler is not None:
            self._file_handler.close()
            self._file_handler = None


class NativeStderrLogCapture:
    """Redirect native-library stdout/stderr (FFmpeg, Qt plugins) into InternalLogger."""

    READER_STOP_SECONDS = 1.0

    def __init__(self, logger: InternalLogger) -> None:
        self.logger = logger
        self._saved_stdout: int | None = None
        self._saved_stderr: int | None = None
        self._read_fd: int | None = None
        self._write_fd: int | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._saved_stderr is not None:
            return
        try:
            read_fd, write_fd = os.pipe()
            self._saved_stdout = os.dup(1)
            self._saved_stderr = os.dup(2)
            os.dup2(write_fd, 1)
            os.dup2(write_fd, 2)
            os.close(write_fd)
            self._redirect_windows_standard_handles()
            self._read_fd = read_fd
            self._write_fd = None
            self._thread = threading.Thread(target=self._read_lines, name="native-stderr-log", daemon=True)
            self._thread.start()
        except OSError:
            self.stop()

    def stop(self) -> None:
        redirected = self._saved_stdout is not None or self._saved_stderr is not None
        if self._saved_stdout is not None:
            try:
                os.dup2(self._saved_stdout, 1)
            except OSError:
                pass
            try:
                os.close(self._saved_stdout)
            except OSError:
                pass
            self._saved_stdout = None
        if self._saved_stderr is not None:
            try:
                os.dup2(self._saved_stderr, 2)
            except OSError:
                pass
            try:
                os.close(self._saved_stderr)
            except OSError:
                pass
            self._saved_stderr = None
        if redirected:
            self._restore_windows_standard_handles()
        # Restoring fds 1 and 2 closed the pipe's last write ends, so the reader
        # sees end-of-file. Close the read end only after the reader has left:
        # closing it under a blocked read frees the descriptor number for reuse,
        # and the reader could then consume another pipe's data (for example a
        # worker queue's messages).
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(self.READER_STOP_SECONDS)
            if thread.is_alive():
                # Another process still holds a write end; leave the read end open.
                self._read_fd = None
                return
        if self._read_fd is not None:
            try:
                os.close(self._read_fd)
            except OSError:
                pass
            self._read_fd = None

    def _read_lines(self) -> None:
        if self._read_fd is None:
            return
        try:
            with os.fdopen(self._read_fd, "rb", closefd=False) as stream:
                for raw_line in iter(stream.readline, b""):
                    text = raw_line.decode("utf-8", errors="replace").rstrip()
                    if text:
                        self.logger.info(f"native console: {text}")
        except OSError:
            return

    def _redirect_windows_standard_handles(self) -> None:
        """Cover libraries that write via Win32 handles instead of C file descriptors."""
        if os.name != "nt":
            return
        try:
            import msvcrt

            handle = msvcrt.get_osfhandle(1)
            _set_std_handles(handle, handle)
        except (AttributeError, OSError):
            pass

    def _restore_windows_standard_handles(self) -> None:
        """Point the Win32 handles at the restored descriptors.

        Saved handle values cannot be reused: in a console process the C
        runtime already switched them to the pipe in ``os.dup2``, and that
        pipe handle is closed by now. The restored fds own valid handles.
        """
        if os.name != "nt":
            return
        try:
            import msvcrt

            _set_std_handles(msvcrt.get_osfhandle(1), msvcrt.get_osfhandle(2))
        except (AttributeError, OSError):
            pass


def _set_std_handles(stdout_handle: int, stderr_handle: int) -> None:
    kernel32 = ctypes.windll.kernel32
    kernel32.SetStdHandle.argtypes = [ctypes.c_int, ctypes.c_void_p]
    kernel32.SetStdHandle.restype = ctypes.c_bool
    kernel32.SetStdHandle(-11, ctypes.c_void_p(stdout_handle))
    kernel32.SetStdHandle(-12, ctypes.c_void_p(stderr_handle))


class InternalLogPanel(QWidget):
    """Bounded, copy-friendly bottom log panel."""

    expanded_changed = Signal(bool)
    MINI_HEIGHT = 26

    def __init__(
        self, logger: InternalLogger, parent: QWidget | None = None, *, maximum_lines: int = 5_000
    ) -> None:
        super().__init__(parent)
        # The log is always available: the splitter may reduce it only to the
        # compact status-row height, never to a hidden zero-height pane.
        self.setMinimumHeight(self.MINI_HEIGHT)
        self._expanded = True
        self.entries = QPlainTextEdit(self)
        self.entries.setReadOnly(True)
        self.entries.setUndoRedoEnabled(False)
        self.entries.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.entries.document().setMaximumBlockCount(max(1, int(maximum_lines)))
        self.expanded_button = QToolButton(self.entries)
        self.expanded_button.setAutoRaise(True)
        self.expanded_button.setToolTip("Minimize internal log")
        self.expanded_button.clicked.connect(lambda: self.set_expanded(False))

        self.mini_row = QWidget(self)
        mini_layout = QHBoxLayout(self.mini_row)
        mini_layout.setContentsMargins(5, 0, 3, 0)
        self.last_message = QLabel("Internal log", self.mini_row)
        self.last_message.setWordWrap(False)
        self.mini_button = QToolButton(self.mini_row)
        self.mini_button.setAutoRaise(True)
        self.mini_button.setToolTip("Expand internal log")
        self.mini_button.clicked.connect(lambda: self.set_expanded(True))
        mini_layout.addWidget(self.last_message, stretch=1)
        mini_layout.addWidget(self.mini_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.mini_row)
        layout.addWidget(self.entries)
        logger.message_logged.connect(self.append_message)
        self._apply_expanded_state()

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._apply_expanded_state()
        self.expanded_changed.emit(expanded)

    @property
    def is_expanded(self) -> bool:
        return self._expanded

    def _apply_expanded_state(self) -> None:
        color = self.palette().color(self.foregroundRole())
        self.entries.setVisible(self._expanded)
        self.expanded_button.setVisible(self._expanded)
        self.mini_row.setVisible(not self._expanded)
        # The icon describes the available direction: up expands a collapsed
        # log, down collapses an expanded one.
        self.expanded_button.setIcon(make_expand_icon(False, color))
        self.mini_button.setIcon(make_expand_icon(True, color))
        if self._expanded:
            self.setMinimumHeight(self.MINI_HEIGHT)
            self.setMaximumHeight(16_777_215)
        else:
            self.setFixedHeight(self.MINI_HEIGHT)
        self.updateGeometry()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if self._expanded:
            size = 22
            scrollbar_width = self.entries.verticalScrollBar().sizeHint().width()
            self.expanded_button.setGeometry(
                max(0, self.entries.width() - scrollbar_width - size - 3), 1, size, size
            )
            self.expanded_button.raise_()

    def append_message(self, timestamp: str, message: str, message_type: str = "message") -> None:
        scrollbar = self.entries.verticalScrollBar()
        follow_tail = scrollbar.value() >= scrollbar.maximum()
        # QPlainTextEdit is considerably cheaper than one tree item per log
        # row and retains standard mouse/keyboard selection and Ctrl+C.
        self.entries.appendPlainText(f"{timestamp}  {message}")
        if follow_tail:
            cursor = self.entries.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.entries.setTextCursor(cursor)
        latest = message.splitlines()[-1] if message.splitlines() else message
        self.last_message.setText(f"{timestamp}  {latest}")
        self.last_message.setToolTip(message)
