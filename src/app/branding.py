"""Application branding, version and Windows taskbar identity."""
import ctypes
import logging
from pathlib import Path
import sys

from PySide6.QtGui import QIcon

# The version lives in src/version.py, which worker processes import without Qt.
from src.version import APP_VERSION, version_text  # noqa: F401 - re-exported


APP_NAME = 'LODGaZilla'
APP_ID = 'LODGaZilla.Desktop'
ASSET_ROOT = Path(__file__).resolve().parents[2] / 'assets' / 'lodgazilla'


def lodgazilla_asset_path(theme, kind):
    variant = 'dark' if theme == 'dark' else 'light'
    return ASSET_ROOT / f'LODGaZilla_{kind}_{variant}.png'


def prepare_windows_taskbar():
    """Give Python-hosted windows the application's own taskbar identity."""
    if sys.platform != 'win32':
        return
    function = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
    function.argtypes = [ctypes.c_wchar_p]
    function.restype = ctypes.c_long
    result = function(APP_ID)
    if result < 0:
        logging.getLogger(__name__).warning('Could not set Windows application ID: %s', result)


def configure_application(app, theme):
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(QIcon(str(lodgazilla_asset_path(theme, 'icon'))))
