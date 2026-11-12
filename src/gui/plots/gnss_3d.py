"""3D GNSS-track plot backed by MapLibre GL JS and deck.gl.

The browser libraries and the default OpenFreeMap basemap are loaded online.
The widget deliberately remains independent from the existing 2D Leaflet GNSS
card so it can evolve without changing the established map workflow.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import h5py
import numpy as np
from PySide6.QtCore import QEvent, QLocale, QRectF, QSignalBlocker, QSize, Qt, Signal, Slot
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QDoubleSpinBox, QHBoxLayout, QLabel, QSlider, QStackedLayout, QToolButton, QWidget,
)

from src.gui.plots.gnss import (
    MAP_MAXIMUM_POINTS, GnssBridge, MapMarkersMixin, gnss_pair_for_series, load_gnss_group, script_json,
)
from src.gui.plots.markers import (
    FIRST_MARKER, SECOND_MARKER, sync_marker_color, marker_color, marker_width, selection_rgba,
)
from src.gui.plots.value_boxes import ValueBoxLayer
from src.gui.plots.icons import plot_control_icon
from src.gui.plots.theme import apply_map_background, canvas_background, palette_overlay_stylesheet
from src.gui.plots.base import PlotBaseWidget, series_legend_name
from src.gui.plots.legend import PlotLegendWidget
from src.gui.plots.legend_groups import GROUP_COLORS, SeriesGroup
from src.data.xyz import AxisSource, interpolated_values
from src.jobs.plot_groups import GroupedPlotLoadCoordinator
from src.data.h5_series import _navigator_series_names
from src.gui.plots.series import (
    _attribute_text,
    _series_group_for_navigator_name,
    _time_axis_metadata_from_group,
)


OPENFREEMAP_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty"
TERRARIUM_TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
# The terrain's most detailed elevation tiles; Follow ground and Auto read the same ones.
# Not above the map's own vector tiles (OpenFreeMap: zoom 14): MapLibre cannot drape a
# stretched map tile on more detailed terrain ("cannot calculate elevation if elevation
# maxzoom > source.maxzoom").
DEM_ZOOM = 14
ALTITUDE_AXIS = 2
FLAT_ALTITUDE_LABEL = "0 m (flat)"
MAX_EXAGGERATION = 10.0
MAX_TRACK_OFFSET = 1000.0
# Auto: heights exaggerated this much.
AUTO_EXAGGERATION = 3.0
LOG = logging.getLogger("lodgazilla.gnss_3d")


class ValueSlider(QSlider):
    """A horizontal slider that shows its value, read-only, over its middle."""

    def __init__(self, text_for_value, parent: QWidget | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._text_for_value = text_for_value
        self.valueChanged.connect(self.update)

    def value_text(self) -> str:
        return self._text_for_value(self.value())

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        painter = QPainter(self)
        font = painter.font()
        font.setPointSizeF(font.pointSizeF() * 0.85)
        painter.setFont(font)
        text = self.value_text()
        box = QRectF(painter.fontMetrics().boundingRect(text)).adjusted(-3, -1, 3, 1)
        box.moveCenter(QRectF(self.rect()).center())
        background = self.palette().color(QPalette.ColorRole.Window)
        background.setAlpha(210)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(box, 3, 3)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()


class Gnss3DBridge(GnssBridge):
    """The 2D map's bridge, plus the result of Auto, which the page works out from the terrain."""

    # JSON with the exaggeration factor and the track offset in metres.
    auto_settings_reported = Signal(str)

    @Slot(str)
    def report_auto_settings(self, settings: str) -> None:
        self.auto_settings_reported.emit(settings)


