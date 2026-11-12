"""Synthetic demo data in <repository>/demodata, made by tools/create_demo_dataset.py.

The generator runs as a separate process (it renders videos with its own worker
processes); its output is read on a background thread and reported with signals.
"""
from __future__ import annotations

from collections import deque
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import threading
import time
from typing import Any

from PySide6.QtCore import QObject, Signal

from src.app.settings import REPOSITORY_ROOT

DEMO_DATA_ROOT = REPOSITORY_ROOT / "demodata"
GENERATOR = REPOSITORY_ROOT / "tools" / "create_demo_dataset.py"
PROGRESS_PREFIX = "PROGRESS "


def demo_workspace(root: Path = DEMO_DATA_ROOT) -> Path:
    return root / "demo.umw"


def demo_data_exists(root: Path = DEMO_DATA_ROOT) -> bool:
    """The generator writes demo.umw last, so it marks a complete set."""
    return demo_workspace(root).is_file()


def parse_progress(line: str) -> float | None:
    """The fraction in a generator "PROGRESS <fraction>" line, otherwise None."""
    if not line.startswith(PROGRESS_PREFIX):
        return None
    try:
        return min(1.0, max(0.0, float(line[len(PROGRESS_PREFIX):])))
    except ValueError:
        return None


def is_inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def uses_folder(state: Any, root: Path) -> bool:
    """Whether a card's saved workspace state names a file or folder inside ``root``."""
    if isinstance(state, dict):
        return any(uses_folder(value, root) for value in state.values())
    if isinstance(state, (list, tuple)):
        return any(uses_folder(value, root) for value in state)
    return isinstance(state, str) and Path(state).is_absolute() and is_inside(Path(state), root)


def remove_demo_data(root: Path = DEMO_DATA_ROOT, attempts: int = 5) -> None:
    """Delete the demo data folder, retrying while just-closed cards release their files."""
    if root.is_symlink():
        raise OSError(f"{root} is a link; not deleting it")

    def writable(function, name, _error):  # type: ignore[no-untyped-def]
        Path(name).chmod(stat.S_IWRITE)
        function(name)

    for attempt in range(attempts):
        if not root.exists():
            return
        try:
            shutil.rmtree(root, onerror=writable)
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(1.0)


class DemoDataJob(QObject):
    """Create the demo data in the background; ``replace`` deletes the existing folder first."""

    progress = Signal(float)
    message = Signal(str)
    # Succeeded, and otherwise the error ("" when cancelled).
    finished = Signal(bool, str)

    def __init__(self, root: Path = DEMO_DATA_ROOT, *, replace: bool = False,
                 arguments: tuple[str, ...] = (), parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.root = Path(root)
        self.replace = replace
        self.arguments = arguments
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None  # type: ignore[type-arg]
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="demo-data", daemon=True)
        self._thread.start()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel(self) -> None:
        self._cancelled.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            # The generator's render workers exit when its task pipe closes.
            process.kill()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        output: deque[str] = deque(maxlen=20)
        try:
            if self.replace and self.root.exists():
                self.message.emit(f"Deleting {self.root.name}…")
                remove_demo_data(self.root)
            if self._cancelled.is_set():
                self.finished.emit(False, "")
                return
            command = [sys.executable, "-u", str(GENERATOR), "--output-dir", str(self.root), "--progress",
                       *self.arguments]
            process = subprocess.Popen(
                command, cwd=REPOSITORY_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            with self._lock:
                self._process = process
            if self._cancelled.is_set():
                process.kill()
            assert process.stdout is not None
            with process.stdout:
                for raw_line in process.stdout:
                    line = raw_line.rstrip()
                    fraction = parse_progress(line)
                    if fraction is not None:
                        self.progress.emit(fraction)
                    elif line:
                        output.append(line)
                        self.message.emit(line)
            code = process.wait()
        except OSError as error:
            self.finished.emit(False, str(error))
            return
        if self._cancelled.is_set():
            self.finished.emit(False, "")
        elif code != 0 or not demo_data_exists(self.root):
            self.finished.emit(False, "\n".join(output) or f"The generator stopped with exit code {code}.")
        else:
            self.finished.emit(True, "")
