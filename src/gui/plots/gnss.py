"""Leaflet-backed GNSS map plot card."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import h5py
import numpy as np
from PySide6.QtCore import QEvent, QObject, Qt, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QHBoxLayout, QLabel, QRadioButton, QStackedLayout, QWidget,
)

from src.data.axis_scale import coordinate_scale, declared_coordinate_scale, is_latitude_name, is_longitude_name
from src.gui.plots.base import PlotBaseWidget, series_from_mime
from src.gui.plots.card_markers import PointMarkers
from src.gui.plots.legend import PlotLegendWidget
from src.gui.plots.legend_groups import GROUP_COLORS, SeriesGroup, group_display_name
from src.gui.plots.series_hover import HIT_TOLERANCE_PIXELS, SeriesNameHover
from src.data.sampling import decimation_step
from src.data.xyz import AxisSource, _aligned_values
from src.jobs.plot_groups import GroupedPlotLoadCoordinator
from src.gui.plots.theme import apply_map_background, canvas_background, palette_overlay_stylesheet
from src.gui.plots.marker_info import MarkerInfoEntry
from src.gui.plots.markers import (
    FIRST_MARKER, SECOND_MARKER, sync_marker_color, marker_color, marker_width, selection_rgba,
)
from src.gui.plots.value_boxes import ValueBoxItem, ValueBoxLayer
from src.data.h5_series import (
    _canonical_to_native_range,
    _navigator_series_names,
    _visible_sample_bounds,
    series_value_range,
)
from src.gui.plots.series import (
    _attribute_text,
    _series_group_for_navigator_name,
    _time_unit_seconds,
    _canonical_time_values, _time_axis_metadata_from_group,
)


class GnssBridge(QObject):
    point_selected = Signal(float)
    marker_clear_requested = Signal()
    second_point_selected = Signal(float)
    second_marker_clear_requested = Signal()
    view_changed = Signal()
    # The map's camera as JSON, for saving it in a workspace file.
    view_reported = Signal(str)
    scroll_requested = Signal(int)

    @Slot(float)
    def select_timestamp(self, timestamp: float) -> None:
        self.point_selected.emit(timestamp)

    @Slot()
    def clear_marker(self) -> None:
        self.marker_clear_requested.emit()

    @Slot(float)
    def select_second_timestamp(self, timestamp: float) -> None:
        self.second_point_selected.emit(timestamp)

    @Slot()
    def clear_second_marker(self) -> None:
        self.second_marker_clear_requested.emit()

    @Slot()
    def notify_view_changed(self) -> None:
        self.view_changed.emit()

    @Slot(str)
    def report_view(self, view: str) -> None:
        self.view_reported.emit(view)

    @Slot(int)
    def scroll_workspace(self, delta: int) -> None:
        self.scroll_requested.emit(delta)


class GnssMapView(QWebEngineView):
    """Map view that forwards workspace scrolling and GNSS-series drops."""

    wheel_scrolled = Signal(int)

    def __init__(self, gnss_plot: GnssPlotWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.gnss_plot = gnss_plot
        self.setAcceptDrops(True)

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            super().wheelEvent(event)
            return
        self.wheel_scrolled.emit(event.angleDelta().y())
        event.accept()

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        series = series_from_mime(event.mimeData())
        if series is not None and self.gnss_plot.accepts_series(*series):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        series = series_from_mime(event.mimeData())
        if series is None or not self.gnss_plot.accepts_series(*series):
            event.ignore()
            return
        if getattr(event.source(), "source_plot", None) is self.gnss_plot:
            event.ignore()
            return
        self.gnss_plot.series_dropped.emit(self.gnss_plot, *series)
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()


# Default points per map track; the card's Sample limit selector can change it.
MAP_MAXIMUM_POINTS = 20_000


def page_positions(result) -> list:  # type: ignore[no-untyped-def]
    """A map page's ``markerScreenPositions`` result, sent as JSON text or as a list, as a list."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return []
    return result if isinstance(result, list) else []