class Gnss3DPlotWidget(MapMarkersMixin, PlotBaseWidget):
    """A 3D GNSS route using recorded altitude or an explicitly selected height."""
    help_topic = "gnss_3d"

    supports_horizontal_layout = True
    default_sample_limit = MAP_MAXIMUM_POINTS
    sample_limit_noun = "track"
    # Auto and Lines draw each track as a line; Points draws its positions.
    supports_draw_style = True

    marker_placed = Signal(float)
    marker_cleared = Signal()
    second_marker_placed = Signal(float)
    second_marker_cleared = Signal()

    def __init__(
        self,
        h5_path: Path,
        series_name: str,
        parent: QWidget | None = None,
        *,
        height_series: tuple[Path, str] | None = None,
        time_range: tuple[float, float] | None = None,
        initial_axes=None,
    ) -> None:
        if initial_axes is None:
            pair = gnss_pair_for_series(h5_path, series_name)
            if pair is None:
                raise ValueError(f"No latitude/longitude partner found for {series_name}")
            height_series = ((Path(height_series[0]), str(height_series[1])) if height_series
                             else _track_altitude_series(pair[0], pair[1]))
            axes = (AxisSource(pair[0], pair[1]), AxisSource(pair[2], pair[3]),
                    AxisSource(*height_series) if height_series else None)
        else:
            axes = tuple(initial_axes)
            if len(axes) != 3:
                raise ValueError("A 3D GNSS track needs three axis slots")
        super().__init__(h5_path, series_name, parent)
        self.time_range = time_range
        self.configure_legend_groups(("Lat", "Lon", "Alt"), title="Track")
        self.grouped_series.groups.append(SeriesGroup(axes, GROUP_COLORS[0], tuple(axis for axis in axes if axis)))
        self.series = self.grouped_series.entries()
        # An empty Alt slot draws the track flat at 0 m instead of waiting.
        self.loader = GroupedPlotLoadCoordinator(self, load_3d_gnss_group, optional_axes=(ALTITUDE_AXIS,))
        self.loader.loaded.connect(self._loaded_tracks)
        self.results = []
        self._loading = False
        self._map_page_loaded = False
        self._map_page_failed = False
        self._track_data = []
        # The camera last reported by the page, and a saved one waiting for the first tracks.
        self._map_view_state: dict | None = None
        self._pending_map_view: dict | None = None
        self._map_generation = 0
        self._marker_revision = 0
        # A new card applies Auto to its first tracks; a card from a workspace keeps its saved settings.
        self._auto_pending = True
        self._init_point_markers()
        self._value_box_times = {FIRST_MARKER: None, SECOND_MARKER: None}
        self.setMinimumHeight(360)

        self.map_container = QWidget(self)
        self.map_container.setStyleSheet(f"background: {canvas_background(self).name()};")
        self.map_stack = QStackedLayout(self.map_container)
        self.map_stack.setContentsMargins(0, 0, 0, 0)
        self.loading = QLabel("Loading 3D map…", self.map_container)
        self.loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map_view = QWebEngineView(self.map_container)
        self.map_view.page().setBackgroundColor(canvas_background(self))
        # Right click places the second marker instead of opening a browser menu.
        self.map_view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.channel = QWebChannel(self.map_view.page())
        self.bridge = Gnss3DBridge(self)
        self.channel.registerObject("gnssBridge", self.bridge)
        self.map_view.page().setWebChannel(self.channel)
        self.bridge.point_selected.connect(lambda timestamp: self._place_marker_from_view(FIRST_MARKER, timestamp))
        self.bridge.second_point_selected.connect(
            lambda timestamp: self._place_marker_from_view(SECOND_MARKER, timestamp))
        self.bridge.marker_clear_requested.connect(lambda: self._clear_marker_from_view(FIRST_MARKER))
        self.bridge.second_marker_clear_requested.connect(lambda: self._clear_marker_from_view(SECOND_MARKER))
        self._watch_map_events(self.map_view)
        self.map_stack.addWidget(self.loading)
        self.map_stack.addWidget(self.map_view)
        self.map_stack.setCurrentWidget(self.loading)
        self.content_layout.addWidget(self.map_container, stretch=1)

        # The controls sit at the top of the card, in groups placed left to right in its header
        # row before its buttons, continuing on rows below when the card is narrow
        # (``_layout_header_controls``). The rest of the Drag strip still moves the card.
        groups = []

        def group(*widgets):
            container = QWidget(self)
            layout = QHBoxLayout(container)
            layout.setContentsMargins(5, 0, 5, 0)
            layout.setSpacing(5)
            for widget in widgets:
                widget.setParent(container)
                layout.addWidget(widget)
            groups.append(container)
            return container

        # The map's opacity; at 0% the map and its terrain are hidden. Terrain is always shown
        # with the map, and the map's terrain and buildings always hide the track behind them.
        self.map_opacity = ValueSlider(lambda value: f"{value}%")
        self.map_opacity.setRange(0, 100)
        self.map_opacity.setValue(100)
        self.map_opacity.setFixedWidth(64)
        self.map_opacity.setToolTip("Map opacity; 0% hides the map")
        self.map_opacity.setAccessibleName("Map opacity")
        # Positions are tenths of the factor.
        # The number in the system's format, like the Offset field.
        self.altitude_exaggeration = ValueSlider(lambda value: QLocale().toString(value / 10, "f", 1) + "x")
        self.altitude_exaggeration.setRange(10, round(MAX_EXAGGERATION * 10))
        self.altitude_exaggeration.setValue(10)
        self.altitude_exaggeration.setFixedWidth(64)
        self.altitude_exaggeration.setToolTip("Altitude exaggeration: multiply map and track altitudes")
        self.altitude_exaggeration.setAccessibleName("Altitude exaggeration")
        self.track_altitude_offset = QSlider(Qt.Orientation.Horizontal)
        self.track_altitude_offset.setRange(round(-MAX_TRACK_OFFSET * 10), round(MAX_TRACK_OFFSET * 10))
        self.track_altitude_offset.setValue(0)
        self.track_altitude_offset.setPageStep(100)
        self.track_altitude_offset.setFixedWidth(64)
        self.track_altitude_offset.setToolTip(
            "Track altitude offset in metres, before exaggeration: (altitude + offset) x factor"
        )
        self.track_altitude_offset.setAccessibleName("Track altitude offset in metres")
        # Typed in; the slider beside it does the stepping, so the field has no arrows.
        self.track_offset_input = QDoubleSpinBox()
        self.track_offset_input.setDecimals(1)
        self.track_offset_input.setRange(-MAX_TRACK_OFFSET, MAX_TRACK_OFFSET)
        self.track_offset_input.setSingleStep(0.1)
        self.track_offset_input.setSuffix(" m")
        self.track_offset_input.setKeyboardTracking(False)
        self.track_offset_input.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.track_offset_input.setFixedWidth(self.track_offset_input.fontMetrics().horizontalAdvance("-1000.0 m") + 10)
        self.track_offset_input.setToolTip("Track altitude offset")
        self.track_offset_input.setAccessibleName("Track altitude offset value in metres")
        self.follow_ground = QToolButton()
        self.follow_ground.setCheckable(True)
        self.follow_ground.setToolTip("Follow ground: draw the track on the ground, ignoring its altitude and offset")
        self.follow_ground.setAccessibleName("Follow ground")
        self.auto_button = QToolButton()
        self.auto_button.setToolTip("Auto: set the offset that puts the track's lowest point on the ground")
        self.auto_button.setAccessibleName("Auto offset")
        self.center_view_button = QToolButton()
        self.center_view_button.setToolTip("Center view: show the whole track again")
        self.center_view_button.setAccessibleName("Center view")
        for button in (self.follow_ground, self.auto_button, self.center_view_button):
            button.setAutoRaise(True)
            button.setIconSize(QSize(18, 18))
        self._update_control_icons()
        group(QLabel("Map"), self.map_opacity)
        group(QLabel("Height"), self.altitude_exaggeration)
        group(QLabel("Offset"), self.track_altitude_offset, self.track_offset_input)
        group(self.follow_ground, self.auto_button, self.center_view_button)
        self.control_groups = groups
        for container in groups:
            container.setStyleSheet(palette_overlay_stylesheet())
            container.adjustSize()
        # Hidden and shown with the other header controls (the Plot navigator's Card controls button).
        self.header_left_controls.extend(groups)
        self.map_opacity.valueChanged.connect(self._set_map_opacity)
        self.follow_ground.toggled.connect(self._set_follow_ground)
        self.auto_button.clicked.connect(lambda: self.apply_auto_settings())
        self.center_view_button.clicked.connect(self.center_view)
        self.altitude_exaggeration.valueChanged.connect(lambda value: self._set_altitude_exaggeration(value / 10.0))
        self.track_altitude_offset.valueChanged.connect(
            lambda value: self.track_offset_input.setValue(value / 10.0)
        )
        self.track_offset_input.valueChanged.connect(self._set_track_altitude_offset)
        self.map_view.loadFinished.connect(self._on_loaded)
        self.legend = PlotLegendWidget(self.map_container)
        self.legend.set_group_model(self.grouped_series)
        self.value_boxes = ValueBoxLayer(self.map_container)
        self.bridge.view_changed.connect(self._refresh_value_box_positions)
        self.bridge.view_reported.connect(self._view_reported)
        self.bridge.auto_settings_reported.connect(self._auto_settings_reported)
        # Connected after the legend so the flat-altitude labels win.
        self.grouped_series.changed.connect(self._update_legend_labels)
        self._update_legend_labels()
        self.on_series_changed()
        self._load_page()

    @property
    def altitude_factor(self) -> float:
        """The altitude exaggeration set with the Height slider."""
        return self.altitude_exaggeration.value() / 10.0

    def set_altitude_factor(self, factor: float) -> None:
        self.altitude_exaggeration.setValue(round(max(1.0, min(MAX_EXAGGERATION, factor)) * 10))

    @property
    def height_series(self) -> tuple[Path, str] | None:
        """The first track's current Alt assignment, or None for a flat track."""
        groups = self.grouped_series.groups
        axis = groups[0].axes[ALTITUDE_AXIS] if groups else None
        return (axis.path, axis.name) if axis is not None else None

    def _watch_map_events(self, target):
        # Chromium receives input through internal children, which can be
        # created or replaced after the QWebEngineView itself is constructed.
        target.installEventFilter(self)
        for child in target.children():
            self._watch_map_events(child)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ChildAdded:
            self._watch_map_events(event.child())
        elif (event.type() == QEvent.Type.Wheel and isinstance(watched, QWidget)
              and (watched is self.map_view or self.map_view.isAncestorOf(watched))
              and not event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.wheel_scrolled.emit(event.angleDelta().y() or event.pixelDelta().y())
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if hasattr(self, "legend"):
            self.legend.reposition()
        if hasattr(self, "value_boxes"):
            self._refresh_value_box_positions()

    def _update_control_icons(self) -> None:
        """The Follow ground and Auto icons in the variant for the current theme."""
        self.follow_ground.setIcon(plot_control_icon(self, "follow-ground"))
        self.auto_button.setIcon(plot_control_icon(self, "auto-ground-offset"))
        self.center_view_button.setIcon(plot_control_icon(self, "center-track"))

    def _layout_header_controls(self) -> None:
        """Place the control groups left to right in the header row, before the card's buttons;
        a group that does not fit continues on a row below, across the card's width."""
        super()._layout_header_controls()
        groups = [group for group in getattr(self, "control_groups", []) if not group.isHidden()]
        if not groups:
            return
        buttons = [control.x() for control in (self.close_button, *self.header_controls) if not control.isHidden()]
        row_right = min(buttons, default=self.width()) - 8
        # Every group as tall as the tallest one and the header buttons, so the rows line up.
        height = max(self.close_button.height(), *(group.sizeHint().height() for group in groups))
        row_top = self.close_button.y() + (self.close_button.height() - height) // 2
        x = 8
        for group in groups:
            width = group.sizeHint().width()
            group.setFixedSize(width, height)
            if x + width > row_right:
                # Below the header row, or below the previous row of groups.
                row_top += height + 4
                row_right, x = self.width() - 8, 8
            group.move(x, row_top)
            group.raise_()
            x += width + 4

    def _markers_ready(self) -> bool:
        return self._map_page_loaded

    def _flush_marker(self) -> None:
        times = [self._marker_times[FIRST_MARKER], self._marker_times[SECOND_MARKER]]
        selection = list(selection_rgba(2.0))
        colors = [list(marker_color(index).getRgb()) for index in (FIRST_MARKER, SECOND_MARKER)]
        sync = self._sync_time
        sync_color = [int(sync_marker_color()[i:i + 2], 16) for i in (1, 3, 5)] + [255]
        self._run_map_script(f"setMarkers({json.dumps(times)}, {json.dumps(colors)}, "
                             f"{marker_width()}, {json.dumps(selection)}, {json.dumps(sync)}, {json.dumps(sync_color)});")

    def on_series_changed(self) -> None:
        self._loading = True
        self.loader.request([group.axes for group in self.grouped_series.groups], self.time_range,
                            self.sample_limit)

    def _loaded_tracks(self, results):
        self._loading = False
        self.results = results
        self.update_sample_coverage(results)
        fit = not self._track_data
        self._track_data = []
        for index, track in enumerate(results):
            if isinstance(track, dict):
                color = self.grouped_series.groups[index].color.lstrip("#")
                track["color"] = [int(color[offset:offset + 2], 16) for offset in (0, 2, 4)]
                self._track_data.append(track)
        self._update_legend_labels()
        if self._map_page_loaded:
            self._push_tracks(fit)
            self._flush_marker()
            self._apply_auto_settings_once()
        elif self._map_page_failed:
            self._load_page()
        # Otherwise the loading page receives the tracks in _on_loaded.
        self._update_selection()
        self._refresh_value_box_positions()

    def _update_legend_labels(self, *_args) -> None:
        """Show where the Alt row is replaced by a flat 0 m altitude."""
        labels = {}
        width = self.grouped_series.width
        for index, group in enumerate(self.grouped_series.groups):
            altitude = group.axes[ALTITUDE_AXIS]
            key = index * width + ALTITUDE_AXIS
            if altitude is None:
                # Until Lat and Lon are assigned, the track itself is incomplete.
                if all(axis is not None for axis in group.axes[:ALTITUDE_AXIS]):
                    labels[key] = FLAT_ALTITUDE_LABEL
                continue
            result = self.results[index] if not self._loading and index < len(self.results) else None
            if isinstance(result, dict) and result.get("altitude_warning"):
                labels[key] = f"{series_legend_name(altitude.path, altitude.name)} unusable: {FLAT_ALTITUDE_LABEL}"
        self.legend.refresh_groups(labels)

    def _push_tracks(self, fit: bool) -> None:
        """Send tracks to the loaded page; ``setHtml`` is limited to 2 MB."""
        script = ""
        if fit and getattr(self, "_pending_map_view", None) is not None and self._track_data:
            script, self._pending_map_view = f"restoreView={json.dumps(self._pending_map_view)};", None
        self.map_view.page().runJavaScript(f"{script}setTracks({script_json(self._track_data)}, {str(fit).lower()});")

    def _view_reported(self, view: str) -> None:
        try:
            self._map_view_state = json.loads(view)
        except ValueError:
            pass

    def workspace_state(self) -> dict:
        return {**super().workspace_state(), "map_view": self._pending_map_view or self._map_view_state,
                "map_controls": {
                    "map_opacity": self.map_opacity.value(), "altitude_factor": self.altitude_factor,
                    "track_offset": self.track_offset_input.value(),
                    "follow_ground": self.follow_ground.isChecked()}}

    def restore_workspace_state(self, state: dict) -> None:
        controls = state.get("map_controls")
        if isinstance(controls, dict):
            self._auto_pending = False
            try:
                opacity = int(controls.get("map_opacity", self.map_opacity.value()))
                # Older workspaces saved a separate Map shown switch.
                self.map_opacity.setValue(0 if controls.get("map_visible") is False else opacity)
                self.set_altitude_factor(float(controls.get("altitude_factor", self.altitude_factor)))
                self.track_offset_input.setValue(float(controls.get("track_offset", self.track_offset_input.value())))
                self.follow_ground.setChecked(bool(controls.get("follow_ground", False)))
            except (TypeError, ValueError):
                pass
        view = state.get("map_view")
        if isinstance(view, dict) and "center" in view and "zoom" in view:
            self._pending_map_view = {"center": [float(value) for value in view["center"]], "zoom": float(view["zoom"]),
                                      "pitch": float(view.get("pitch", 55)), "bearing": float(view.get("bearing", 0))}
            # Tracks already on the map are shown again at the saved camera.
            self._track_data = []
        super().restore_workspace_state(state)

    def apply_theme(self) -> None:
        apply_map_background(self)
        for group in self.control_groups:
            group.setStyleSheet(palette_overlay_stylesheet())
        self._update_control_icons()
        self.value_boxes.apply_style()

    def _load_page(self) -> None:
        """Load the empty map page with the current view settings."""
        self._map_page_loaded = False
        self._map_page_failed = False
        self._map_generation += 1
        self.loading.setText("Loading 3D map…")
        self.map_stack.setCurrentWidget(self.loading)
        self.map_view.setHtml(_three_d_map_html(background=canvas_background(self).name(), settings={
            "mapOpacity": self.map_opacity.value() / 100.0,
            "altitudeExaggeration": self.altitude_factor,
            "trackAltitudeOffset": self.track_offset_input.value(),
            "followGround": self.follow_ground.isChecked(),
            "drawStyle": self.draw_style,
        }))

    def _height_values(self, gnss_times: list[float]) -> tuple[list[float], str]:
        """Interpolate optional height/depth samples onto the GNSS timestamps."""
        return load_track_heights(self.height_series, gnss_times)

    def _on_loaded(self, success: bool) -> None:
        self._map_page_loaded = success
        self._map_page_failed = not success
        if success:
            apply_map_background(self)
            self._push_tracks(True)
            self.map_stack.setCurrentWidget(self.map_view)
            self._set_map_opacity(self.map_opacity.value())
            self._set_altitude_exaggeration(self.altitude_factor)
            self._set_track_altitude_offset(self.track_offset_input.value())
            self._set_follow_ground(self.follow_ground.isChecked())
            self._flush_marker()
            self._apply_auto_settings_once()
            self.legend.reposition()
            self._refresh_value_box_positions()
        else:
            self.loading.setText("Could not load 3D map")

    def _set_map_opacity(self, value: int) -> None:
        """Fade only MapLibre layers; the deck.gl GNSS route stays opaque. 0% hides the map."""
        value = max(0, min(100, value))
        self._run_map_script(f"setMapOpacity({value / 100.0});")

    def _set_altitude_exaggeration(self, value: float) -> None:
        factor = max(1.0, min(MAX_EXAGGERATION, value))
        with QSignalBlocker(self.altitude_exaggeration):
            self.altitude_exaggeration.setValue(round(factor * 10))
        self._run_map_script(f"setAltitudeExaggeration({factor});")

    def _set_track_altitude_offset(self, value: float) -> None:
        offset = max(-MAX_TRACK_OFFSET, min(MAX_TRACK_OFFSET, value))
        with QSignalBlocker(self.track_altitude_offset):
            self.track_altitude_offset.setValue(round(offset * 10))
        self._run_map_script(f"setTrackAltitudeOffset({offset});")

    def _set_follow_ground(self, enabled: bool) -> None:
        # On the ground, the altitude series and its offset are not used.
        self.track_altitude_offset.setEnabled(not enabled)
        self.track_offset_input.setEnabled(not enabled)
        self._run_map_script(f"setFollowGround({str(bool(enabled)).lower()});")

    def apply_auto_settings(self, *, first: bool = False) -> None:
        """Auto: the offset that puts the track's lowest point on the ground; a track without
        altitude follows the ground instead.

        A new card's ``first`` Auto also shows the whole track and exaggerates heights 3x; the
        Auto button leaves the view and the exaggeration as they are. The page works out the
        offset from the terrain and reports it back (``_auto_settings_reported``).
        """
        follow = bool(self._track_data) and all(track.get("flat_altitude") for track in self._track_data)
        self.follow_ground.setChecked(follow)
        if first:
            self.set_altitude_factor(AUTO_EXAGGERATION)
        self._run_map_script(f"autoSettings({str(not follow).lower()}, {str(first).lower()});")

    def apply_draw_style(self, style: str) -> None:
        self._run_map_script(f"setDrawStyle({json.dumps(style)});")

    def center_view(self) -> None:
        """Show the whole track again, for example after panning it out of view."""
        self._run_map_script("fitTracks();")

    def _apply_auto_settings_once(self) -> None:
        if self._auto_pending and self._track_data and self._map_page_loaded:
            self._auto_pending = False
            self.apply_auto_settings(first=True)

    def _auto_settings_reported(self, settings: str) -> None:
        try:
            values = json.loads(settings)
            factor, offset = float(values["factor"]), float(values["offset"])
        except (KeyError, TypeError, ValueError):
            return
        self.set_altitude_factor(factor)
        self.track_offset_input.setValue(offset)

    def _run_map_script(self, script):
        if self._map_page_loaded:
            self.map_view.page().runJavaScript(script)

    def dispose(self):
        # Invalidate pending marker-position callbacks before the page goes away.
        self._map_generation += 1
        self._map_page_loaded = False
        self.loader.shutdown()
        self.map_view.blockSignals(True)
        self.map_view.stop()


def load_track_heights(height_series, gnss_times):
    if height_series is None or not gnss_times:
        return [0.0] * len(gnss_times), "m"
    with h5py.File(height_series[0], "r") as h5_file:
        group = _series_group_for_navigator_name(h5_file, height_series[1])
        time_name = _attribute_text(group.attrs.get("time_dataset", "time_unix"))
        unit = _attribute_text(group.attrs.get("unit", "")) or "value"
        # Only the altitude around the track's (sampled) times is read, in bounded chunks.
        try:
            heights = interpolated_values(group[time_name], group["value"],
                                          _time_axis_metadata_from_group(h5_file, group), np.asarray(gnss_times))
        except ValueError as error:
            raise ValueError(f"Altitude series: {error}") from error
    return heights.astype(float).tolist(), unit


def load_3d_gnss_group(axes, *, time_range=None, cancelled=None, maximum_points=MAP_MAXIMUM_POINTS):
    """Load one track; a missing or unusable altitude draws it flat at 0 m."""
    track = load_gnss_group(axes[:2], time_range=time_range, cancelled=cancelled,
                            maximum_points=maximum_points)
    altitude = axes[ALTITUDE_AXIS] if len(axes) > ALTITUDE_AXIS else None
    warning = None
    if altitude is None:
        heights, unit = load_track_heights(None, track["time"])
    else:
        try:
            heights, unit = load_track_heights((altitude.path, altitude.name), track["time"])
        except Exception as error:  # noqa: BLE001 - any unreadable altitude falls back to flat
            warning = str(error) or type(error).__name__
            LOG.warning("Altitude %s cannot be used for 3D GNSS track %s; drawing it flat at 0 m: %s",
                        altitude.name, track["name"], warning)
            heights, unit = load_track_heights(None, track["time"])
    return {"name": track["name"],
            "shown_points": track.get("shown_points"),
            "available_points": track.get("available_points"),
            "coordinates": [[lon, lat, height] for lat, lon, height in zip(track["lat"], track["lon"], heights)],
            # Markers and values boxes use the same per-point fields as the 2D Map.
            **{key: track[key] for key in ("time", "lat", "lon", "device_time", "time_unit") if key in track},
            "height_unit": unit,
            "flat_altitude": altitude is None or warning is not None,
            "altitude_warning": warning}


def _track_altitude_series(h5_path: Path, latitude_name: str) -> tuple[Path, str] | None:
    """Find recorded altitude beside this track's coordinates in the cache."""
    group = latitude_name.rpartition("/")[0].casefold()
    with h5py.File(h5_path, "r") as h5_file:
        names = {
            name.rpartition("/")[2].casefold(): name
            for name in _navigator_series_names(h5_file)
            if name.rpartition("/")[0].casefold() == group
        }
    for altitude_name in ("altitude_m", "altitude", "height_m", "elevation_m"):
        if altitude_name in names:
            return Path(h5_path), names[altitude_name]
    return None


def _three_d_map_html(*, settings: dict[str, object] | None = None, background: str = "#000000") -> str:
    """Return an empty MapLibre/deck.gl document; routes arrive via ``setTracks``."""
    view_settings = script_json({
        "mapOpacity": 1, "altitudeExaggeration": 1, "trackAltitudeOffset": 0, "followGround": False,
        "drawStyle": "auto", **(settings or {}),
    })
    return f'''<!doctype html><html><head><meta charset="utf-8">
<link href="https://unpkg.com/maplibre-gl@5.12.0/dist/maplibre-gl.css" rel="stylesheet">
<style>html,body,#map{{height:100%;margin:0;background:{background}}}</style>
<script src="https://unpkg.com/maplibre-gl@5.12.0/dist/maplibre-gl.js"></script>
<script src="https://unpkg.com/deck.gl@9.2.0/dist.min.js"></script>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
</head><body><div id="map"></div><script>
let tracks=[];
let bridge=null;
let markerState={{times:[null,null],colors:[[255,59,48,255],[0,200,83,255]],width:1,selection:[33,150,243,112]}};
let rightPress=null,lastContextMenu=0,viewTimer=null;
// A camera from a workspace file, shown instead of fitting the first tracks.
let restoreView=null;
new QWebChannel(qt.webChannelTransport,c=>bridge=c.objects.gnssBridge);
const viewSettings={view_settings};
const map=new maplibregl.Map({{container:'map',style:{json.dumps(OPENFREEMAP_STYLE_URL)},center:[24.94,60.17],zoom:12,pitch:55,maxPitch:85,bearing:-18,attributionControl:true}});
let overlay=null, overlayInterleaved=null, terrainAdded=false, mapReady=false;
// The ground height under each track point, from the elevation tiles the terrain uses.
const DEM_TILE_URL={json.dumps(TERRARIUM_TILE_URL)},DEM_ZOOM={DEM_ZOOM};
let demTiles={{}},groundVersion=0,groundWaiters=[];
function loadDemTile(x,y,done){{
  // Terrarium tiles: height = R*256 + G + B/256 - 32768 metres.
  const image=new Image();image.crossOrigin='anonymous';
  image.onload=()=>{{
    const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;
    const context=canvas.getContext('2d');context.drawImage(image,0,0);
    done({{size:image.width,data:context.getImageData(0,0,image.width,image.height).data}});
  }};
  image.onerror=()=>done(null);
  image.src=DEM_TILE_URL.replace('{{z}}',DEM_ZOOM).replace('{{x}}',x).replace('{{y}}',y);
}}
function tilePosition(lon,lat){{
  const n=2**DEM_ZOOM,r=lat*Math.PI/180;
  return [(lon+180)/360*n,(1-Math.log(Math.tan(r)+1/Math.cos(r))/Math.PI)/2*n];
}}
function demHeight(tile,px,py){{
  const s=tile.size,x=Math.max(0,Math.min(s-1,px)),y=Math.max(0,Math.min(s-1,py)),i=(y*s+x)*4,d=tile.data;
  return d[i]*256+d[i+1]+d[i+2]/256-32768;
}}
function groundAt(lon,lat){{
  // Interpolated between the tile's pixels like the terrain; null until its tile has loaded.
  const [x,y]=tilePosition(lon,lat),tx=Math.floor(x),ty=Math.floor(y),tile=demTiles[tx+'/'+ty];
  if(!tile)return null;
  const fx=(x-tx)*tile.size-0.5,fy=(y-ty)*tile.size-0.5,x0=Math.floor(fx),y0=Math.floor(fy),ax=fx-x0,ay=fy-y0;
  return demHeight(tile,x0,y0)*(1-ax)*(1-ay)+demHeight(tile,x0+1,y0)*ax*(1-ay)
    +demHeight(tile,x0,y0+1)*(1-ax)*ay+demHeight(tile,x0+1,y0+1)*ax*ay;
}}
function loadGround(){{
  const missing=new Set();
  tracks.forEach(track=>track.coordinates.forEach(p=>{{
    const [x,y]=tilePosition(p[0],p[1]),key=Math.floor(x)+'/'+Math.floor(y);
    if(!(key in demTiles))missing.add(key);
  }}));
  if(!missing.size){{finishGround();return}}
  let left=missing.size;
  missing.forEach(key=>{{
    demTiles[key]=null;
    const [x,y]=key.split('/').map(Number);
    loadDemTile(x,y,tile=>{{demTiles[key]=tile;if(--left===0)finishGround()}});
  }});
}}
function finishGround(){{
  tracks.forEach(track=>{{track.ground=track.coordinates.map(p=>groundAt(p[0],p[1]))}});
  groundVersion++;
  updateRoute();
  const waiters=groundWaiters;groundWaiters=[];waiters.forEach(waiter=>waiter());
}}
function whenGroundReady(callback){{
  if(tracks.every(track=>track.ground&&track.ground.length===track.coordinates.length))callback();
  else groundWaiters.push(callback);
}}
function useTrackOcclusion(){{
  // Terrain and buildings hide the track behind them while the map shows.
  return viewSettings.mapOpacity>0;
}}
function pointHeight(track,i){{
  const factor=viewSettings.altitudeExaggeration;
  if(viewSettings.followGround){{
    const ground=track.ground?track.ground[i]:null;
    return (ground===null||ground===undefined?0:ground)*factor;
  }}
  return (track.coordinates[i][2]+viewSettings.trackAltitudeOffset)*factor;
}}
function routeLayer(){{
  const parameters={{depthCompare:useTrackOcclusion()?'less-equal':'always',depthWriteEnabled:useTrackOcclusion()}};
  const heightTriggers=[viewSettings.altitudeExaggeration,viewSettings.trackAltitudeOffset,viewSettings.followGround,groundVersion];
  if(viewSettings.drawStyle==='points'){{
    // Each track's positions as dots, at the same heights as the line.
    const points=[];
    tracks.forEach(track=>track.coordinates.forEach((_,i)=>points.push({{track,i}})));
    return new deck.ScatterplotLayer({{
      id:'gnss-3d-route-points',data:points,getPosition:d=>trackPoint(d.track,d.i),
      updateTriggers:{{getPosition:heightTriggers}},getFillColor:d=>d.track.color||[33,150,243],
      getRadius:3,radiusUnits:'pixels',pickable:true,parameters
    }});
  }}
  return new deck.PathLayer({{
    id:'gnss-3d-route',data:tracks,
    getPath:d=>d.coordinates.map((p,i)=>[p[0],p[1],pointHeight(d,i)]),
    updateTriggers:{{getPath:[viewSettings.altitudeExaggeration,viewSettings.trackAltitudeOffset,
      viewSettings.followGround,groundVersion]}},getColor:d=>d.color||[33,150,243],getWidth:5,
    widthUnits:'pixels',jointRounded:true,capRounded:true,pickable:true,
    // deck.gl 9 uses luma.gl depth parameters, not the legacy depthTest flag.
    parameters:{{depthCompare:useTrackOcclusion()?'less-equal':'always',depthWriteEnabled:useTrackOcclusion()}}
  }});
}}
function trackPoint(track,i){{
  const p=track.coordinates[i];return [p[0],p[1],pointHeight(track,i)];
}}
function nearestIndex(track,t){{
  const times=track.time||[];
  if(!times.length||t<times[0]||t>times[times.length-1])return -1;
  let best=0;for(let i=1;i<times.length;i++)if(Math.abs(times[i]-t)<Math.abs(times[best]-t))best=i;return best;
}}
function markerLayers(){{
  const layers=[],times=markerState.times;
  if(times[0]!==null&&times[1]!==null){{
    const left=Math.min(times[0],times[1]),right=Math.max(times[0],times[1]);
    const paths=tracks.map(track=>(track.time||[]).map((t,i)=>t>=left&&t<=right?trackPoint(track,i):null).filter(Boolean)).filter(p=>p.length>1);
    layers.push(new deck.PathLayer({{id:'gnss-3d-selection',data:paths,getPath:d=>d,getColor:markerState.selection,
      getWidth:11,widthUnits:'pixels',jointRounded:true,capRounded:true,
      parameters:{{depthCompare:'always',depthWriteEnabled:false}}}}));
  }}
  const points=[];
  // A Video card's stored sync data marker, as a larger point under the markers.
  if(markerState.syncTime!==null&&markerState.syncTime!==undefined)tracks.forEach(track=>{{
    const i=nearestIndex(track,markerState.syncTime);
    if(i>=0)points.push({{position:trackPoint(track,i),color:markerState.syncColor,radius:10}});
  }});
  times.forEach((t,index)=>{{if(t===null)return;tracks.forEach(track=>{{const i=nearestIndex(track,t);if(i>=0)points.push({{position:trackPoint(track,i),color:markerState.colors[index]}})}})}});
  layers.push(new deck.ScatterplotLayer({{id:'gnss-3d-markers',data:points,getPosition:d=>d.position,getFillColor:d=>d.color,
    getLineColor:[255,255,255],stroked:true,lineWidthUnits:'pixels',getLineWidth:Math.max(1,markerState.width),
    radiusUnits:'pixels',getRadius:d=>d.radius||6+markerState.width,parameters:{{depthCompare:'always',depthWriteEnabled:false}}}}));
  return layers;
}}
function setMarkers(times,colors,width,selection,syncTime=null,syncColor=null){{
  markerState={{times,colors,width,selection,syncTime,syncColor}};
  updateRoute();
}}
function screenPoint(p){{
  // deck.gl's viewport includes the track altitude; MapLibre's projection does not.
  try{{const viewport=overlay&&overlay._deck&&overlay._deck.getViewports()[0];if(viewport){{const q=viewport.project(p);return [q[0],q[1]]}}}}catch(_e){{}}
  const q=map.project([p[0],p[1]]);return [q.x,q.y];
}}
function markerScreenPositions(t){{return tracks.map(track=>{{const i=nearestIndex(track,t);return i<0?null:screenPoint(trackPoint(track,i))}})}}
function nearestTime(point){{
  let best=null;
  tracks.forEach(track=>(track.time||[]).forEach((t,i)=>{{
    const q=screenPoint(trackPoint(track,i)),d=(q[0]-point.x)**2+(q[1]-point.y)**2;
    if(best===null||d<best[0])best=[d,t];
  }}));
  return best===null?null:best[1];
}}
function updateRoute(){{
  if(!mapReady)return;
  const interleaved=useTrackOcclusion();
  // Interleaving is a constructor option. Replace only the overlay when the
  // mode changes, leaving the map, camera and terrain source in place.
  if(overlay&&overlayInterleaved!==interleaved){{
    map.removeControl(overlay);
    overlay=null;
  }}
  if(!overlay){{
    overlay=new deck.MapboxOverlay({{interleaved,layers:[routeLayer(),...markerLayers()]}});
    overlayInterleaved=interleaved;
    map.addControl(overlay);
  }}else{{
    overlay.setProps({{layers:[routeLayer(),...markerLayers()]}});
  }}
}}
function fitTracks(){{
  const coordinates=tracks.reduce((points,track)=>points.concat(track.coordinates),[]);
  if(!coordinates.length)return;
  const bounds=coordinates.reduce((b,p)=>b.extend([p[0],p[1]]),new maplibregl.LngLatBounds(coordinates[0],coordinates[0]));
  map.fitBounds(bounds,{{padding:50,maxZoom:16,duration:0}});
}}
function addRoute(){{
  updateRoute();
  if(tracks.length&&restoreView){{
    map.jumpTo(restoreView);restoreView=null;
  }}else fitTracks();
}}
function setTracks(nextTracks,fit=false){{
  tracks=nextTracks;
  loadGround();
  if(fit&&mapReady)addRoute();else updateRoute();
}}
function reportAutoSettings(){{
  // The web channel may still be connecting when a new card's Auto finishes.
  if(!bridge){{setTimeout(reportAutoSettings,100);return}}
  bridge.report_auto_settings(JSON.stringify({{factor:viewSettings.altitudeExaggeration,offset:viewSettings.trackAltitudeOffset}}));
}}
function autoSettings(computeOffset,first=false){{
  // The offset that puts the track's lowest point relative to the ground exactly on the ground.
  // A new card's first Auto also shows the whole track with heights {AUTO_EXAGGERATION:g}x.
  if(first){{
    viewSettings.altitudeExaggeration={AUTO_EXAGGERATION};
    applyTerrain();
    fitTracks();
  }}
  whenGroundReady(()=>{{
    if(computeOffset){{
      let lowest=null;
      tracks.forEach(track=>track.coordinates.forEach((p,i)=>{{
        const ground=track.ground[i];
        if(ground!==null&&(lowest===null||p[2]-ground<lowest))lowest=p[2]-ground;
      }}));
      if(lowest!==null)viewSettings.trackAltitudeOffset=Math.max(-{MAX_TRACK_OFFSET:g},Math.min({MAX_TRACK_OFFSET:g},-lowest));
    }}
    updateRoute();
    reportAutoSettings();
  }});
}}
map.doubleClickZoom.disable();
map.on('click',e=>{{const t=nearestTime(e.point);if(t!==null&&bridge)bridge.select_timestamp(t)}});
map.on('dblclick',()=>{{if(bridge)bridge.clear_marker()}});
// A right drag tilts and rotates; only a right click without moving places the second marker,
// and a second right click soon after removes it.
map.on('mousedown',e=>{{if(e.originalEvent.button===2)rightPress=e.point}});
map.on('contextmenu',e=>{{
  const press=rightPress;rightPress=null;
  if(!press||Math.hypot(e.point.x-press.x,e.point.y-press.y)>4)return;
  const now=Date.now();
  if(now-lastContextMenu<400){{lastContextMenu=0;if(bridge)bridge.clear_second_marker();return}}
  lastContextMenu=now;
  const t=nearestTime(e.point);if(t!==null&&bridge)bridge.select_second_timestamp(t);
}});
map.on('move',()=>{{if(viewTimer)return;viewTimer=setTimeout(()=>{{
  viewTimer=null;if(!bridge)return;
  bridge.notify_view_changed();
  const c=map.getCenter();
  bridge.report_view(JSON.stringify({{center:[c.lng,c.lat],zoom:map.getZoom(),pitch:map.getPitch(),bearing:map.getBearing()}}));
}},80)}});
map.on('load',()=>{{
  mapReady=true;
  addRoute();
  setMapOpacity(viewSettings.mapOpacity);
}});
function setMapOpacity(opacity){{
  // 0 hides the map and its terrain; the route stays.
  viewSettings.mapOpacity=opacity;
  if(!mapReady)return;
  const paintKeys={{fill:'fill-opacity',line:'line-opacity',symbol:'text-opacity',circle:'circle-opacity',raster:'raster-opacity',background:'background-opacity','fill-extrusion':'fill-extrusion-opacity',heatmap:'heatmap-opacity'}};
  map.getStyle().layers.forEach(layer=>{{
    if(layer.id==='gnss-3d-route')return;
    try{{map.setLayoutProperty(layer.id,'visibility',opacity>0?'visible':'none')}}catch(_e){{}}
    const key=paintKeys[layer.type];
    if(!key)return;
    try{{map.setPaintProperty(layer.id,key,opacity)}}catch(_e){{}}
  }});
  applyTerrain();
  updateRoute();
}}
function applyTerrain(){{
  if(!mapReady)return;
  const shown=viewSettings.mapOpacity>0;
  if(shown&&!terrainAdded){{map.addSource('terrain-dem',{{type:'raster-dem',tiles:[DEM_TILE_URL],tileSize:256,encoding:'terrarium',maxzoom:DEM_ZOOM}});terrainAdded=true;}}
  if(terrainAdded)map.setTerrain(shown?{{source:'terrain-dem',exaggeration:viewSettings.altitudeExaggeration}}:null);
}}
function setAltitudeExaggeration(factor){{
  if(!Number.isFinite(factor))return;
  viewSettings.altitudeExaggeration=Math.max(1,Math.min({MAX_EXAGGERATION:g},factor));
  updateRoute();
  applyTerrain();
}}
function setTrackAltitudeOffset(offset){{
  if(!Number.isFinite(offset))return;
  viewSettings.trackAltitudeOffset=Math.max(-{MAX_TRACK_OFFSET:g},Math.min({MAX_TRACK_OFFSET:g},offset));
  updateRoute();
}}
function setFollowGround(enabled){{
  viewSettings.followGround=Boolean(enabled);
  updateRoute();
}}
function setDrawStyle(style){{
  viewSettings.drawStyle=style;
  updateRoute();
}}
</script></body></html>'''
