"""Application palette helpers for the user-selectable theme."""

from __future__ import annotations

import gc

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory
from shiboken6 import isValid

from src.app.settings import APPEARANCE_SECTION, DEFAULT_APP_SETTINGS_PATH, load_settings_section


THEME_OPTIONS = ("os", "light", "dark")
_system_palette: QPalette | None = None
_system_stylesheet: str | None = None
_system_style_name: str | None = None
# The theme last applied; applying it again would repaint every widget for nothing.
_applied_theme: str | None = None


def load_theme(settings_path=DEFAULT_APP_SETTINGS_PATH) -> str:  # type: ignore[no-untyped-def]
    theme = str(load_settings_section(APPEARANCE_SECTION, settings_path).get("theme", "os")).lower()
    return theme if theme in THEME_OPTIONS else "os"


def apply_theme(theme: str, *, force: bool = False) -> bool:
    """Apply a theme to the application; return whether anything changed.

    The same theme again is skipped unless ``force``.
    """
    global _system_palette, _system_stylesheet, _system_style_name, _applied_theme
    app = QApplication.instance()
    if app is None:
        return False
    if theme.lower() == _applied_theme and not force:
        return False
    _applied_theme = theme.lower()
    if _system_palette is None:
        _system_palette = QPalette(app.palette())
    if _system_stylesheet is None:
        _system_stylesheet = app.styleSheet()
    if _system_style_name is None:
        _system_style_name = app.style().objectName()
    theme = theme.lower()
    if theme == "dark":
        app.setStyle(QStyleFactory.create("Fusion"))
        _apply_palette_to_application(app, dark_palette())
    elif theme == "light":
        app.setStyle(QStyleFactory.create("Fusion"))
        _apply_palette_to_application(app, light_palette())
    else:
        app.setStyleSheet(_system_stylesheet)
        if _system_style_name:
            app.setStyle(QStyleFactory.create(_system_style_name))
        _apply_palette_to_application(app, _system_palette)
    return True


def _apply_palette_to_application(app: QApplication, palette: QPalette) -> None:
    """Apply the palette to existing native-style widgets as well as new ones."""
    # Qt visits every widget here. Python's cyclic garbage collector can run at
    # any allocation and destroy a Python-owned widget tree mid-visit, which
    # Qt would then use after it is freed (heap corruption).
    collecting = gc.isenabled()
    gc.disable()
    try:
        app.setPalette(palette)
        for widget in app.allWidgets():
            if isValid(widget):
                widget.setPalette(palette)
    finally:
        if collecting:
            gc.enable()


def light_palette() -> QPalette:
    """Return a deterministic light palette independent of the OS colour scheme."""
    palette = QPalette()
    _set_palette_colors(
        palette,
        {
            QPalette.ColorRole.WindowText: QColor(32, 32, 32),
            QPalette.ColorRole.Button: QColor(240, 240, 240),
            QPalette.ColorRole.Light: QColor(255, 255, 255),
            QPalette.ColorRole.Midlight: QColor(227, 227, 227),
            QPalette.ColorRole.Dark: QColor(160, 160, 160),
            QPalette.ColorRole.Mid: QColor(184, 184, 184),
            QPalette.ColorRole.Text: QColor(32, 32, 32),
            QPalette.ColorRole.BrightText: QColor(200, 0, 0),
            QPalette.ColorRole.ButtonText: QColor(32, 32, 32),
            QPalette.ColorRole.Base: QColor(255, 255, 255),
            QPalette.ColorRole.Window: QColor(245, 245, 245),
            QPalette.ColorRole.Shadow: QColor(105, 105, 105),
            QPalette.ColorRole.Highlight: QColor(30, 120, 210),
            QPalette.ColorRole.HighlightedText: QColor(255, 255, 255),
            QPalette.ColorRole.Link: QColor(0, 90, 180),
            QPalette.ColorRole.LinkVisited: QColor(100, 60, 160),
            QPalette.ColorRole.AlternateBase: QColor(248, 248, 248),
            QPalette.ColorRole.ToolTipBase: QColor(255, 255, 220),
            QPalette.ColorRole.ToolTipText: QColor(32, 32, 32),
            QPalette.ColorRole.PlaceholderText: QColor(112, 112, 112),
            QPalette.ColorRole.Accent: QColor(30, 120, 210),
        },
    )
    for color_role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            color_role,
            QColor(112, 112, 112),
        )
    return palette


def _set_palette_colors(palette: QPalette, colors: dict[QPalette.ColorRole, QColor]) -> None:
    """Set colours for every state, avoiding inherited dark inactive colours."""
    for color_group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        for color_role, color in colors.items():
            palette.setColor(color_group, color_role, color)


def dark_palette() -> QPalette:
    palette = light_palette()
    _set_palette_colors(
        palette,
        {
            QPalette.ColorRole.Window: QColor(45, 45, 45),
            QPalette.ColorRole.WindowText: QColor(235, 235, 235),
            QPalette.ColorRole.Base: QColor(30, 30, 30),
            QPalette.ColorRole.AlternateBase: QColor(45, 45, 45),
            QPalette.ColorRole.ToolTipBase: QColor(235, 235, 235),
            QPalette.ColorRole.ToolTipText: QColor(30, 30, 30),
            QPalette.ColorRole.Text: QColor(235, 235, 235),
            QPalette.ColorRole.PlaceholderText: QColor(165, 165, 165),
            QPalette.ColorRole.Button: QColor(55, 55, 55),
            QPalette.ColorRole.ButtonText: QColor(235, 235, 235),
            QPalette.ColorRole.Light: QColor(75, 75, 75),
            QPalette.ColorRole.Midlight: QColor(65, 65, 65),
            QPalette.ColorRole.Dark: QColor(20, 20, 20),
            QPalette.ColorRole.Mid: QColor(35, 35, 35),
            QPalette.ColorRole.Shadow: QColor(0, 0, 0),
            QPalette.ColorRole.Highlight: QColor(30, 120, 210),
            QPalette.ColorRole.HighlightedText: QColor(255, 255, 255),
        },
    )
    return palette
