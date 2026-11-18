"""Oscilloscope-style time plots with independent vertical transforms."""
from dataclasses import dataclass, field, replace
import math
from pathlib import Path

import h5py
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QPen, QTransform
from PySide6.QtWidgets import QApplication, QGraphicsRectItem

from src.gui.plots.series_style import series_line_width
from src.app.settings import DEFAULT_PLOT_SETTINGS, load_plot_settings
from src.data.h5_series import TimeAxisMetadata, series_value_range
from src.data.plot_policy import PlotSource
from src.gui.plots.base import SERIES_COLORS, apply_view_range_state, first_unused_color, series_legend_name
from src.gui.plots.legend import LegendEntry, PlotLegendWidget
from src.gui.plots.marker_info import MarkerInfoEntry
from src.gui.plots.value_boxes import ValueBoxItem
from src.gui.plots.video.filmstrip import FilmstripFrames
from src.gui.plots.video.widget import folder_start_unix_seconds
from src.gui.plots.event_state import (
    is_event_state_series, load_event_state_series, render_event_state_lane, update_event_state_lane_heights,
)
from src.gui.plots.interactive import InteractivePlotWidget
from src.gui.plots.line import LinePlotWidget
from src.gui.plots.series import PlotSeriesData, SeriesPlotWidget, load_plot_series


class SelectedAxis(pg.AxisItem):
    def __init__(self, owner):
        super().__init__('left')
        self.owner = owner
        self.enableAutoSIPrefix(False)

    def tickValues(self, minVal, maxVal, size):
        gain, offset = self.owner.transforms.get(self.owner.selected, (1, 0))
        if self.owner.event_kinds.get(self.owner.selected):
            return [(1, [offset])] if minVal <= offset <= maxVal else []
        return [(spacing * gain, [value * gain + offset for value in values])
                for spacing, values in super().tickValues((minVal - offset) / gain, (maxVal - offset) / gain, size)]

    def tickStrings(self, values, scale, spacing):
        transform = self.owner.transforms.get(self.owner.selected)
        if transform is None:
            return [''] * len(values)
        gain, offset = transform
        if self.owner.event_kinds.get(self.owner.selected):
            return [self.owner.selected[1]] * len(values)
        raw = [(value - offset) / gain for value in values]
        return super().tickStrings(raw, scale, spacing / gain)


class MultiInteractivePlot(InteractivePlotWidget):
    owner = None

    def mousePressEvent(self, event):
        if self.owner is not None and event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            if self.marker_near(point) is None:
                selected = self.owner.hit_series(point)
                axis = self.getPlotItem().getAxis('left').sceneBoundingRect()
                if selected is not None:
                    self.owner.select_series(selected)
                if selected is not None or (axis.contains(point) and self.owner.selected is not None):
                    self._begin_axis_drag('series_y', event.position())
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._axis_drag_mode == 'series_y':
            if ((event.position() - self._axis_drag_start).manhattanLength() < QApplication.startDragDistance()
                    and self._axis_drag_position == self._axis_drag_start):
                # A click on a trace places a marker; it should not nudge the series.
                event.accept()
                return
            previous =self.interactive_view_box.mapSceneToView(self.mapToScene(self._axis_drag_position.toPoint()))
            current = self.interactive_view_box.mapSceneToView(self.mapToScene(event.position().toPoint()))
            self.owner.move_selected(current.y() - previous.y())
            self._axis_drag_position = event.position()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        point = self.mapToScene(event.position().toPoint())
        clicked = (self.owner is not None and event.button() == Qt.MouseButton.LeftButton
                   and self._axis_drag_start is not None
                   and (event.position() - self._axis_drag_start).manhattanLength()
                   < QApplication.startDragDistance()
                   and self.interactive_view_box.sceneBoundingRect().contains(point))
        if clicked and self._axis_drag_mode == 'series_y':
            # Clicking a trace selects it and still places the first marker.
            self._axis_drag_mode = 'plot_x'
        deselect = clicked and self._axis_drag_mode in ('plot_x', 'marker') and self.owner.hit_series(point) is None
        super().mouseReleaseEvent(event)
        if deselect:
            self.owner.select_series(None)

    def wheelEvent(self, event):
        if self.owner is not None and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            point = self.mapToScene(event.position().toPoint())
            if (not self.interactive_view_box.sceneBoundingRect().contains(point)
                    and self.getPlotItem().getAxis('left').sceneBoundingRect().contains(point)):
                center = self.interactive_view_box.mapSceneToView(point).y()
                self.owner.zoom_selected(1 / 0.85 if event.angleDelta().y() > 0 else 0.85, center)
                event.accept()
                return
        super().wheelEvent(event)


@dataclass
class FilmstripLane:
    """A video drawn as a row of still frames along the time axis."""

    source: object
    data_start: float
    color: str
    center: float
    images: list = field(default_factory=list)
    # Dashed outlines where frames are still being decoded.
    placeholders: list = field(default_factory=list)
    # Drawn around the lane while it is selected.
    outline: object = None

    @property
    def key(self) -> tuple:
        """Selection key, distinct from H5 series keys."""
        return 'video', str(self.path)

    @property
    def path(self) -> Path:
        return Path(self.source.path)

    @property
    def start(self) -> float:
        """Data time of the video's first frame: log start plus the sync offset."""
        return self.data_start + float(self.source.offset)