class MapMarkersMixin(PointMarkers):
    """Two markers, the selection and values boxes for a web map card.

    The card calls ``_init_point_markers()`` and provides ``_track_data`` (tracks with
    ``time``, ``lat`` and ``lon`` lists, in the page's track order), ``map_view``,
    ``map_container``, ``value_boxes``, ``_map_generation``, the marker signals,
    ``_value_box_times``, ``_marker_revision``, ``_flush_marker()`` and
    ``_markers_ready()``. Its page provides ``setMarkers`` and ``markerScreenPositions``.
    """

    def _redraw_markers(self) -> None:
        self._marker_revision += 1
        self._flush_marker()

    def apply_marker_style(self) -> None:
        self._redraw_markers()
        self.value_boxes.apply_style()

    def hide_marker_data(self, index: int | None = None) -> None:
        for marker in (FIRST_MARKER, SECOND_MARKER) if index is None else (index,):
            self._value_box_times[marker] = None
            self.value_boxes.clear(marker)

    def _track_samples(self, timestamp: float):  # type: ignore[no-untyped-def]
        """For each displayed track, the index of its sample nearest a marker time."""
        for track_index, track in enumerate(self._track_data):
            times = np.asarray(track.get("time", []), dtype=float)
            if not len(times) or timestamp < times[0] or timestamp > times[-1]:
                continue
            sample_index = int(np.argmin(np.abs(times - timestamp)))
            if sample_index < len(track.get("lat", [])) and sample_index < len(track.get("lon", [])):
                yield track_index, track, sample_index

    def show_marker_data(self, timestamp: float, index: int = FIRST_MARKER) -> None:
        """Show one values box per track, beside its point nearest the marker.

        Before the tracks load, the time is kept; loading the tracks places its boxes."""
        if self._track_data and not any(True for _ in self._track_samples(timestamp)):
            self.hide_marker_data(index)
            return
        self._value_box_times[index] = float(timestamp)
        self._request_value_box_positions(index)

    def _request_value_box_positions(self, index: int) -> None:
        timestamp = self._value_box_times[index]
        if timestamp is None or not self._markers_ready() or self.map_view is None:
            return
        generation = self._map_generation
        # As JSON text: PySide6 6.11 hands a JavaScript array to the callback as an empty string.
        self.map_view.page().runJavaScript(
            f"JSON.stringify(markerScreenPositions({timestamp!r}));",
            lambda positions: self._show_value_boxes(generation, index, timestamp, page_positions(positions)),
        )

    def _refresh_value_box_positions(self) -> None:
        for index in (FIRST_MARKER, SECOND_MARKER):
            self._request_value_box_positions(index)

    def _show_value_boxes(self, generation, index, timestamp, positions) -> None:  # type: ignore[no-untyped-def]
        if generation != self._map_generation or self._value_box_times[index] != timestamp or self.map_view is None:
            return
        offset = self.map_view.mapTo(self.map_container, self.map_view.rect().topLeft())
        items = []
        for track_index, track, sample_index in self._track_samples(timestamp):
            position = positions[track_index] if isinstance(positions, list) and track_index < len(positions) else None
            if not isinstance(position, list) or len(position) != 2:
                continue
            items.append(ValueBoxItem(
                self._track_key(track_index), self._track_entry(track_index, track, sample_index), None,
                anchor=(float(position[0]) + offset.x(), float(position[1]) + offset.y()),
            ))
        self.value_boxes.set_items(index, timestamp, items)

    def _track_key(self, track_index: int) -> tuple:
        group = self.grouped_series.groups[track_index] if track_index < len(self.grouped_series.groups) else None
        return tuple((axis.path, axis.name) for axis in group.axes if axis is not None) if group else (track_index,)

    def _track_entry(self, track_index: int, track, sample_index: int) -> MarkerInfoEntry:  # type: ignore[no-untyped-def]
        sample_time = float(track["time"][sample_index])
        device_times = track.get("device_time", [])
        device_time = float(device_times[sample_index]) if sample_index < len(device_times) else sample_time
        date_time = (
            datetime.fromtimestamp(sample_time).astimezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            if sample_time > 1_000_000_000
            else "Unavailable (cache has no log start time)"
        )
        latitude, longitude = float(track["lat"][sample_index]), float(track["lon"][sample_index])
        groups = self.grouped_series.groups
        return MarkerInfoEntry(
            str(track.get("name", "GNSS")), self._track_color(track_index),
            f"{latitude:.7f}°, {longitude:.7f}°",
            (
                f"Date/time: {date_time}",
                f"Device time: {device_time:.3f} {track.get('time_unit', 's')}",
                f"Latitude: {latitude:.7f}°",
                f"Longitude: {longitude:.7f}°",
            ),
            full_name=group_display_name(groups[track_index]) if track_index < len(groups) else "",
        )

    def _track_color(self, track_index: int) -> str:
        groups = self.grouped_series.groups
        return groups[track_index].color if track_index < len(groups) else GROUP_COLORS[track_index % len(GROUP_COLORS)]

    def _update_selection(self) -> None:
        """Selection Δt, drawn points and distance along each track for the expanded boxes."""
        times = self.marker_values()
        if None in times:
            self.value_boxes.set_selection_duration(None)
            return
        left, right = sorted(times)
        self.value_boxes.set_selection_duration(right - left)
        statistics = {}
        for track_index, track in enumerate(self._track_data):
            track_times = np.asarray(track.get("time", []), dtype=float)
            inside = (track_times >= left) & (track_times <= right)
            latitudes = np.asarray(track.get("lat", []), dtype=float)[inside[:len(track.get("lat", []))]]
            longitudes = np.asarray(track.get("lon", []), dtype=float)[inside[:len(track.get("lon", []))]]
            statistics[self._track_key(track_index)] = (
                f"Points: {len(latitudes):,} (drawn)",
                f"Distance: {track_distance_m(latitudes, longitudes):.1f} m (along drawn points)",
            )
        self.value_boxes.set_statistics(statistics)


