"""Series line widths shared by every card, and the click distance that selects a series.

Every card draws its series, tracks and lane outlines ``series_line_width()`` pixels wide
and its selected series ``series_line_width(selected=True)`` (Settings > Plot > Cards).
A left click within ``SELECT_TOLERANCE_PIXELS`` of a series selects it; farther away it
selects nothing (``PlotBaseWidget.select_series``).
"""
from __future__ import annotations

SELECT_TOLERANCE_PIXELS = 8.0
# Width of a series, and of the selected one, in pixels.
_WIDTHS = {"normal": 2, "selected": 5}


def series_line_width(selected: bool = False) -> int:
    return _WIDTHS["selected" if selected else "normal"]


def set_series_line_widths(normal: int, selected: int) -> None:
    """Use these widths; open cards follow when their ``apply_series_widths`` runs."""
    _WIDTHS["normal"] = max(1, int(normal))
    _WIDTHS["selected"] = max(1, int(selected))
