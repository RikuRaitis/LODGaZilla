"""Where LODGaZilla writes its log files.

The application writes to logs/ in the checkout. A test run writes to a
temporary folder instead, so tests add no noise to the application's logs.
LODGAZILLA_LOG_DIR overrides both; worker processes inherit it.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

LOG_DIR_ENV = "LODGAZILLA_LOG_DIR"
APPLICATION_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
TEST_LOG_DIR = Path(tempfile.gettempdir()) / "lodgazilla_test_logs"


def log_directory() -> Path:
    configured = os.environ.get(LOG_DIR_ENV)
    if configured:
        return Path(configured)
    # The application never imports unittest; a test run always has.
    if "unittest" in sys.modules:
        os.environ[LOG_DIR_ENV] = str(TEST_LOG_DIR)
        return TEST_LOG_DIR
    return APPLICATION_LOG_DIR


def log_path(name: str) -> Path:
    return log_directory() / name