class GnssPlotWidget(MapMarkersMixin, PlotBaseWidget):
    """Map card with street/satellite tiles and one or more GNSS tracks."""
    help_topic = "gnss"

    supports_horizontal_layout = True
    accepts_series_drop = True
    default_sample_limit = MAP_MAXIMUM_POINTS
    sample_limit_noun = "track"
    # Auto and Lines draw each track as a line; Points draws its positions.
    supports_draw_style = True

    marker_placed = Signal(float)
    marker_cleared = Signal()
    second_marker_placed = Signal(float)
    second_marker_cleared = Signal()

    def __init__(
        self, h5_path: Path, series_name: str, parent=None, *, time_range=None, coordinate_pair=None,
        initial_axes=None,
    ) -> None:  # type: ignore[no-untyped-def]
        if initial_axes is None:
            pair = coordinate_pair or gnss_pair_for_series(h5_path, series_name)
            if pair is None:
                raise ValueError(f"No latitude/longitude partner found for {series_name}")
            axes = (AxisSource(pair[0], pair[1]), AxisSource(pair[2], pair[3]))
        else:
            axes = tuple(initial_axes)
            if len(axes) != 2:
                raise ValueError("A GNSS track needs two axis slots")
        super().__init__(h5_path, series_name, parent)
        self.configure_legend_groups(("Lat", "Lon"), title="Track")
        self.grouped_series.groups.append(SeriesGroup(axes, GROUP_COLORS[0], tuple(axis for axis in axes if axis)))
        self.series = self.grouped_series.entries()
        self.loader = GroupedPlotLoadCoordinator(self, load_gnss_group)
        self.loader.loaded.connect(self._loaded_tracks)
        self._loading = False
        self.time_range = time_range
        self._track_data: list[dict[str, list[float]]] = []
        # Each drawn track's sources, for the name shown while the pointer rests on it.
        self._track_names: list[str] = []
        self._name_hover: SeriesNameHover | None = None
        self.map_container = QWidget(self)
        self.map_container.setStyleSheet(f"background: {canvas_background(self).name()};")
        self.map_stack = QStackedLayout(self.map_container)
        self.map_stack.setContentsMargins(0, 0, 0, 0)
        self.map_view: GnssMapView | None = None
        # The camera last reported by the page, and a saved one waiting for the first tracks.
        self._map_view_state: dict | None = None
        self._pending_map_view: dict | None = None
        self._map_ready = False
        self._map_failed = False
        self._map_generation = 0
        self._disposed = False
        self._marker_revision = 0
        self._marker_in_flight = False
        self.map_loading = QLabel("Loading map…", self.map_container)
        self.map_loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map_stack.addWidget(self.map_loading)
        self.map_stack.setCurrentWidget(self.map_loading)
        self.content_layout.addWidget(self.map_container, stretch=1)
        # Street/Satellite sits in the card's header row, left of its other controls.
        self.map_type = QWidget(self)
        type_layout = QHBoxLayout(self.map_type)
        type_layout.setContentsMargins(5, 0, 5, 0)
        self.street_radio = QRadioButton("Street", self.map_type)
        self.satellite_radio = QRadioButton("Satellite", self.map_type)
        self.street_radio.setChecked(True)
        type_buttons = QButtonGroup(self.map_type)
        type_buttons.addButton(self.street_radio)
        type_buttons.addButton(self.satellite_radio)
        self.street_radio.toggled.connect(lambda checked: checked and self._set_map_type("Street"))
        self.satellite_radio.toggled.connect(lambda checked: checked and self._set_map_type("Satellite"))
        type_layout.addWidget(self.street_radio)
        type_layout.addWidget(self.satellite_radio)
        # A metric scale bar at the map's bottom right, for the current zoom; in its own panel,
        # like Street/Satellite.
        self.scale_bar_panel = QWidget(self)
        scale_layout = QHBoxLayout(self.scale_bar_panel)
        scale_layout.setContentsMargins(5, 0, 5, 0)
        self.scale_bar_check = QCheckBox("Scale bar", self.scale_bar_panel)
        self.scale_bar_check.setChecked(True)
        self.scale_bar_check.setToolTip("Scale bar")
        self.scale_bar_check.toggled.connect(self._set_scale_bar)
        scale_layout.addWidget(self.scale_bar_check)
        # Both panels are as tall as the card's other header controls (the Draw style buttons
        # and the Sample limit selector).
        for panel in (self.map_type, self.scale_bar_panel):
            panel.setStyleSheet(palette_overlay_stylesheet())
            panel.setFixedHeight(self.close_button.height() - 2)
            panel.adjustSize()
            self.header_controls.append(panel)
        # Keep the legend a sibling of the WebEngine view.  A child overlay of
        # QWebEngineView can be drawn above Chromium while still letting its
        # mouse events fall through to the web page.
        self.legend = PlotLegendWidget(self.map_container, top_offset=MAP_LEGEND_TOP)
        self.legend.set_group_model(self.grouped_series)
        # Values boxes are siblings of the web view, like the legend, so they draw above it.
        self.value_boxes = ValueBoxLayer(self.map_container)
        self._init_point_markers()
        self._value_box_times: dict[int, float | None] = {FIRST_MARKER: None, SECOND_MARKER: None}
        for widget in (self.map_container, self.map_type, self.legend):
            widget.installEventFilter(self)
        self.on_series_changed()
        self._initialize_map()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if hasattr(self, "legend"):
            self.legend.reposition()
        if hasattr(self, "value_boxes"):
            self._refresh_value_box_positions()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        if event.type() == QEvent.Type.Wheel and watched in {
            self.map_container, self.map_type, self.legend
        }:
            if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.wheel_scrolled.emit(event.angleDelta().y())
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def add_series(self, h5_path: Path, series_name: str) -> None:
        self.insert_series(h5_path, series_name, len(self.grouped_series.groups))

    @property
    def tracks(self):
        return [(group.axes[0].path, group.axes[0].name, group.axes[1].path, group.axes[1].name)
                for group in self.grouped_series.groups if all(axis is not None for axis in group.axes)]

    @property
    def _track_colors(self):
        return [group.color for group in self.grouped_series.groups]

    def insert_series(self, h5_path: Path, series_name: str, index: int) -> None:
        pair = gnss_pair_for_series(h5_path, series_name)
        if pair is not None and pair not in self.tracks:
            self.add_legend_group((AxisSource(pair[0], pair[1]), AxisSource(pair[2], pair[3])), index)

    def accepts_series(self, h5_path: Path, series_name: str) -> bool:
        """Accept only entries that can resolve to a latitude/longitude pair."""
        try:
            return gnss_pair_for_series(h5_path, series_name) is not None
        except OSError:
            return False

    def remove_series_at(self, index: int) -> None:
        """Remove one GNSS track and close an empty map card."""
        self.grouped_series.remove(index)

    def apply_theme(self) -> None:
        """Refresh Qt overlays and WebEngine's initial page background."""
        apply_map_background(self)
        self.map_type.setStyleSheet(palette_overlay_stylesheet())
        self.scale_bar_panel.setStyleSheet(palette_overlay_stylesheet())
        self.value_boxes.apply_style()

    def on_series_changed(self) -> None:
        if hasattr(self, "value_boxes"):
            # The boxes return on the reloaded tracks (Sample limit, trim): keep their times.
            for marker in (FIRST_MARKER, SECOND_MARKER):
                self.value_boxes.clear(marker)
        self._loading = True
        self.loader.request([group.axes for group in self.grouped_series.groups], self.time_range,
                            self.sample_limit)

    def _loaded_tracks(self, results):
        self._loading = False
        self.results = results
        self.update_sample_coverage(results)
        fit = not self._track_data
        self._track_data = []
        self._track_names = []
        for index, track in enumerate(results):
            if isinstance(track, dict):
                track["color"] = self.grouped_series.groups[index].color
                self._track_data.append(track)
                self._track_names.append(group_display_name(self.grouped_series.groups[index]))
        if self.map_view is not None:
            if self._map_ready:
                self._push_tracks(fit)
                self._marker_revision += 1
                self._flush_marker()
            elif self._map_failed:
                self._load_map()
            # Otherwise the loading page receives the tracks once it is ready.
        self._update_selection()
        self._refresh_value_box_positions()

    def _push_tracks(self, fit: bool) -> None:
        """Send tracks to the ready page; ``setHtml`` is limited to 2 MB."""
        script = ""
        if fit and getattr(self, "_pending_map_view", None) is not None and self._track_data:
            script, self._pending_map_view = f"restoreView={json.dumps(self._pending_map_view)};", None
        # A new page starts in Auto; another style is sent along.
        if self.draw_style != "auto":
            script = f"drawStyle={json.dumps(self.draw_style)};{script}"
        self.map_view.page().runJavaScript(f"{script}setTracks({script_json(self._track_data)}, {str(fit).lower()});")

    def apply_draw_style(self, style: str) -> None:
        """Redraw the tracks in the new style, keeping the view; the markers and selection return."""
        if self.map_view is None or not self._map_ready or not self._track_data:
            return  # The page draws the tracks in this style once they are pushed.
        self.map_view.page().runJavaScript(f"drawStyle={json.dumps(style)};")
        self._push_tracks(False)
        self._marker_revision += 1
        self._flush_marker()
        self._update_selection()

    def _view_reported(self, view: str) -> None:
        try:
            self._map_view_state = json.loads(view)
        except ValueError:
            pass

    def workspace_state(self) -> dict:
        return {**super().workspace_state(), "map_type": "Satellite" if self.satellite_radio.isChecked() else "Street",
                "map_view": self._pending_map_view or self._map_view_state,
                "scale_bar": self.scale_bar_check.isChecked()}

    def restore_workspace_state(self, state: dict) -> None:
        if state.get("map_type") == "Satellite":
            self.satellite_radio.setChecked(True)
        if isinstance(state.get("scale_bar"), bool):
            self.scale_bar_check.setChecked(state["scale_bar"])
        view = state.get("map_view")
        if isinstance(view, dict) and "center" in view and "zoom" in view:
            self._pending_map_view = {"center": [float(value) for value in view["center"]], "zoom": float(view["zoom"])}
            # Tracks already on the map are shown again at the saved view.
            self._track_data = []
        super().restore_workspace_state(state)

    def _load_map(self):
        self._map_ready = False
        self._map_failed = False
        self._map_generation += 1
        self._marker_in_flight = False
        self.map_loading.setText("Loading map…")
        self.map_view.setHtml(map_html(background=canvas_background(self).name()))

    def _initialize_map(self) -> None:
        """Create the map view and connect its page and interaction bridge."""
        if self._disposed or self.map_view is not None:
            return
        self.map_view = GnssMapView(self, self.map_container)
        self.map_view.page().setBackgroundColor(canvas_background(self))
        self.map_stack.addWidget(self.map_view)
        self.channel = QWebChannel(self.map_view.page())
        self.bridge = GnssBridge(self)
        self.channel.registerObject("gnssBridge", self.bridge)
        self.map_view.page().setWebChannel(self.channel)
        self.bridge.point_selected.connect(lambda timestamp: self._place_marker_from_view(FIRST_MARKER, timestamp))
        self.bridge.second_point_selected.connect(
            lambda timestamp: self._place_marker_from_view(SECOND_MARKER, timestamp))
        self.bridge.marker_clear_requested.connect(lambda: self._clear_marker_from_view(FIRST_MARKER))
        self.bridge.second_marker_clear_requested.connect(lambda: self._clear_marker_from_view(SECOND_MARKER))
        self.bridge.view_changed.connect(self._refresh_value_box_positions)
        self.bridge.view_reported.connect(self._view_reported)
        # Right click places the second marker instead of opening a browser menu.
        self.map_view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.bridge.scroll_requested.connect(self.wheel_scrolled)
        self.map_view.wheel_scrolled.connect(self.wheel_scrolled)
        self.map_view.loadFinished.connect(self._on_map_loaded)
        self.map_view.loadStarted.connect(lambda: setattr(self, '_map_ready', False))
        # The page names the track under a resting pointer.
        self._name_hover = SeriesNameHover(self.map_view, self._track_name_at)
        self._load_map()

    def _track_name_at(self, position, done) -> None:  # type: ignore[no-untyped-def]
        if self.map_view is None or not self._map_ready:
            done(None)
            return
        names = list(self._track_names)
        self.map_view.page().runJavaScript(
            f"trackAt({position.x()},{position.y()},{HIT_TOLERANCE_PIXELS})",
            lambda index: done(names[int(index)] if isinstance(index, (int, float)) and 0 <= index < len(names)
                               else None))

    def _on_map_loaded(self, success: bool) -> None:
        if self.map_view is None:
            return
        generation = self._map_generation
        if not success:
            self._map_ready = False
            self._map_failed = True
            self.map_loading.setText("Could not load map")
            return
        self.map_view.page().runJavaScript(
            "window.gnssReady === true", lambda ready: self._on_map_ready(generation, ready))

    def _on_map_ready(self, generation, ready):
        if generation != self._map_generation:
            return
        self._map_ready = bool(ready)
        if self._map_ready:
            apply_map_background(self)
            # Pointer events reach the page's input widget, which exists once it has loaded.
            name_hover = getattr(self, "_name_hover", None)
            if name_hover is not None and self.map_view.focusProxy() is not None:
                name_hover.watch(self.map_view.focusProxy())
            self._push_tracks(True)
            self.map_stack.setCurrentWidget(self.map_view)
            self.legend.reposition()
            if self.satellite_radio.isChecked():
                self._set_map_type("Satellite")
            self._set_scale_bar(self.scale_bar_check.isChecked())
            self._flush_marker()
            self._refresh_value_box_positions()
        else:
            self._map_failed = True
            self.map_loading.setText("Could not initialize map. Check the map library connection.")

    def _set_scale_bar(self, shown: bool) -> None:
        if self.map_view is not None and self._map_ready:
            self.map_view.page().runJavaScript(f"setScaleBar({str(bool(shown)).lower()});")

    def _set_map_type(self, name: str) -> None:
        if self.map_view is not None and self._map_ready:
            self.map_view.page().runJavaScript(f"setMapType({json.dumps(name)});")

    def _flush_marker(self):
        """Keep only the latest marker state while loading or JS is busy."""
        if not self._map_ready or self.map_view is None or self._marker_in_flight:
            return
        generation, revision = self._map_generation, self._marker_revision
        self._marker_in_flight = True
        times = [self._marker_times[FIRST_MARKER], self._marker_times[SECOND_MARKER]]
        selection = _css_rgba(selection_rgba(2.0))
        colors = [_css_rgba(marker_color(index).getRgb()) for index in (FIRST_MARKER, SECOND_MARKER)]
        sync = json.dumps(self._sync_time)
        command = (f"setMarkers({json.dumps(times)}, {json.dumps(colors)}, {marker_width()}, {json.dumps(selection)}, "
                   f"{sync}, {json.dumps(sync_marker_color())});")
        self.map_view.page().runJavaScript(
            "if (window.gnssReady === true) { " + command + " }",
            lambda _: self._marker_delivered(generation, revision))

    def _marker_delivered(self, generation, revision):
        if generation != self._map_generation:
            return
        self._marker_in_flight = False
        if revision != self._marker_revision:
            self._flush_marker()

    def _markers_ready(self) -> bool:
        return self._map_ready

    def dispose(self) -> None:
        """Invalidate pending JavaScript callbacks before destroying the browser."""
        self._disposed = True
        if hasattr(self, "loader"):
            self.loader.shutdown()
        self._map_ready = False
        self._map_generation += 1
        self._marker_in_flight = False
        view, self.map_view = self.map_view, None
        if view is not None:
            view.blockSignals(True)
            view.stop()


