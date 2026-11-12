"""The QApplication and the splash screen, shown before LODGaZilla's other modules load.

Only light modules are imported here, so the splash appears about a second
after Python starts. It closes when the main window is shown; the main window
then reports the rest of the loading in its Internal log panel.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from src.app.branding import configure_application, lodgazilla_asset_path, prepare_windows_taskbar
from src.app.graphics import configure_graphics
from src.app.settings import DEFAULT_APP_SETTINGS_PATH, begin_app_settings_cache
from src.app.startup_profile import mark as startup_mark
from src.gui.theme import load_theme

SPLASH_WIDTH = 760

_splash: QSplashScreen | None = None


def start_application() -> tuple[QApplication, QSplashScreen | None]:
    """Create the QApplication and show the splash screen; later calls return them again."""
    global _splash
    app = QApplication.instance()
    if app is not None:
        return app, _splash  # type: ignore[return-value]
    begin_app_settings_cache(DEFAULT_APP_SETTINGS_PATH)
    prepare_windows_taskbar()
    configure_graphics()
    app = QApplication(sys.argv)
    startup_mark("QApplication created")
    theme = load_theme(DEFAULT_APP_SETTINGS_PATH)
    configure_application(app, theme)
    _splash = create_splash_screen(app, theme)
    startup_mark("splash screen shown")
    return app, _splash


def create_splash_screen(app: QApplication, theme: str) -> QSplashScreen:
    pixmap = QPixmap(str(lodgazilla_asset_path(theme, "big")))
    if not pixmap.isNull():
        pixmap = pixmap.scaledToWidth(SPLASH_WIDTH, Qt.TransformationMode.SmoothTransformation)
    splash = QSplashScreen(pixmap)
    splash.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    color = Qt.GlobalColor.white if theme == "dark" else Qt.GlobalColor.black
    splash.showMessage("Starting LODGaZilla…", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, color)
    splash.show()
    app.processEvents()
    return splash
