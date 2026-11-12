"""Shared marker identities and styles for cards and the Plot navigator."""

from PySide6.QtGui import QColor

FIRST_MARKER = 0
SECOND_MARKER = 1
# Left click places the first marker, right click the second. The Settings
# dialog changes these application-wide through `set_marker_style` and
# `set_selection_style`; cards read them whenever they draw.
MARKER_COLORS = ["#ff3b30", "#00c853"]
MARKER_WIDTH = [1]
MARKER_OPACITY = [100]
# A Video card's stored sync data marker, shown dashed on every card until the sync completes.
SYNC_MARKER_COLOR = ["#8e49e6"]
# RGBA fill of the selection between the two markers.
SELECTION_BRUSH = [33, 150, 243, 56]


def set_marker_style(first_color: str, second_color: str, width: int, opacity: int = 100) -> None:
    MARKER_COLORS[:] = [first_color, second_color]
    MARKER_WIDTH[0] = int(width)
    MARKER_OPACITY[0] = max(0, min(100, int(opacity)))


def set_selection_style(color: str, opacity: int) -> None:
    rgb = QColor(color)
    SELECTION_BRUSH[:] = [rgb.red(), rgb.green(), rgb.blue(), round(255 * max(0, min(100, int(opacity))) / 100)]


def set_sync_marker_color(color: str) -> None:
    SYNC_MARKER_COLOR[0] = color


def sync_marker_color() -> str:
    return SYNC_MARKER_COLOR[0]


def marker_width() -> int:
    return MARKER_WIDTH[0]


def marker_color(index: int) -> QColor:
    """A marker's color with the configured opacity."""
    color = QColor(MARKER_COLORS[index])
    color.setAlpha(round(255 * MARKER_OPACITY[0] / 100))
    return color


def selection_rgba(strength: float = 1.0) -> tuple[int, int, int, int]:
    """The selection color; a band drawn over a line uses a stronger alpha to stay visible."""
    red, green, blue, alpha = SELECTION_BRUSH
    return red, green, blue, min(255, round(alpha * strength))