def _css_rgba(rgba) -> str:  # type: ignore[no-untyped-def]
    red, green, blue, alpha = rgba
    return f"rgba({red}, {green}, {blue}, {alpha / 255:.3f})"


def track_distance_m(latitudes, longitudes) -> float:  # type: ignore[no-untyped-def]
    """Great-circle distance along consecutive points, in metres."""
    if len(latitudes) < 2:
        return 0.0
    lat, lon = np.radians(np.asarray(latitudes, dtype=float)), np.radians(np.asarray(longitudes, dtype=float))
    a = (np.sin(np.diff(lat) / 2) ** 2
         + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(np.diff(lon) / 2) ** 2)
    return float(np.nansum(2 * 6_371_000 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))))


def gnss_pair_for_series(h5_path: Path, series_name: str) -> tuple[Path, str, Path, str] | None:
    """Find the latitude/longitude sibling, based on metadata rather than a fixed parser name."""
    with h5py.File(h5_path, "r") as h5_file:
        names = _navigator_series_names(h5_file)
    requested = series_name.casefold()
    lat = next((name for name in names if _is_latitude(name) and (name.casefold() == requested or _same_group(name, series_name))), None)
    lon = next((name for name in names if _is_longitude(name) and (name.casefold() == requested or _same_group(name, series_name))), None)
    if lat is None:
        lat = next((name for name in names if _is_latitude(name) and _same_group(name, series_name)), None)
    if lon is None:
        lon = next((name for name in names if _is_longitude(name) and _same_group(name, series_name)), None)
    return (h5_path, lat, h5_path, lon) if lat is not None and lon is not None else None


