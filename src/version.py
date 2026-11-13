"""LODGaZilla's version: the single source of truth, without Qt.

``APP_VERSION`` follows Semantic Versioning, MAJOR.MINOR.PATCH with an optional
pre-release such as ``1.0.0-beta.1``; when to raise which number is in
docs/Releasing.md. Worker processes and saved files use it too, so this module
imports nothing heavy.
"""
from __future__ import annotations

import functools
import re
import subprocess
from pathlib import Path

# The released version; raise it for each release (docs/Releasing.md).
APP_VERSION = "1.0.0-beta.3"

# Semantic Versioning 2.0.0, without build metadata: 1.2.3, 1.0.0-beta.1, 1.0.0-rc.2.
VERSION_PATTERN = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(-(0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(\.(0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@functools.lru_cache(maxsize=1)
def version_text() -> str:
    """The version as the About dialog and log show it.

    A release tag's own commit gives just the version, such as ``1.0.0``. Any
    other Git checkout adds ``git describe``: ``1.0.0 (v1.0.0-5-g9850d27-dirty)``
    is five commits after v1.0.0 with uncommitted changes. Without Git it is the
    plain version. Git can take most of a second, so the result is kept.
    """
    try:
        described = subprocess.run(
            ["git", "-C", str(REPOSITORY_ROOT), "describe", "--tags", "--always", "--dirty"],
            capture_output=True, text=True, timeout=5, check=False, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        described = ""
    if not described or described == f"v{APP_VERSION}":
        return APP_VERSION
    return f"{APP_VERSION} ({described})"
