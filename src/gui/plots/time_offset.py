"""Linked time views that respect each card's display time offset.

A card's time offset only moves what it shows: shared time = card time + offset.
Cards keep their own data coordinates; linked X ranges are shifted by the
difference between the two cards' offsets, so one screen position shows the
same shared time on every linked card.
"""

from __future__ import annotations

import types

import pyqtgraph as pg

_OFFSET_ATTRIBUTE = "_lodgazilla_time_offset"


def view_box_time_offset(view_box) -> float:  # type: ignore[no-untyped-def]
    return float(getattr(view_box, _OFFSET_ATTRIBUTE, 0.0))


def install_offset_x_link(view_box) -> None:  # type: ignore[no-untyped-def]
    """Make this view box follow a linked X range shifted by the offset difference."""
    if getattr(view_box, "_lodgazilla_offset_link", False):
        return
    view_box._lodgazilla_offset_link = True
    view_box.linkedViewChanged = types.MethodType(_offset_linked_view_changed, view_box)


def set_view_box_time_offset(view_box, seconds: float) -> None:  # type: ignore[no-untyped-def]
    install_offset_x_link(view_box)
    setattr(view_box, _OFFSET_ATTRIBUTE, float(seconds))


def _offset_linked_view_changed(self, view, axis) -> None:  # type: ignore[no-untyped-def]
    """pyqtgraph's X link alignment, plus the offset difference between the two views."""
    if axis != pg.ViewBox.XAxis or self.linksBlocked or view is None:
        pg.ViewBox.linkedViewChanged(self, view, axis)
        return
    shift = view_box_time_offset(view) - view_box_time_offset(self)
    rect, view_geometry, own_geometry = view.viewRect(), view.screenGeometry(), self.screenGeometry()
    if view_geometry is None or own_geometry is None:
        return
    view.blockLink(True)
    try:
        overlap = min(own_geometry.right(), view_geometry.right()) - max(own_geometry.left(), view_geometry.left())
        if overlap < min(view_geometry.width() / 3, own_geometry.width() / 3):
            left, right = rect.left(), rect.right()
        else:
            units_per_pixel = float(rect.width()) / view_geometry.width()
            if self.xInverted():
                left = rect.left() + (own_geometry.right() - view_geometry.right()) * units_per_pixel
            else:
                left = rect.left() + (own_geometry.x() - view_geometry.x()) * units_per_pixel
            right = left + own_geometry.width() * units_per_pixel
        self.enableAutoRange(pg.ViewBox.XAxis, False)
        self.setXRange(left + shift, right + shift, padding=0)
    finally:
        view.blockLink(False)