def load_gnss_group(axes, *, time_range=None, cancelled=None, maximum_points=MAP_MAXIMUM_POINTS):
    return load_gnss_track(axes[0].path, axes[0].name, axes[1].path, axes[1].name, time_range=time_range,
                           maximum_points=maximum_points, cancelled=cancelled)


def load_gnss_track(h5_path: Path, lat_name: str, lon_path: Path, lon_name: str, *, time_range=None,
                    maximum_points: int = MAP_MAXIMUM_POINTS, cancelled=None) -> dict[str, list[float]]:
    """Every Nth latitude sample in the time range, with the nearest longitude.

    Memory stays bounded whatever the track length: latitude is read every Nth sample
    within the range, longitude is searched in chunks, and each coordinate scale comes
    from the series' LOD minimum and maximum (or a chunked scan).
    """
    with h5py.File(h5_path, "r") as h5_file, h5py.File(lon_path, "r") as lon_file:
        lat_group = _series_group_for_navigator_name(h5_file, lat_name)
        lon_group = _series_group_for_navigator_name(lon_file, lon_name)
        time_dataset_name = _attribute_text(lat_group.attrs.get("time_dataset", "time_unix"))
        time_unit = _attribute_text(lat_group.attrs.get("time_unit", time_dataset_name.removeprefix("time_")))
        time_reference = _attribute_text(lat_group.attrs.get("time_reference", ""))
        lat_times = lat_group[time_dataset_name]
        lat_metadata = _time_axis_metadata_from_group(h5_file, lat_group)
        raw_time_origin = lat_metadata.raw_origin
        seconds_per_unit = _time_unit_seconds(time_unit)
        is_unix_time = time_reference.casefold() == "unix" or time_dataset_name == "time_unix"
        log_start = h5_file.attrs.get("log_start_unix_seconds")
        # Choose one fixed-point scale per series from all samples, so a time
        # range selection cannot change how the same coordinates are read.
        lat_scale = _series_coordinate_scale(h5_file, lat_group, lat_name, 90.0)
        lon_scale = _series_coordinate_scale(lon_file, lon_group, lon_name, 180.0)
        lon_times = lon_group[_attribute_text(lon_group.attrs.get("time_dataset", "time_unix"))]
        if not len(lon_times):
            raise ValueError("Longitude series has no samples")
        start, stop = _visible_sample_bounds(lat_times, _canonical_to_native_range(time_range, lat_metadata))
        available = max(0, stop - start)
        step = decimation_step(available, maximum_points)
        device_times = np.asarray(lat_times[start:stop:step], dtype=float)
        # Use the file's time origin, as every other card does, rather than
        # the first GNSS fix; the map then shares the linked marker time.
        universal_times = _canonical_time_values(device_times, lat_metadata)
        latitudes = np.asarray(lat_group["value"][start:stop:step], dtype=float)
        if time_range is not None:
            lower, upper = sorted(time_range)
            inside = (universal_times >= lower) & (universal_times <= upper)
            device_times, universal_times, latitudes = device_times[inside], universal_times[inside], latitudes[inside]
        lon_metadata = _time_axis_metadata_from_group(lon_file, lon_group)
        longitudes = (_aligned_values(lon_times, lon_group["value"], lon_metadata, universal_times, cancelled)
                      if len(universal_times) else np.empty(0))
    latitudes = latitudes / lat_scale
    longitudes = longitudes / lon_scale
    valid = (
        np.isfinite(latitudes)
        & np.isfinite(longitudes)
        & (np.abs(latitudes) <= 90.0)
        & (np.abs(longitudes) <= 180.0)
    )
    return {
        "shown_points": len(universal_times),
        "available_points": available,
        "time": universal_times[valid].tolist(),
        "device_time": device_times[valid].tolist(),
        "lat": latitudes[valid].tolist(),
        "lon": longitudes[valid].tolist(),
        "name": lat_name.rsplit("/", 1)[0] or "GNSS",
        "time_unit": time_unit,
        "seconds_per_unit": seconds_per_unit,
        "raw_time_origin": raw_time_origin,
        "log_start_unix_seconds": float(log_start) if log_start is not None else None,
        "is_unix_time": is_unix_time,
    }