def _whole_series_range(path, name, *, scan_raw=False):
    """Read full LOD bounds; the optional raw scan is used only by a background reader."""
    try:
        with h5py.File(path, 'r') as h5_file:
            return series_value_range(h5_file, name, scan_raw=scan_raw)
    except (OSError, KeyError, TypeError, ValueError):
        return None


def next_lane_center(centers, lane_height):
    """Return the vertical centre (0-1 of the card) for a newly added series.

    A new series goes one lane above the highest series. When that would leave
    the card, it fills the gaps between lanes: half a lane apart, then a quarter,
    and so on, lowest gap first.
    """
    lane = min(max(float(lane_height), 0.01), 1.0)
    lowest, highest = lane / 2, 1 - lane / 2
    if not centers:
        return lowest
    above = max(centers) + lane
    if above <= highest + 1e-9:
        return max(above, lowest)
    step = lane
    for _ in range(12):
        count = int((highest - lowest) / step + 1e-9) + 1
        for candidate in (lowest + index * step for index in range(count)):
            if all(abs(candidate - center) >= step / 2 - 1e-9 for center in centers):
                return candidate
        step /= 2
    return lowest


class MultiSeriesPlot(SeriesPlotWidget):
    interactive_plot_class = MultiInteractivePlot
    filmstrips_changed = Signal()
    # The selected series or filmstrip changed (None when nothing is selected).
    selected_series_changed = Signal(object)

    def value_box_y(self, series_index, value):
        # Each series is drawn in its own lane, scaled and offset.
        if series_index >= len(self._series_entries):
            return value
        gain, offset = self.transforms.get(self._series_entries[series_index], (1.0, 0.0))
        return value * gain + offset

    def __init__(self, *args, **kwargs):
        self.transforms = {}
        # Raw value at the centre of each series' lane; with its transform it gives the lane centre.
        self.value_midpoints = {}
        self.value_ranges = {}
        # Preferred lane centers survive collective compression and horizontal reloads.
        self._collective_centers = {}
        self.lane_height = DEFAULT_PLOT_SETTINGS['multi_series_lane_height_percent'] / 100
        self.selected = None
        # Seconds each H5 series is shown later than its data; filmstrips use their video's sync offset.
        self.series_offsets: dict = {}
        self.state_labels = {}
        self.event_kinds = {}
        self.sample_times = {}
        self.timeline_data = {}
        self.timeline_items = {}
        self.filmstrips: list[FilmstripLane] = []
        self.filmstrip_height = DEFAULT_PLOT_SETTINGS['multi_filmstrip_height_pixels']
        # A view restored from a workspace file is kept when a filmstrip's video information arrives.
        self.view_restored = False
        super().__init__(*args, **kwargs)
        self._frames = FilmstripFrames(self)
        # Ask again after each load: the view may have moved on. Times without a frame are
        # cached too, so this stops once every visible slot is known.
        self._frames.loaded.connect(lambda _path: self._refresh_filmstrips())
        self._frames.info_loaded.connect(self._filmstrip_info_loaded)
        self._frames.failed.connect(
            lambda path, error: self.status_changed.emit(f"Cannot read frames from {Path(path).name}: {error}"))
        # Frames are requested once zooming or panning pauses; placed frames follow at once.
        self._filmstrip_timer = QTimer(self)
        self._filmstrip_timer.setSingleShot(True)
        self._filmstrip_timer.setInterval(150)
        self._filmstrip_timer.timeout.connect(self._refresh_filmstrips)
        for changed in (self.plot_item.vb.sigRangeChanged, self.plot_item.vb.sigResized):
            changed.connect(self._filmstrip_view_changed)
        self.plot_widget.owner = self
        self.selected_axis = SelectedAxis(self)
        self.plot_item.setAxisItems({'left': self.selected_axis})
        self.plot_item.vb.sigResized.connect(self._update_event_heights)
        self.plot_item.vb.sigYRangeChanged.connect(self._update_event_heights)

    def _load_entry(self, path, name, *, maximum_points, x_range=None):
        """Read one series without changing plot state; safe off the GUI thread."""
        key = (Path(path), name)
        # A series with a time offset is read at its own times and drawn shifted.
        shift = float(self.series_offsets.get(key, 0.0))
        data_range = None if x_range is None or not shift else (x_range[0] - shift, x_range[1] - shift)
        if data_range is not None:
            x_range = data_range
        is_event = self.event_kinds.get(key)
        if is_event is None:
            is_event = is_event_state_series(path, name)
        if not is_event:
            data = load_plot_series(path, name, maximum_points=maximum_points, x_range=x_range)
            if shift:
                data = replace(data, x=data.x + shift)
            # The whole-series range keeps a new lane's scale independent of the current zoom.
            value_range, range_ready = None, False
            if key not in self.value_ranges:
                background = QThread.currentThread() != self.thread()
                value_range = _whole_series_range(path, name, scan_raw=background and x_range is not None)
                if value_range is None and x_range is None:
                    # The initial load already contains the full raw series or a full
                    # LOD envelope, so do not reread the file just to calculate bounds.
                    finite = data.y[np.isfinite(data.y)]
                    if len(finite):
                        value_range = (float(finite.min()), float(finite.max()))
                range_ready = value_range is not None or x_range is None or background
            return key, False, data, None, value_range, range_ready
        data = load_event_state_series(path, name, time_range=x_range)
        if shift:
            data = {**data, 'time': np.asarray(data['time'], dtype=float) + shift}
        return key, True, PlotSeriesData(data['time'], data['value'], name, str(data['unit'])), data, None, False

    def _entry_loader(self, *, primary):
        if primary and self.plot_mode == 'timing_delta':
            return super()._entry_loader(primary=primary)
        return self._load_entry

    def _accept_entry_payload(self, h5_path, series_name, payload):
        if isinstance(payload, PlotSeriesData):
            return payload
        key, is_event, series_data, event_data, value_range, range_ready = payload
        self.event_kinds[key] = is_event
        if range_ready:
            self.value_ranges[key] = value_range
            if value_range is not None and key in self.value_midpoints:
                self.value_midpoints[key] = value_range[0] / 2 + value_range[1] / 2
                self._collective_centers.clear()
        elif not is_event and key not in self.value_ranges:
            # An initial partial view without LOD bounds needs a background scan.
            self._view_update_timer.start()
        if event_data is not None:
            self.state_labels[key] = event_data['labels']
            self.sample_times[key] = series_data.x
            self.timeline_data[key] = event_data
        return series_data

    def load_data(self, path, name, *, maximum_points, x_range=None):
        return self._accept_entry_payload(
            path, name, self._load_entry(path, name, maximum_points=maximum_points, x_range=x_range))

    def show_series_entries(self, entries):
        # Selection follows the user's choice, never the new primary series.
        # The base renderer temporarily loads only the first entry while rebuilding.
        if self.selected not in entries and self._selected_filmstrip() is None:
            self.selected = None
        if set(entries) != set(self.transforms):
            self._collective_centers.clear()
        result = super().show_series_entries(entries)
        self.transforms = {key: value for key, value in self.transforms.items() if key in entries}
        self.value_midpoints = {key: value for key, value in self.value_midpoints.items() if key in entries}
        self.value_ranges = {key: value for key, value in self.value_ranges.items() if key in entries}
        self.sample_times = {key: value for key, value in self.sample_times.items() if key in entries}
        self.series_offsets = {key: value for key, value in self.series_offsets.items() if key in entries}
        self.rebuild_timelines()
        self.apply_transforms()
        return result

    def nearest_visible_x(self, x_value):
        arrays = [self.sample_times.get(key, data.x) for key, _, _, data in self.items() if data is not None]
        arrays = [x[np.isfinite(x)] for x in arrays]
        candidates = [float(x[np.argmin(abs(x - x_value))]) for x in arrays if len(x)]
        return min(candidates, key=lambda x: abs(x - x_value)) if candidates else x_value

    def items(self):
        if self.h5_path is not None:
            yield (self.h5_path, self.series_name), self.curve, self.markers, self._current_data
        for entry in self.additional_series:
            yield (entry.h5_path, entry.series_name), entry.curve, entry.markers, entry.data

    def _apply_visible_data(self, results, *, auto_range):
        # Shared by synchronous refreshes and background pan/zoom loads.
        data = super()._apply_visible_data(results, auto_range=auto_range)
        if data is not None:
            self.rebuild_timelines()
            for key, curve, markers, values in self.items():
                if key not in self.transforms and values is not None:
                    self._place_in_lane(key, values)
            if auto_range:
                # Lanes are fractions of the card height, so the card spans 0-1.
                self.plot_item.setYRange(0, 1, padding=0)
            self.apply_transforms()
        return data

    def lane_centers(self):
        """Current vertical centres of placed series and filmstrips, including manual moves and scaling."""
        return [midpoint * self.transforms[key][0] + self.transforms[key][1]
                for key, midpoint in self.value_midpoints.items() if key in self.transforms] + [
            lane.center for lane in getattr(self, 'filmstrips', [])]

    def add_filmstrip(self, source):
        """Add a video as a filmstrip lane; return why it cannot be added, or None."""
        path = Path(source.path)
        if any(lane.path == path for lane in self.filmstrips):
            return None
        data_start = folder_start_unix_seconds(Path(source.folder or path.parent))
        if data_start is None:
            return f"{path.name}: the video's data folder has no log start time"
        metadata = self.time_axis_metadata
        if self._series_entries and metadata is not None and not (
                metadata.is_unix_time or metadata.log_start_unix_seconds is not None):
            return f"{path.name}: this card's series have no absolute time to align the video with"
        used = self.series_colors() + [lane.color for lane in self.filmstrips]
        color = first_unused_color(SERIES_COLORS, used, len(used))
        self.filmstrips.append(FilmstripLane(source, data_start, color, next_lane_center(self.lane_centers(), self.lane_height)))
        if not self._series_entries:
            self._use_video_time_axis(data_start)
        self._refresh_filmstrips()
        self.filmstrips_changed.emit()
        return None

    def set_filmstrip_offset(self, video_path, offset):
        """Move a filmstrip after its video's sync offset changes."""
        for lane in self.filmstrips:
            if lane.path.resolve() == Path(video_path).resolve():
                lane.source = replace(lane.source, offset=float(offset))
        self._refresh_filmstrips()
        self.filmstrips_changed.emit()

    def remove_filmstrip(self, index):
        if 0 <= index < len(self.filmstrips):
            lane = self.filmstrips.pop(index)
            for item in lane.images + lane.placeholders + [lane.outline]:
                if item is not None:
                    self.plot_item.removeItem(item)
            if self.selected == lane.key:
                self.selected = None
            self.filmstrips_changed.emit()

    def _filmstrip_at(self, view_point):
        """The filmstrip lane drawn at a point in view coordinates."""
        view = self.plot_item.vb
        _, (bottom, top) = view.viewRange()
        if view.height() <= 0:
            return None
        half = self.filmstrip_height * (top - bottom) / view.height() / 2
        for lane in reversed(self.filmstrips):
            info = self._frames.info.get(str(lane.path))
            end = lane.start + info.duration if info is not None and info.duration > 0 else math.inf
            if abs(view_point.y() - lane.center) <= half and lane.start <= view_point.x() <= end:
                return lane
        return None

    def _selected_filmstrip(self):
        return next((lane for lane in self.filmstrips if lane.key == self.selected), None)

    def filmstrip_time_ranges(self):
        ranges = []
        for lane in self.filmstrips:
            info = self._frames.info.get(str(lane.path))
            if info is not None and info.duration > 0:
                ranges.append((lane.start, lane.start + info.duration))
        return ranges

    def _use_video_time_axis(self, data_start):
        """Without H5 series, the card's time axis is Unix time, like the video's data folder."""
        self.time_axis_metadata = TimeAxisMetadata(data_start, 1.0, data_start, True)
        self.set_time_axis_display_metadata(self.time_axis_metadata)

    def _filmstrip_info_loaded(self, path, info):
        lane = next((lane for lane in self.filmstrips if lane.path == Path(path)), None)
        if (lane is not None and not self._series_entries and len(self.filmstrips) == 1 and info.duration > 0
                and not self.view_restored):
            # A card opened from a video alone shows the whole video.
            self.plot_item.vb.setXRange(lane.start, lane.start + info.duration, padding=0)
        self.filmstrips_changed.emit()
        # The aspect ratio sets the frame slots, so request frames for the new slots.
        self._refresh_filmstrips()

    def _filmstrip_view_changed(self, *_args):
        if self.filmstrips:
            self._refresh_filmstrips(request=False)
            self._filmstrip_timer.start()

    def _filmstrip_slots(self, lane):
        """Frame slots across the visible time range: (left, width, video seconds)."""
        view = self.plot_item.vb
        (left, right), _ = view.viewRange()
        width_px = view.width()
        info = self._frames.info.get(str(lane.path))
        aspect = info.aspect if info is not None else 16 / 9
        if width_px <= 0 or right <= left:
            return []
        frame_px = max(8.0, self.filmstrip_height * aspect)
        # A grid aligned to the frame width keeps frame times stable while panning.
        step = (right - left) * frame_px / width_px
        first = math.floor(left / step)
        slots = []
        for index in range(first, first + int(width_px // frame_px) + 2):
            slot_left = index * step
            seconds = slot_left + step / 2 - lane.start
            if seconds < 0 or (info is not None and info.duration > 0 and seconds > info.duration):
                continue
            slots.append((slot_left, step, round(seconds, 3)))
        return slots

    def _refresh_filmstrips(self, *, request=True):
        """Place each lane's cached frames, requesting the missing ones when the view settles.

        A slot whose frame is still being decoded shows a dashed outline instead.
        """
        view = self.plot_item.vb
        (left, right), (bottom, top) = view.viewRange()
        if view.height() <= 0 or top <= bottom:
            return
        height = self.filmstrip_height * (top - bottom) / view.height()
        for lane in self.filmstrips:
            slots = self._filmstrip_slots(lane)
            while len(lane.images) < len(slots):
                image = pg.ImageItem(axisOrder='row-major')
                # Below the series curves and markers.
                image.setZValue(8)
                self.plot_item.addItem(image, ignoreBounds=True)
                lane.images.append(image)
            while len(lane.placeholders) < len(slots):
                placeholder = QGraphicsRectItem()
                placeholder.setZValue(8)
                self.plot_item.addItem(placeholder, ignoreBounds=True)
                lane.placeholders.append(placeholder)
            pending = QPen(QColor(lane.color), 1, Qt.PenStyle.DashLine)
            pending.setCosmetic(True)
            failed = str(lane.path) in self._frames.failed_paths
            missing = []
            # An image keeps the frame it shows: panning moves it to that frame's new slot
            # instead of uploading every frame again (`shown_frame`).
            showing = {id(image.shown_frame): image for image in lane.images
                       if getattr(image, "shown_frame", None) is not None}
            placed = []
            for placeholder, (slot_left, step, seconds) in zip(lane.placeholders, slots):
                rect = QRectF(slot_left, lane.center - height / 2, step, height)
                frame = self._frames.frame(lane.path, seconds, self.filmstrip_height)
                if frame is None:
                    known = self._frames.known(lane.path, seconds, self.filmstrip_height)
                    if not known:
                        missing.append(seconds)
                    # Inset so neighbouring outlines stay apart.
                    placeholder.setRect(rect.adjusted(step * 0.04, height * 0.04, -step * 0.04, -height * 0.04))
                    placeholder.setPen(pending)
                    placeholder.setVisible(not known and not failed)
                    continue
                placeholder.hide()
                placed.append((rect, frame, showing.pop(id(frame), None)))
            in_use = {id(image) for _rect, _frame, image in placed if image is not None}
            free = [image for image in lane.images if id(image) not in in_use]
            for rect, frame, image in placed:
                if image is None:
                    image = free.pop(0)
                    # Image rows run top down; the view's Y axis runs bottom up.
                    image.setImage(frame[::-1], autoLevels=False, levels=(0, 255))
                    image.shown_frame = frame
                image.setRect(rect)
                image.show()
            for image in free:
                image.hide()
            for item in lane.placeholders[len(slots):]:
                item.hide()
            self._update_filmstrip_outline(lane, left, right, height)
            if request and (missing or str(lane.path) not in self._frames.info):
                self._frames.request(lane.path, missing, self.filmstrip_height)

    def _update_filmstrip_outline(self, lane, left, right, height):
        """Outline the selected lane over its visible span."""
        if lane.outline is None:
            lane.outline = QGraphicsRectItem()
            lane.outline.setZValue(9)
            self.plot_item.addItem(lane.outline, ignoreBounds=True)
        info = self._frames.info.get(str(lane.path))
        end = lane.start + info.duration if info is not None and info.duration > 0 else right
        span_left, span_right = max(left, lane.start), min(right, end)
        selected = lane.key == self.selected and span_right > span_left
        if selected:
            pen = QPen(QColor(lane.color), series_line_width(selected=True))
            pen.setCosmetic(True)
            lane.outline.setPen(pen)
            lane.outline.setRect(QRectF(span_left, lane.center - height / 2, span_right - span_left, height))
        lane.outline.setVisible(selected)

    def extra_marker_box_items(self, x_value):
        """Each filmstrip's video time at the marker."""
        items = []
        for lane in self.filmstrips:
            info = self._frames.info.get(str(lane.path))
            seconds = x_value - lane.start
            if info is None or not 0 <= seconds <= info.duration:
                continue
            entry = MarkerInfoEntry(lane.path.name, lane.color, f"{seconds:.3f} s", (
                f"Video time: {seconds:.3f} s of {info.duration:.3f} s",
                f"Sync offset: {float(lane.source.offset):+.3f} s",
                f"File: {lane.path}",
                "Frames show the nearest keyframe at or before their time.",
            ))
            items.append(ValueBoxItem(('video', str(lane.path)), entry, lane.center))
        return items

    def dispose(self):
        super().dispose()
        self._frames.shutdown()

    def _place_in_lane(self, key, values):
        """Scale a new series to one lane and stack it above the others."""
        lane = self.lane_height
        if self.event_kinds.get(key):
            # Shared timeline rendering sets font-sized bar heights in screen pixels.
            midpoint, gain = 0.0, 1.0
        else:
            value_range = self.value_ranges.get(key)
            if value_range is None:
                finite = values.y[np.isfinite(values.y)]
                if not len(finite):
                    return
                value_range = (float(finite.min()), float(finite.max()))
            low, high = value_range
            midpoint = (low + high) / 2
            gain = lane / (high - low) if high > low else lane
        center = next_lane_center(self.lane_centers(), lane)
        self.value_midpoints[key] = midpoint
        self.transforms[key] = (gain, center - midpoint * gain)

    def rebuild_timelines(self):
        for items in self.timeline_items.values():
            for item in items:
                self.plot_item.removeItem(item)
        self.timeline_items.clear()
        for index, (key, _, _, data) in enumerate(self.items()):
            if data is not None and key in self.timeline_data:
                self.timeline_items[key] = render_event_state_lane(
                    self.plot_item, 0, self.timeline_data[key], self.series_colors()[index])

    def diagnostic_value_range(self, series_index=0):
        """A series' lane: its whole-series minimum to maximum as drawn."""
        key = self._series_entries[series_index] if series_index < len(self._series_entries) else None
        if key is None or key not in self.transforms or self.event_kinds.get(key):
            return None
        value_range = self.value_ranges.get(key)
        if value_range is None:
            return None
        gain, offset = self.transforms[key]
        return value_range[0] * gain + offset, value_range[1] * gain + offset

    def set_draw_style(self, style):
        super().set_draw_style(style)
        # Lanes place and scale the points too; event lanes keep their own drawing.
        self.apply_transforms()

    def apply_transforms(self):
        self._update_diagnostic_region_spans()
        colors = self.series_colors()
        for index, (key, curve, markers, data) in enumerate(self.items()):
            if data is not None and curve.opts.get('connect') != 'finite':
                # Keep event stems and missing-data gaps separate. Qt's default
                # 'all' mode connects across NaN separators.
                curve.setData(data.x, data.y, connect='finite')
            gain, offset = self.transforms.get(key, (1, 0))
            transform = QTransform(1, 0, 0, gain, 0, offset)
            curve.setTransform(transform)
            markers.setTransform(transform)
            curve.setPen(pg.mkPen(colors[index], width=series_line_width(selected=key == self.selected)))
            curve.setZValue(15 if key == self.selected else 10)
            if self.event_kinds.get(key):
                markers.clear()
                curve.hide()
                for item in self.timeline_items.get(key, []):
                    if isinstance(item, pg.TextItem):
                        item.setY(offset)
                    else:
                        item.setTransform(transform)
                        pen = pg.mkPen(item.opts['pen'])
                        pen.setWidth(series_line_width(selected=key == self.selected))
                        item.setOpts(pen=pen)
                    item.setZValue(16 if key == self.selected else 12)
            else:
                curve.setVisible(self.draw_style != 'points')
            if key == self.selected and data is not None:
                self.plot_item.setLabel('left', '' if self.event_kinds.get(key) else key[1],
                                        units=None if self.event_kinds.get(key) else data.unit or None)
        self.selected_axis.picture = None
        if self.selected is None or self._selected_filmstrip() is not None:
            self.plot_item.setLabel('left', '', units=None)
        self.selected_axis.update()
        self._update_event_heights()

    def _update_event_heights(self, *_args):
        for items in self.timeline_items.values():
            update_event_state_lane_heights(self.plot_item, items)

    def select_series(self, key):
        changed = key != self.selected
        self.selected = key
        self.apply_transforms()
        if self.filmstrips:
            self._refresh_filmstrips(request=False)
        self.parent().activated.emit(self.parent())
        if changed:
            self.selected_series_changed.emit(key)

    def entry_time_offsets(self):
        offsets = [float(self.series_offsets.get(entry, 0.0)) for entry in self._series_entries]
        return offsets if any(offsets) else None

    def series_time_offset(self, key):
        """A series' own time offset, or a filmstrip's video sync offset."""
        lane = next((lane for lane in self.filmstrips if lane.key == key), None)
        if lane is not None:
            return float(lane.source.offset)
        return float(self.series_offsets.get(key, 0.0))

    def set_series_time_offset(self, key, seconds):
        """Show one H5 series earlier or later; its data and the other series stay put."""
        seconds = float(seconds)
        if abs(seconds) < 0.0005:
            self.series_offsets.pop(key, None)
        else:
            self.series_offsets[key] = seconds
        self.sample_times.pop(key, None)
        if self.h5_path is not None and self.series_name:
            self._request_visible_data()
        self.refresh_marker_data()

    def move_selected(self, delta):
        lane = self._selected_filmstrip()
        if lane is not None:
            # Filmstrips keep a fixed screen height; dragging only moves the lane.
            lane.center += delta
            self._refresh_filmstrips(request=False)
            return
        if self.selected in self.transforms:
            self._collective_centers.clear()
            gain, offset = self.transforms[self.selected]
            self.transforms[self.selected] = (gain, offset + delta)
            self.apply_transforms()

    def zoom_selected(self, factor, center):
        """Scale selected or unselected numeric series; event/state lanes stay fixed."""
        if self.selected is None:
            self.zoom_all(factor)
        elif self.selected in self.transforms and not self.event_kinds.get(self.selected):
            gain, offset = self.transforms[self.selected]
            if 1e-15 < gain * factor < 1e15:
                self._collective_centers.clear()
                self.transforms[self.selected] = (gain * factor, center + (offset - center) * factor)
                self.apply_transforms()

    def zoom_all(self, factor):
        """Enlarge numeric lanes about their centers, compressing gaps to avoid clipping.

        A common compression towards the view center preserves vertical order,
        even when lanes overlap completely. Recompute it from preferred centers
        on each wheel step so zooming out restores spacing without accumulating drift.
        """
        if not np.isfinite(factor) or factor <= 0:
            return
        bounds = {}
        for key in self.transforms:
            if self.event_kinds.get(key):
                continue
            elif self.value_ranges.get(key) is not None:
                bounds[key] = self.value_ranges[key]
        if not bounds:
            return
        low, high = self.plot_item.vb.viewRange()[1]
        view_center = (low + high) / 2
        half_height = (high - low) * .45  # Five percent margin at each edge.
        if half_height <= 0:
            return
        minimum_factor, maximum_factor = 0.0, np.inf
        for key, (raw_low, raw_high) in bounds.items():
            gain, offset = self.transforms[key]
            midpoint = raw_low / 2 + raw_high / 2
            self._collective_centers.setdefault(key, midpoint * gain + offset)
            minimum_factor = max(minimum_factor, 1e-15 / gain)
            maximum_factor = min(maximum_factor, 1e15 / gain)
            height = (raw_high - raw_low) * gain
            if height > 0:
                maximum_factor = min(maximum_factor, 2 * half_height / height)
        factor = min(max(factor, minimum_factor), maximum_factor)
        compression = 1.0
        for key, (raw_low, raw_high) in bounds.items():
            gain = self.transforms[key][0] * factor
            distance = abs(self._collective_centers[key] - view_center)
            if distance:
                room = half_height - (raw_high - raw_low) * gain / 2
                compression = min(compression, max(0.0, room / distance))
        for key, (raw_low, raw_high) in bounds.items():
            gain = self.transforms[key][0] * factor
            midpoint = raw_low / 2 + raw_high / 2
            center = view_center + compression * (self._collective_centers[key] - view_center)
            self.transforms[key] = (gain, center - midpoint * gain)
        self.apply_transforms()

    def series_name_at(self, position):
        """The name of the trace, lane or filmstrip under a canvas position, for the hover name."""
        key = self.hit_series(self.plot_widget.mapToScene(position))
        if key is None:
            return None
        lane = next((lane for lane in self.filmstrips if lane.key == key), None)
        return lane.path.name if lane is not None else series_legend_name(*key)

    def hit_series(self, point):
        """Hit-test displayed segments in pixels, independent of each Y scale."""
        view = self.plot_item.getViewBox()
        if not view.sceneBoundingRect().contains(point):
            return None
        p = view.mapSceneToView(point)
        lane = self._filmstrip_at(p)
        if lane is not None:
            return lane.key
        dx, dy = view.viewPixelSize()
        best, selected = 8.0 ** 2, None
        for key, curve, markers, data in self.items():
            if data is None or not len(data.x):
                continue
            gain, offset = self.transforms.get(key, (1, 0))
            if self.event_kinds.get(key):
                for item in self.timeline_items.get(key, []):
                    if isinstance(item, pg.BarGraphItem):
                        local = item.mapFromScene(point)
                        opts = item.opts
                        if np.any((opts['x0'] <= local.x()) & (opts['x1'] >= local.x())
                                  & (opts['y0'] <= local.y()) & (opts['y1'] >= local.y())):
                            return key
                continue
            x, y = (data.x - p.x()) / dx, (data.y * gain + offset - p.y()) / dy
            distances = x * x + y * y
            if len(x) > 1:
                vx, vy = np.diff(x), np.diff(y)
                length = vx * vx + vy * vy
                t = np.clip(np.divide(-(x[:-1] * vx + y[:-1] * vy), length,
                                      out=np.zeros_like(length), where=length > 0), 0, 1)
                distances = np.concatenate((distances, (x[:-1] + t * vx) ** 2 + (y[:-1] + t * vy) ** 2))
            finite = distances[np.isfinite(distances)]
            if len(finite) and finite.min() < best:
                best, selected = float(finite.min()), key
        return selected

    def set_series_color(self, index, color):
        super().set_series_color(index, color)
        self.rebuild_timelines()
        self.apply_transforms()

    def reload_plot_settings(self):
        super().reload_plot_settings()
        settings = load_plot_settings(self.settings_path)
        # Applies to series added later; placed series keep their lane.
        self.lane_height = settings['multi_series_lane_height_percent'] / 100
        self.filmstrip_height = settings['multi_filmstrip_height_pixels']
        if hasattr(self, '_filmstrip_timer'):
            self._refresh_filmstrips()
        if hasattr(self, 'selected_axis'):
            self.apply_transforms()


class MultiPlotWidget(LinePlotWidget):
    """A base.py plot card combining line and event/state series, and video filmstrips.

    A card opened from a video alone is created with an empty ``series_name``.
    """
    help_topic = "multi"
    series_plot_class = MultiSeriesPlot
    filmstrips_changed = Signal()
    # A selected filmstrip's offset was edited: its video's folder, path and new sync offset.
    filmstrip_offset_edited = Signal(object, object, float)
    # A Video card dragged onto this card's legend adds its video as a filmstrip.
    accepts_filmstrip_drop = True

    def __init__(self, h5_path, series_name, *args, **kwargs):
        super().__init__(h5_path, series_name, *args, **kwargs)
        self.series_plot.filmstrips_changed.connect(self._filmstrips_changed)
        self.series_plot.selected_series_changed.connect(self._selected_series_changed)
        # A filmstrip's offset is its video's sync offset; it is saved once it stops changing.
        self._pending_filmstrip_offset = None
        self._filmstrip_offset_timer = QTimer(self)
        self._filmstrip_offset_timer.setSingleShot(True)
        self._filmstrip_offset_timer.setInterval(500)
        self._filmstrip_offset_timer.timeout.connect(self._emit_filmstrip_offset)

    # With a series or filmstrip selected, the time offset controls move only that one.
    def _selected_offset_key(self):
        key = self.series_plot.selected
        if key in self.series or any(lane.key == key for lane in self.series_plot.filmstrips):
            return key
        return None

    def _selected_series_changed(self, key):
        self.hide_time_offset_editor()
        self._show_time_offset()
        self._selected_series = key
        self.show_selected_series_in_legend()

    def _select_series_at(self, position):
        """Multi-series selects on press, in its own plot (it also drags the selected trace)."""

    def apply_series_widths(self):
        self.series_plot.apply_transforms()

    def selected_legend_rows(self):
        key = self.series_plot.selected
        if key in self.series:
            return (self.series.index(key),), None
        lanes = [lane.key for lane in self.series_plot.filmstrips]
        return ((len(self.series) + lanes.index(key),) if key in lanes else ()), None

    def edited_time_offset(self):
        key = self._selected_offset_key()
        return self.time_offset if key is None else self.series_plot.series_time_offset(key)

    def edited_time_offset_name(self):
        key = self._selected_offset_key()
        if key is None:
            return None
        lane = next((lane for lane in self.series_plot.filmstrips if lane.key == key), None)
        return lane.path.name if lane is not None else series_legend_name(*key)

    def set_edited_time_offset(self, seconds):
        key = self._selected_offset_key()
        if key is None:
            self.set_time_offset(seconds)
            return
        seconds = float(seconds)
        if abs(seconds - self.series_plot.series_time_offset(key)) < 0.0005:
            return
        lane = next((lane for lane in self.series_plot.filmstrips if lane.key == key), None)
        if lane is not None:
            self.series_plot.set_filmstrip_offset(lane.path, seconds)
            self._pending_filmstrip_offset = (Path(lane.source.folder or lane.path.parent), lane.path, seconds)
            self._filmstrip_offset_timer.start()
        else:
            self.series_plot.set_series_time_offset(key, seconds)
        self._show_time_offset()
        # The card's offset is unchanged; this refreshes its Plot navigator bar and markers.
        self.time_offset_changed.emit(self, self.time_offset)

    def _emit_filmstrip_offset(self):
        if self._pending_filmstrip_offset is not None:
            pending, self._pending_filmstrip_offset = self._pending_filmstrip_offset, None
            self.filmstrip_offset_edited.emit(*pending)

    def series_time_offset(self, entry):
        return float(self.series_plot.series_offsets.get(entry, 0.0))

    def has_time_offset(self):
        return super().has_time_offset() or bool(self.series_plot.series_offsets)

    def reset_time_offset(self):
        """Show the card and every H5 series at their own times; filmstrips keep their sync."""
        for key in list(self.series_plot.series_offsets):
            self.series_plot.set_series_time_offset(key, 0.0)
        previous = self.time_offset
        self.set_time_offset(0.0)
        if abs(previous) < 0.0005:
            self._show_time_offset()
            self.time_offset_changed.emit(self, self.time_offset)

    def dispose(self):
        if self._filmstrip_offset_timer.isActive():
            self._filmstrip_offset_timer.stop()
            self._emit_filmstrip_offset()
        super_dispose = getattr(super(), "dispose", None)
        if callable(super_dispose):
            super_dispose()

    def on_series_changed(self):
        # A video-only card has no H5 series.
        self.series = [entry for entry in self.series if entry[1]]
        super().on_series_changed()
        if hasattr(self, 'legend'):
            self._update_legend()

    def add_filmstrip(self, source):
        return self.series_plot.add_filmstrip(source)

    def workspace_state(self):
        plot = self.series_plot
        state = super().workspace_state()
        # Each series' lane: its manual move and scaling, kept as drawn.
        state['lanes'] = [{
            'transform': list(plot.transforms[entry]) if entry in plot.transforms else None,
            'midpoint': plot.value_midpoints.get(entry),
            'value_range': list(plot.value_ranges[entry]) if plot.value_ranges.get(entry) else None,
            'time_offset': plot.series_offsets.get(entry),
        } for entry in self.series]
        state['filmstrips'] = [{
            'path': str(lane.path), 'folder': str(lane.source.folder or lane.path.parent),
            'offset': float(lane.source.offset), 'center': lane.center, 'color': lane.color,
        } for lane in plot.filmstrips]
        return state

    def restore_workspace_state(self, state):
        plot = self.series_plot
        if not state.get('series'):
            # A video-only card keeps its filmstrip's time axis.
            state = {key: value for key, value in state.items() if key != 'series'}
        super().restore_workspace_state(state)
        for entry, lane in zip(self.series, state.get('lanes', [])):
            if lane.get('transform'):
                plot.transforms[entry] = tuple(float(value) for value in lane['transform'])
            if lane.get('midpoint') is not None:
                plot.value_midpoints[entry] = float(lane['midpoint'])
            if lane.get('value_range'):
                plot.value_ranges[entry] = tuple(float(value) for value in lane['value_range'])
            if isinstance(lane.get('time_offset'), (int, float)):
                plot.set_series_time_offset(entry, float(lane['time_offset']))
        for saved in state.get('filmstrips', []):
            path = Path(saved['path'])
            if not any(lane.path == path for lane in plot.filmstrips):
                problem = plot.add_filmstrip(PlotSource('video', path, folder=Path(saved['folder']),
                                                        offset=float(saved.get('offset', 0.0))))
                if problem is not None:
                    plot.status_changed.emit(f'Cannot add video filmstrip: {problem}')
                    continue
            lane = next(lane for lane in plot.filmstrips if lane.path == path)
            lane.center = float(saved.get('center', lane.center))
            lane.color = str(saved.get('color', lane.color))
        plot.view_restored = True
        plot.apply_transforms()
        plot._refresh_filmstrips()
        self._update_legend()
        self._show_time_offset()
        apply_view_range_state(self.plot_view_box(), state.get('view'))

    def filmstrip_time_ranges(self):
        return self.series_plot.filmstrip_time_ranges()

    def _filmstrips_changed(self):
        self._update_legend()
        self.filmstrips_changed.emit()

    def _update_legend(self):
        """Series rows, then one row per filmstrip; a filmstrip row can be dragged but not recolored."""
        lanes = self.series_plot.filmstrips
        entries = [LegendEntry(index, series_legend_name(*entry), color, entry)
                   for index, (entry, color) in enumerate(zip(self.series, self.series_plot.series_colors()))]
        entries += [LegendEntry(len(self.series) + index, lane.path.name, lane.color,
                                color_editable=False, drag_source=lane.source)
                    for index, lane in enumerate(lanes)]
        PlotLegendWidget.set_entries(self.legend, entries)
        self.legend.setVisible(bool(entries))
        self.legend.reposition()

    def remove_series_at(self, index):
        """Remove a series or filmstrip row; close the card once neither is left."""
        if index >= len(self.series):
            self.series_plot.remove_filmstrip(index - len(self.series))
        elif 0 <= index < len(self.series):
            del self.series[index]
            self.on_series_changed()
        if not self.series and not self.series_plot.filmstrips:
            self.close_requested.emit(self)
