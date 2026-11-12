"""Low-overhead startup timing written before the normal Qt logger exists."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime

from src.app.log_paths import log_path

_STARTED_AT = time.perf_counter()
_LOCK = threading.Lock()


def mark(stage: str) -> None:
    """Append a timestamp and Python-relative elapsed time to the startup log."""
    elapsed = time.perf_counter() - _STARTED_AT
    timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
    line = f"[{timestamp}] +{elapsed:8.3f}s python: {stage} (pid={os.getpid()})\n"
    path = log_path("startup.log")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, path.open("a", encoding="utf-8") as log_file:
            log_file.write(line)
            log_file.flush()
    except OSError:
        pass