"""Marker and selection code shared by cards, so each card keeps only its own drawing.

`TimeCardMarkers`: cards drawn on an `InteractivePlotWidget` time axis (Line, Event/state,
Frequency); the canvas owns the markers, the selection and the time axis.

`PointMarkers`: cards that show a marker at the point nearest its time (X/Y, 3D plot, Map);
the card keeps the two marker times and draws them itself.
"""
from __future__ import annotations

from src.gui.plots.markers import markers_shown
from src.gui.plots.markers import FIRST_MARKER, SECOND_MARKER


class TimeCardMarkers:
    """Markers, snapping and time axis of a card on an `InteractivePlotWidget`.

    The card provides ``marker_canvas()`` (default ``self.plot_widget``) and
    ``marker_value_boxes()`` (default ``self.value_boxes``).
    """

    def marker_canvas(self):  # type: ignore[no-untyped-def]
        return self.plot_widget

    def marker_value_boxes(self):  # type: ignore[no-untyped-def]
        return self.value_boxes

    def set_vertical_marker(self, x_value: float, index: int = FIRST_MARKER) -> None:
        self.marker_canvas().set_vertical_marker(x_value, index)

    def clear_vertical_marker(self, index: int = FIRST_MARKER) -> None:
        self.marker_canvas().clear_vertical_marker(index)

    def set_sync_marker(self, x_value: float | None) -> None:
        self.marker_canvas().set_sync_marker(x_value)

    def apply_marker_style(self) -> None:
        self.marker_canvas().apply_marker_style()
        self.marker_value_boxes().apply_style()

    def marker_values(self) -> tuple[float | None, float | None]:
        canvas = self.marker_canvas()
        return canvas.marker_value(FIRST_MARKER), canvas.marker_value(SECOND_MARKER)

    def set_marker_snap_enabled(self, enabled: bool) -> None:
        self.marker_canvas().snap_marker_to_nearest_point = bool(enabled)

    def time_axis_representation(self) -> str:
        """Real date/time or time from log start; saved in workspaces by the base card."""
        return self.marker_canvas().time_axis.representation

    def set_time_axis_representation(self, representation: str) -> None:
        self.marker_canvas().time_axis.set_representation(representation)


class PointMarkers:
    """Two marker times and their selection, on a card that draws markers at points.

    The card calls ``_init_point_markers()`` and provides ``_redraw_markers()`` (draw both
    markers and the sync marker from ``_marker_times`` and ``_sync_time``),
    ``_update_selection()``, ``hide_marker_data(index)`` and the marker signals.
    """

    def _init_point_markers(self) -> None:
        self._marker_times: dict[int, float | None] = {FIRST_MARKER: None, SECOND_MARKER: None}
        self._sync_time: float | None = None

    def set_vertical_marker(self, time_value: float, index: int = FIRST_MARKER) -> None:
        self._marker_times[index] = float(time_value)
        self._redraw_markers()
        self._update_selection()

    def clear_vertical_marker(self, index: int = FIRST_MARKER) -> None:
        self._marker_times[index] = None
        self._redraw_markers()
        self.hide_marker_data(index)
        self._update_selection()

    def marker_values(self) -> tuple[float | None, float | None]:
        return self._marker_times[FIRST_MARKER], self._marker_times[SECOND_MARKER]

    def selection_range(self) -> tuple[float, float] | None:
        times = self.marker_values()
        return None if None in times else tuple(sorted(times))

    def set_sync_marker(self, time_value: float | None) -> None:
        """Show a Video card's stored sync data marker on each graph, trace or track."""
        self._sync_time = time_value
        self._redraw_markers()

    def _place_marker_from_view(self, index: int, time_value: float) -> None:
        """A click placed a marker: show it and tell the workspace (not while markers are hidden)."""
        if not markers_shown():
            return
        self.set_vertical_marker(time_value, index)
        (self.marker_placed if index == FIRST_MARKER else self.second_marker_placed).emit(time_value)

    def _clear_marker_from_view(self, index: int) -> None:
        """A double click removed a marker: hide it and tell the workspace."""
        if self._marker_times[index] is None:
            return
        self.clear_vertical_marker(index)
        (self.marker_cleared if index == FIRST_MARKER else self.second_marker_cleared).emit()