def _series_coordinate_scale(h5_file, group, name: str, maximum_degrees: float) -> float:  # type: ignore[no-untyped-def]
    """The coordinate scale of a whole series, without reading all its samples at once."""
    unit = _attribute_text(group.attrs.get("unit", ""))
    source = _attribute_text(group.attrs.get("source", ""))
    declared = _declared_coordinate_scale(unit=unit, name=name, source=source)
    if declared is not None:
        return declared
    # Only an unlabelled series needs its magnitude: LOD bounds, or a chunked scan.
    bounds = series_value_range(h5_file, name, scan_raw=True)
    return _coordinate_scale(np.asarray(bounds if bounds is not None else [], dtype=float), maximum_degrees)


def _declared_coordinate_scale(*, unit="", name="", source="") -> float | None:
    """The scale a series' unit, name or source states (shared, src/data/axis_scale.py)."""
    return declared_coordinate_scale(unit=unit, name=name, source=source)


def _coordinate_scale(values: np.ndarray, maximum_degrees: float, *, unit="", name="", source="") -> float:
    """Return the one divisor that converts a whole coordinate series to degrees.

    Explicit units take precedence over all magnitude heuristics; only unlabelled legacy
    series fall back to a scale chosen from the complete series (src/data/axis_scale.py).
    """
    finite = np.abs(np.asarray(values, dtype=float))
    finite = finite[np.isfinite(finite)]
    largest = float(finite.max()) if len(finite) else 0.0
    return coordinate_scale(largest, maximum_degrees, unit=unit, name=name, source=source)


