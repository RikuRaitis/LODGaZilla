"""Sample loss and Clipping areas: one item per series and kind, painting only what is in view.

A long log can have many thousands of loss or clipping ranges. Each range used to be its own
`pg.LinearRegionItem`, all re-checked on every view change. `DiagnosticAreas` keeps the ranges
in one array and paints the visible ones, each at least a few pixels wide; zoomed far out,
ranges closer together than a pixel merge into one column.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor

SAMPLE_LOSS = "sample_loss"
CLIPPING = "clipping"
# Fill colors of the two kinds; a card with several series adds a band in the series' color.
AREA_COLORS = {SAMPLE_LOSS: (220, 70, 70), CLIPPING: (245, 170, 45)}
AREA_ALPHA = 65
SERIES_BAND_PIXELS = 3


def visible_spans(ranges: np.ndarray, left: float, right: float, minimum_span: float,
                  pixel_width: float) -> np.ndarray:
    """The (start, stop) spans to paint between ``left`` and ``right``.

    Each range is widened about its center to at least ``minimum_span``. When there are more
    visible ranges than pixel columns, ranges in the same column of ``pixel_width`` merge.
    """
    ranges = np.asarray(ranges, dtype=np.float64).reshape(-1, 2)
    if not len(ranges) or right <= left:
        return np.empty((0, 2))
    centers = ranges.mean(axis=1)
    widths = np.maximum(ranges[:, 1] - ranges[:, 0], minimum_span)
    spans = np.column_stack((centers - widths / 2, centers + widths / 2))
    spans = spans[(spans[:, 1] >= left) & (spans[:, 0] <= right)]
    if pixel_width > 0 and len(spans) > (right - left) / pixel_width:
        # More ranges than pixels: one span per pixel column is enough to draw.
        columns = np.floor((spans.mean(axis=1) - left) / pixel_width)
        _, first = np.unique(columns, return_index=True)
        merged = []
        for index, start in enumerate(first):
            stop = first[index + 1] if index + 1 < len(first) else len(spans)
            merged.append((spans[start:stop, 0].min(), spans[start:stop, 1].max()))
        spans = np.asarray(merged)
    return spans


class DiagnosticAreas(pg.GraphicsObject):
    """One series' areas of one kind, behind its curve.

    ``value_range`` gives the Y span (in view coordinates) to cover, or None for the full height.
    """

    def __init__(self, kind: str, value_range: Callable[[], tuple[float, float] | None]) -> None:
        super().__init__()
        self.kind = kind
        self.value_range = value_range
        self.ranges = np.empty((0, 2))
        self.minimum_pixels = 5
        self.series_color: str | None = None
        self._view = None
        # Above the ViewBox background, below the curves and point markers.
        self.setZValue(1)

    def set_ranges(self, ranges: np.ndarray) -> None:
        self.ranges = np.asarray(ranges, dtype=np.float64).reshape(-1, 2)
        self.update()

    def set_minimum_pixels(self, pixels: int) -> None:
        self.minimum_pixels = max(0, int(pixels))
        self.update()

    def set_series_color(self, color: str | None) -> None:
        """A band in the series' color along the bottom, telling series apart; None for none."""
        self.series_color = color
        self.update()

    def viewTransformChanged(self) -> None:  # noqa: N802 - pyqtgraph's name
        # Only the visible part is painted, so the bounds follow the view (range or size).
        super().viewTransformChanged()
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt's name
        view = self.getViewBox()
        return QRectF(view.viewRect()) if view is not None else QRectF()

    def paint(self, painter, *_args) -> None:  # type: ignore[no-untyped-def]
        view = self.getViewBox()
        if view is None or not len(self.ranges):
            return
        rect = view.viewRect()
        pixel_width, pixel_height = (abs(float(value)) for value in view.viewPixelSize())
        spans = visible_spans(self.ranges, rect.left(), rect.right(), pixel_width * self.minimum_pixels, pixel_width)
        if not len(spans):
            return
        value_range = self.value_range()
        bottom, top = sorted(value_range) if value_range is not None else (rect.top(), rect.bottom())
        painter.setPen(pg.mkPen(None))
        painter.setBrush(pg.mkBrush(*AREA_COLORS[self.kind], AREA_ALPHA))
        painter.drawRects([QRectF(start, bottom, stop - start, top - bottom) for start, stop in spans])
        if self.series_color is not None:
            band = min(top - bottom, SERIES_BAND_PIXELS * pixel_height)
            painter.setBrush(pg.mkBrush(QColor(self.series_color)))
            painter.drawRects([QRectF(start, bottom, stop - start, band) for start, stop in spans])
