"""Apply the active Qt palette to PyQtGraph plot widgets."""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtGui import QColor, QPalette


def canvas_background(widget) -> QColor:  # type: ignore[no-untyped-def]
    """Use black for dark canvases and the palette base for light canvases."""
    base = widget.palette().color(QPalette.ColorRole.Base)
    return QColor("black") if base.lightness() < 128 else base


def apply_map_background(widget) -> None:  # type: ignore[no-untyped-def]
    """Match the map's loading surface and uncovered page to other canvases."""
    color = canvas_background(widget)
    widget.map_container.setStyleSheet(f"background: {color.name()};")
    if widget.map_view is not None:
        widget.map_view.page().setBackgroundColor(color)
        widget.map_view.page().runJavaScript(
            "document.querySelectorAll('html,body,#map').forEach("
            f"element => element.style.backgroundColor = '{color.name()}');"
        )


def apply_plot_palette(plot_widget: pg.PlotWidget) -> None:
    """Synchronise PyQtGraph's independent colours with the Qt application palette."""
    palette = plot_widget.palette()
    base = canvas_background(plot_widget)
    text = palette.color(QPalette.ColorRole.Text)
    grid = palette.color(QPalette.ColorRole.Mid)
    plot_widget.setBackground(base)
    plot_item = plot_widget.getPlotItem()
    for axis_name in ("left", "right", "top", "bottom"):
        axis = plot_item.getAxis(axis_name)
        axis.setPen(grid)
        axis.setTextPen(text)
        axis.label.setDefaultTextColor(text)
    plot_item.titleLabel.setText(plot_item.titleLabel.text, color=text.name())
    plot_widget.viewport().update()


def palette_popup_stylesheet() -> str:
    """Palette-based overlay styling that remains valid after a theme switch."""
    return (
        "QTextBrowser { background: palette(base); color: palette(text); "
        "border: 1px solid palette(mid); padding: 3px 6px; }"
    )


def palette_overlay_stylesheet() -> str:
    """Palette-based style for GNSS map controls placed over a WebEngine view."""
    return (
        "QWidget { background: palette(button); color: palette(button-text); "
        "border: 1px solid palette(mid); }"
    )