def _normalise_coordinate(values: np.ndarray, maximum_degrees: float) -> np.ndarray:
    """Convert common fixed-point GNSS storage to decimal degrees for maps."""
    values = np.asarray(values, dtype=float)
    return values / _coordinate_scale(values, maximum_degrees)


def _same_group(first: str, second: str) -> bool:
    return first.rsplit("/", 1)[0].casefold() == second.rsplit("/", 1)[0].casefold()


# Shared with the 3D plot's Equal scale (src/data/axis_scale.py).
_is_latitude = is_latitude_name
_is_longitude = is_longitude_name


def script_json(value) -> str:
    """Encode JSON for a JavaScript literal; ``</script>`` in names stays inert."""
    return json.dumps(value).replace("<", "\\u003c")


# The map data's credit sits at the map's top right, just under the card's Close button row
# (the map starts 3 px below the card's top); the legend goes below the credit.
MAP_CREDIT_TOP = 28
MAP_LEGEND_TOP = MAP_CREDIT_TOP + 18


def map_html(*, background: str = "#000000") -> str:
    """Return the empty map page; tracks are pushed with ``setTracks`` once ready."""
    return f'''<!doctype html>
<html><head><meta charset="utf-8">
<style>html,body,#map{{height:100%;margin:0;background:{background}}}
.leaflet-top.leaflet-right{{top:{MAP_CREDIT_TOP}px}}.leaflet-top.leaflet-right .leaflet-control{{margin-top:0}}</style>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
</head><body><div id="map"></div><script>
let tracks=[];
let bridge=null;
// A view from a workspace file, shown instead of fitting the first tracks.
let restoreView=null;
let map=L.map('map',{{zoomControl:false,scrollWheelZoom:false,doubleClickZoom:false}});
// The map data's required credit, small at the top right under the card's Close button,
// without Leaflet's own name.
map.attributionControl.setPrefix(false);
map.attributionControl.setPosition('topright');
let street=L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'&copy; OpenStreetMap'}}).addTo(map);
// A metric scale bar at the bottom right; the card's Scale bar checkbox shows or hides it.
let scaleBar=L.control.scale({{position:'bottomright',imperial:false}});
function setScaleBar(shown){{if(shown)scaleBar.addTo(map);else scaleBar.remove();}}
let satellite=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',{{maxZoom:19,attribution:'Esri'}});
let markers=[];
let selectionLayers=[];
let lastContextMenu=0;
let all=[];
let trackPoints=[];
let trackLayers=[];
// "auto" and "lines" draw tracks as lines, "points" as their positions on one canvas.
let drawStyle='auto';
let pointRenderer=L.canvas({{padding:0.5}});
// The track drawn within tol pixels of a container point, or -1; for the hover name.
function trackAt(x,y,tol){{
  let best=tol*tol,found=-1;
  trackPoints.forEach((pts,i)=>{{
    let prev=null;
    for(const p of pts){{
      let c=map.latLngToContainerPoint([p[0],p[1]]);
      let dx=c.x-x,dy=c.y-y,d=dx*dx+dy*dy;
      if(prev&&drawStyle!=='points'){{
        let vx=c.x-prev.x,vy=c.y-prev.y,len=vx*vx+vy*vy;
        let t=len>0?Math.max(0,Math.min(1,((x-prev.x)*vx+(y-prev.y)*vy)/len)):0;
        let sx=prev.x+t*vx-x,sy=prev.y+t*vy-y;d=Math.min(d,sx*sx+sy*sy);
      }}
      if(d<=best){{best=d;found=i}}
      prev=c;
    }}
  }});
  return found;
}}
function nearest(e){{return all.reduce((a,p)=>map.distance(e.latlng,L.latLng(p[0],p[1]))<map.distance(e.latlng,L.latLng(a[0],a[1]))?p:a)}}
function select(e,index){{if(!all.length||!bridge)return;let point=nearest(e);if(index===0)bridge.select_timestamp(point[2]);else bridge.select_second_timestamp(point[2])}}
function setTracks(nextTracks,fit=false){{
tracks=nextTracks;
trackLayers.forEach(layer=>map.removeLayer(layer));
trackLayers=[];trackPoints=[];all=[];clearMarkers();
tracks.forEach(track=>{{
  let pts=track.lat.map((lat,j)=>[lat,track.lon[j],track.time[j]]);
  let color=track.color||'#2196f3';
  trackPoints.push(pts);
  // A loop, not all.push(...pts): spreading 200k+ points overflows the call stack.
  for(const p of pts)all.push(p);
  if(drawStyle==='points')trackLayers.push(L.layerGroup(pts.map(p=>L.circleMarker(p.slice(0,2),
    {{renderer:pointRenderer,radius:2.5,stroke:false,fillColor:color,fillOpacity:1,interactive:false}}))).addTo(map));
  else trackLayers.push(L.polyline(pts.map(p=>p.slice(0,2)),{{color:color,weight:3}}).addTo(map));
  // A stationary or very short GNSS recording can be smaller than one pixel
  // even at Leaflet's maximum zoom.  The H5 data is still a valid track, so
  // give it a visible representative point instead of making it look empty.
  if(pts.length){{
    let origin=L.latLng(pts[0][0],pts[0][1]);
    let span=0;for(const p of pts)span=Math.max(span,map.distance(origin,L.latLng(p[0],p[1])));
    if(pts.length===1||span<1){{
      let p=pts[Math.floor(pts.length/2)];
      trackLayers.push(L.circleMarker(p.slice(0,2),{{radius:6,color:color,weight:2,fillColor:color,fillOpacity:0.8}}).addTo(map));
    }}
  }}
}});
if(fit&&all.length){{
  if(restoreView){{map.setView(restoreView.center,restoreView.zoom,{{animate:false}});restoreView=null}}
  else map.fitBounds(all.map(p=>p.slice(0,2)),{{padding:[20,20]}});
}}
}}
map.on('click',e=>select(e,0));
map.on('dblclick',()=>{{if(bridge)bridge.clear_marker()}});
// Right click places the second marker; a second right click soon after removes it.
map.on('contextmenu',e=>{{let now=Date.now();if(now-lastContextMenu<400){{lastContextMenu=0;if(bridge)bridge.clear_second_marker();return}}lastContextMenu=now;select(e,1)}});
map.on('moveend zoomend',()=>{{
  if(!bridge)return;
  bridge.notify_view_changed();
  let c=map.getCenter();bridge.report_view(JSON.stringify({{center:[c.lat,c.lng],zoom:map.getZoom()}}));
}});
map.getContainer().addEventListener('wheel',e=>{{
  e.preventDefault();
  if(e.ctrlKey){{map.setZoom(map.getZoom()+(e.deltaY<0?1:-1))}}
  else if(bridge){{bridge.scroll_workspace(e.deltaY<0?120:-120)}}
}},{{passive:false}});
new QWebChannel(qt.webChannelTransport,c=>bridge=c.objects.gnssBridge);
function setMapType(t){{if(t==='Satellite'){{map.removeLayer(street);satellite.addTo(map)}}else{{map.removeLayer(satellite);street.addTo(map)}}}}
function nearestTimestamp(points,t){{return points.reduce((a,p)=>Math.abs(p[2]-t)<Math.abs(a[2]-t)?p:a)}}
function pointAtTimestamp(points,t){{if(!points.length||t<points[0][2]||t>points[points.length-1][2])return null;return nearestTimestamp(points,t)}}
function setMarkers(times,colors,width,selectionColor,syncTime=null,syncColor=null){{
  clearMarkers();
  if(syncTime!==null)trackPoints.forEach(points=>{{
    let p=pointAtTimestamp(points,syncTime);
    if(p)markers.push(L.circleMarker(p.slice(0,2),{{radius:11,color:syncColor,weight:2,dashArray:'4 3',fill:false,interactive:false}}).addTo(map));
  }});
  if(times[0]!==null&&times[1]!==null){{
    let left=Math.min(times[0],times[1]),right=Math.max(times[0],times[1]);
    trackPoints.forEach(points=>{{
      let inside=points.filter(p=>p[2]>=left&&p[2]<=right).map(p=>p.slice(0,2));
      // Above the track, translucent so its color shows through; markers are added after it.
      if(inside.length>1)selectionLayers.push(L.polyline(inside,{{color:selectionColor,weight:9,interactive:false}}).addTo(map));
    }});
  }}
  times.forEach((t,index)=>{{
    if(t===null)return;
    trackPoints.forEach(points=>{{
      let p=pointAtTimestamp(points,t);
      if(p)markers.push(L.circleMarker(p.slice(0,2),{{radius:8,color:colors[index],weight:Math.max(2,width),fillOpacity:0.25}}).addTo(map));
    }});
  }});
}}
function clearMarkers(){{markers.concat(selectionLayers).forEach(layer=>map.removeLayer(layer));markers=[];selectionLayers=[]}}
function markerScreenPositions(t){{return trackPoints.map(points=>{{let p=pointAtTimestamp(points,t);if(!p)return null;let point=map.latLngToContainerPoint(p.slice(0,2));return [point.x,point.y]}})}}
window.gnssReady = true;
</script></body></html>'''
