"""Low-overhead startup timing written before the normal Qt logger exists."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime

from src.app.log_paths import log_path

_STARTED_AT = time.perf_counter()
_LOCK = threading.Lock()
# Only the start is profiled: later marks, such as every Navigator rebuild, are ignored.
STARTUP_SECONDS = 120.0
# A log that has grown past this is started afresh by the next process.
MAXIMUM_LOG_BYTES = 1_000_000
_checked_size = False


def mark(stage: str) -> None:
    """Append a timestamp and Python-relative elapsed time to the startup log."""
    global _checked_size
    elapsed = time.perf_counter() - _STARTED_AT
    if elapsed > STARTUP_SECONDS:
        return
    timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
    line = f"[{timestamp}] +{elapsed:8.3f}s python: {stage} (pid={os.getpid()})\n"
    path = log_path("startup.log")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            mode = "a"
            if not _checked_size:
                _checked_size = True
                if path.is_file() and path.stat().st_size > MAXIMUM_LOG_BYTES:
                    mode = "w"
        with _LOCK, path.open(mode, encoding="utf-8") as log_file:
            log_file.write(line)
            log_file.flush()
    except OSError:
        pass