"""Theme-aware access to the bundled plot-control SVG icons."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap


_ICON_ROOT = Path(__file__).resolve().parents[3] / "assets" / "plot_icons"
# The stroke colors of the bundled light and dark icon variants.
ICON_COLORS = {"light": "#334155", "dark": "#E2E8F0"}
# A plugin launcher without its own icon shows this bundled one.
DEFAULT_PLUGIN_ICON = "plugin"


def _variant(widget) -> str:  # type: ignore[no-untyped-def]
    background = widget.palette().color(widget.backgroundRole())
    return "dark" if background.lightness() < 128 else "light"


def plot_control_icon(widget, name: str) -> QIcon:  # type: ignore[no-untyped-def]
    """Return the light or dark SVG variant appropriate for *widget*."""
    return QIcon(str(_ICON_ROOT / _variant(widget) / f"{name}.svg"))


def plugin_icon(widget, path: Path | None, size: int = 64) -> QIcon:  # type: ignore[no-untyped-def]
    """A plugin's launcher SVG, its ``currentColor`` drawn in the bundled icons' color for *widget*'s theme."""
    if path is None:
        return plot_control_icon(widget, DEFAULT_PLUGIN_ICON)
    from PySide6.QtSvg import QSvgRenderer

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return plot_control_icon(widget, DEFAULT_PLUGIN_ICON)
    renderer = QSvgRenderer(QByteArray(text.replace("currentColor", ICON_COLORS[_variant(widget)]).encode("utf-8")))
    if not renderer.isValid():
        return plot_control_icon(widget, DEFAULT_PLUGIN_ICON)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)
