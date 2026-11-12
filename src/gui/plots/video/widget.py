"""INSV/LRV/MP4 video card with LODGaZilla marker synchronization."""
from pathlib import Path
import logging
import queue
import sys
import time

import h5py
from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QSlider,
                               QSpinBox, QStackedLayout, QStyle, QStyleOptionSlider,
                               QToolButton, QVBoxLayout, QWidget)
from src.data.media import MEDIA_KIND_ATTR
from src.data.plot_policy import PlotSource
from src.gui.plots.base import PlotBaseWidget
from src.gui.plots.icons import plot_control_icon
from src.gui.plots.theme import canvas_background
from src.gui.plots.markers import SELECTION_BRUSH, sync_marker_color, marker_color, marker_width
from .audio import AudioOutput
from .decoder import Session
from .viewport import Viewport

LOG = logging.getLogger('lodgazilla.video')


class TimelineSlider(QSlider):
    """Seek to a clicked point while retaining Qt's native handle dragging.

    Also shows the first and second markers, and the selection between them,
    at their positions in the video.
    """

    seek_requested = Signal(int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Marker positions in slider units (ms); None when not inside this video.
        self.marks: list[int | None] = [None, None]
        self.sync_mark: int | None = None

    def set_marks(self, first: int | None, second: int | None) -> None:
        if [first, second] != self.marks:
            self.marks = [first, second]
            self.update()

    def mark_x(self, value: int) -> float:
        """The horizontal center of the handle at a slider value."""
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderGroove, self
        )
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self
        )
        span = max(1, groove.width() - handle.width())
        return groove.x() + handle.width() / 2 + QStyle.sliderPositionFromValue(
            self.minimum(), self.maximum(), value, span, option.upsideDown
        )

    def value_insets(self) -> tuple[float, float]:
        """Pixels from the slider's left and right edges to its first and last values."""
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderGroove, self
        )
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self
        )
        left = groove.x() + handle.width() / 2
        return left, self.width() - (groove.x() + groove.width() - handle.width() / 2)

    def set_sync_mark(self, value: int | None) -> None:
        if value != self.sync_mark:
            self.sync_mark = value
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.maximum() <= self.minimum() or (self.marks == [None, None] and self.sync_mark is None):
            return
        painter = QPainter(self)
        if self.sync_mark is not None:
            x = round(self.mark_x(self.sync_mark))
            painter.setPen(QPen(QColor(sync_marker_color()), 2, Qt.PenStyle.DashLine))
            painter.drawLine(x, 0, x, self.height() - 1)
        if None not in self.marks:
            left, right = sorted(self.mark_x(value) for value in self.marks)
            painter.fillRect(QRectF(left, 2, max(1.0, right - left), self.height() - 4), QColor(*SELECTION_BRUSH))
        for index, value in enumerate(self.marks):
            if value is not None:
                x = round(self.mark_x(value))
                painter.setPen(QPen(marker_color(index), max(2, marker_width())))
                painter.drawLine(x, 1, x, self.height() - 2)
        painter.end()

    def mousePressEvent(self, event):
        target = None
        if event.button() == Qt.MouseButton.LeftButton:
            option = QStyleOptionSlider()
            self.initStyleOption(option)
            handle = self.style().subControlRect(
                QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self
            )
            if not handle.contains(event.position().toPoint()):
                groove = self.style().subControlRect(
                    QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderGroove, self
                )
                target = QStyle.sliderValueFromPosition(
                    self.minimum(), self.maximum(),
                    round(event.position().x() - groove.x() - handle.width() / 2),
                    max(1, groove.width() - handle.width()), option.upsideDown,
                )
                # Move the handle beneath the pointer before Qt starts its drag.
                self.setValue(target)
        super().mousePressEvent(event)
        if target is not None:
            self.seek_requested.emit(target)


class _StatusText:
    """Playback status, shown as the Timeline slider's tooltip rather than its own line."""

    def __init__(self, changed):  # type: ignore[no-untyped-def]
        self._text = ""
        self._changed = changed

    def setText(self, text: str) -> None:  # noqa: N802 - mirrors QLabel
        self._text = str(text)
        self._changed()

    def text(self) -> str:
        return self._text


class VideoPlotWidget(PlotBaseWidget):
    help_topic = "video"
    supports_horizontal_layout = True
    marker_timestamp_changed = Signal(float)
    duration_changed = Signal()
    # The two sync points: the data marker time and the matching video position.
    sync_data_requested = Signal(object)
    sync_video_requested = Signal(object)
    # A manual offset edit, after it settles; the workspace saves it.
    offset_edited = Signal(object)
    # The card's width changed, so the Timeline row must be realigned.
    timeline_alignment_requested = Signal()

    def __init__(self, folder, video_path, offset_seconds=0.0, parent=None,
                 *, max_side=None, hardware='auto', gpu_output=True):
        super().__init__(folder, Path(video_path).name, parent)
        self.folder, self.video_path = Path(folder), Path(video_path)
        self.offset_seconds = float(offset_seconds)
        # Reset offset returns here: the offset the card opened with, or the last completed sync.
        self._reset_offset = self.offset_seconds
        # The Plot navigator's trimmed range, in video seconds; markers outside it show black.
        self._visible_window: tuple[float, float] | None = None
        self.data_start = folder_start_unix_seconds(self.folder)
        self.max_side, self.hardware = max_side or 1536, hardware
        self.gpu_allowed = gpu_output and sys.platform == 'win32'
        self.restart_position = None
        self.session = self.sound = None
        self.duration = self.position = self.base = 0.0
        self.started = time.monotonic()
        self.playing = self.ready = self.awaiting = False
        self.next_picture = None
        self.presented = self.dropped = 0
        self.metrics_at = time.monotonic()
        self.description = ''
        self._closed = False
        self._outside_marker = False
        self._pending_marker = None
        self._publish_after_seek = False
        self._marker_link_enabled = False
        self._sync_anchor_timestamp = None
        self._sync_video_position = None
        self._last_marker_emit_at = 0.0
        self._presented_frame_count = 0
        self._last_metrics_at = time.monotonic()
        # The time cards' plot-area edges in card coordinates, followed by the Timeline slider.
        self._timeline_span: tuple[int, int] | None = None
        self.frame_label = QLabel("Loading video…", self)
        self.frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.frame_label.setMinimumHeight(110)
        self.frame_label.setStyleSheet(f"background: {canvas_background(self).name()}; color: palette(text);")
        self.viewport = Viewport(self)
        self.viewport.setMinimumSize(160, 110)
        self.viewport.max_side = self.max_side
        self.viewport.failed.connect(self.render_error)
        self.viewport.installEventFilter(self)
        self.viewport.setStyleSheet(f"background: {canvas_background(self).name()};")
        self.video_surface = QWidget(self)
        self.video_surface_layout = QStackedLayout(self.video_surface)
        self.video_surface_layout.setContentsMargins(0, 0, 0, 0)
        self.video_surface_layout.addWidget(self.frame_label)
        self.video_surface_layout.addWidget(self.viewport)
        # The performance label sits in the card's header row; its tooltip names the playback backend.
        self.metrics_label = QLabel("FPS: —", self)
        self.metrics_label.setStyleSheet(
            "background: rgba(0, 0, 0, 150); color: white; padding: 3px 6px; border-radius: 3px;"
        )
        self.metrics_label.setToolTip("Playback backend: loading…")
        self.header_controls.append(self.metrics_label)
        # QStackedLayout raises the selected page above sibling overlays.
        # Restore them on every switch, including out-of-range marker -> seek.
        self.video_surface_layout.currentChanged.connect(self._raise_video_overlays)
        self._metrics_timer = QTimer(self)
        self._metrics_timer.setInterval(1000)
        self._metrics_timer.timeout.connect(self._update_metrics)
        self._metrics_timer.start()
        self.status_label = _StatusText(lambda: self._update_playback_status())
        self.play_button = QToolButton(self)
        self.play_button.setAutoRaise(True)
        self.play_button.setFixedSize(25, 25)
        self.play_button.setIconSize(QSize(20, 20))
        self.play_button.clicked.connect(self.toggle_playback)
        self.mute_button = QToolButton(self)
        self.mute_button.setAutoRaise(True)
        self.mute_button.setFixedSize(25, 25)
        self.mute_button.setIconSize(QSize(20, 20))
        self.mute_button.setCheckable(True)
        self.mute_button.toggled.connect(self.set_volume)
        # Sync controls stay hidden behind the Sync button until it is pressed.
        self.sync_button = QToolButton(self)
        self.sync_button.setText("Sync")
        self.sync_button.setCheckable(True)
        self.sync_button.setAutoRaise(True)
        self.sync_button.setFixedHeight(25)
        self.sync_button.setToolTip("Sync video with data")
        self.sync_button.toggled.connect(self.set_sync_expanded)
        self.sync_data_button = QToolButton(self)
        self.sync_video_button = QToolButton(self)
        for button in (self.sync_data_button, self.sync_video_button):
            button.setAutoRaise(True)
            button.setFixedSize(25, 25)
            button.setIconSize(QSize(20, 20))
        self.sync_data_button.clicked.connect(lambda: self.sync_data_requested.emit(self))
        self.sync_video_button.clicked.connect(lambda: self.sync_video_requested.emit(self))
        self.sync_status_label = QLabel("Sync: no anchor", self)
        self.offset_input = QDoubleSpinBox(self)
        self.offset_input.setRange(-1e7, 1e7)
        self.offset_input.setDecimals(3)
        self.offset_input.setSingleStep(0.1)
        self.offset_input.setSuffix(" s")
        self.offset_input.setKeyboardTracking(False)
        self.offset_input.setToolTip("Sync offset")
        self.offset_input.setFixedWidth(110)
        self.offset_input.setValue(self.offset_seconds)
        self.offset_input.valueChanged.connect(self.set_time_offset)
        # Arrow clicks apply at once; saving waits until the value settles.
        self._offset_save_timer = QTimer(self)
        self._offset_save_timer.setSingleShot(True)
        self._offset_save_timer.setInterval(500)
        self._offset_save_timer.timeout.connect(lambda: self.offset_edited.emit(self))
        self.volume_icon = QLabel(self)
        self.volume_icon.setFixedSize(20, 20)
        self.volume_icon.setToolTip("Volume")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setFixedWidth(90)
        self.volume_slider.setToolTip("Volume")
        self.volume_slider.valueChanged.connect(self.set_volume)
        self.timeline = TimelineSlider(Qt.Orientation.Horizontal, self)
        self.timeline.setRange(0, 0)
        self.timeline.sliderPressed.connect(self._begin_timeline_drag)
        self.timeline.sliderReleased.connect(self._finish_timeline_drag)
        self.timeline.seek_requested.connect(lambda value: self.seek(value / 1000))

        # Two translucent rows over the bottom of the video: the Timeline row,
        # then the playback bar with the buttons.
        overlay_style = ("#{name} {{ background: rgba(0, 0, 0, 150); border-radius: 3px; }}"
                         " #{name} QLabel, #{name} QToolButton {{ color: white; }}")
        self.timeline_row = QWidget(self.video_surface)
        self.timeline_row.setObjectName("videoTimelineRow")
        self.timeline_row.setStyleSheet(overlay_style.format(name="videoTimelineRow"))
        self.timeline.setParent(self.timeline_row)
        self.playback_bar = QWidget(self.video_surface)
        self.playback_bar.setObjectName("videoPlaybackBar")
        self.playback_bar.setStyleSheet(overlay_style.format(name="videoPlaybackBar"))
        self.sync_panel = QWidget(self.playback_bar)
        sync_layout = QHBoxLayout(self.sync_panel)
        sync_layout.setContentsMargins(0, 0, 0, 0)
        for widget in (self.sync_data_button, self.sync_video_button, self.sync_status_label, self.offset_input):
            widget.setParent(self.sync_panel)
            sync_layout.addWidget(widget)
        self.sync_panel.setVisible(False)
        controls = QHBoxLayout(self.playback_bar)
        controls.setContentsMargins(4, 2, 4, 2)
        for widget in (self.play_button, self.mute_button, self.sync_button, self.sync_panel):
            widget.setParent(self.playback_bar)
            controls.addWidget(widget)
        controls.addStretch(1)
        for widget in (self.volume_icon, self.volume_slider):
            widget.setParent(self.playback_bar)
            controls.addWidget(widget)
        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 0, 4, 2)
        layout.addWidget(self.video_surface, stretch=1)
        self.video_surface.installEventFilter(self)
        # Preview mode sits in the card's header row, beside the performance label.
        self.quality = QComboBox(self)
        self.quality.addItems(['Auto', 'Manual'])
        self.quality.setCurrentIndex(0 if max_side is None else 1)
        self.quality.setFixedHeight(self.close_button.height() - 2)
        self.quality.setToolTip('Preview size: calculating…')
        self.manual_quality = QSpinBox(self)
        self.manual_quality.setRange(128, 8192)
        self.manual_quality.setSingleStep(2)
        self.manual_quality.setSuffix(' px')
        self.manual_quality.setKeyboardTracking(False)
        self.manual_quality.setValue(self.max_side)
        self.manual_quality.setFixedHeight(self.close_button.height() - 2)
        self.manual_quality.setToolTip('Manual preview size')
        self.manual_quality.setVisible(max_side is not None)
        self.header_controls.extend((self.manual_quality, self.quality))
        self._update_playback_status()
        self._show_time_offset()
        self.content_layout.addWidget(content, stretch=1)


        self.quality_timer = QTimer(self)
        self.quality_timer.setSingleShot(True)
        self.quality_timer.setInterval(200)
        self.quality_timer.timeout.connect(self.change_quality)
        self.viewport.preview_changed.connect(self.quality_timer.start)
        self.quality.currentIndexChanged.connect(self.change_quality)
        self.manual_quality.valueChanged.connect(self.change_quality)
        self.timeline.actionTriggered.connect(self.timeline_action)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self._refresh_control_icons()
        self._open_video()

    def _open_video(self):
        if self._closed:
            return
        path = self.video_path
        self.release()
        self.ready = False
        self.playing = False
        self.awaiting = True
        self.next_picture = None
        self.position = self.base = self.duration = 0.0
        self.presented = self.dropped = 0
        self.metrics_at = time.monotonic()
        self.viewport.clear_picture()
        self.play_button.setEnabled(False)
        self.timeline.setRange(0, 0)
        self.status_label.setText('Opening ' + Path(path).name + '…')
        self.metrics_label.setToolTip('Playback backend: probing video…')
        self.session = Session(path, self.max_side, self.hardware, gpu_output=self.gpu_allowed)

    def release(self):
        if self.session:
            self.session.close()
            self.session = None
        if self.sound:
            self.sound.close()
            self.sound = None

    def _ready(self, info):
        self.ready = True
        self.duration = info['duration']
        self.timeline.setRange(0, round(self.duration * 1000))
        self._update_timeline_marks()
        self.play_button.setEnabled(True)
        self.viewport.metadata = info['metadata']
        self.viewport.reset_view()
        meta = info['metadata']
        label = meta.model if meta else 'Standard video'
        note = meta.note if meta else 'Standard video • Fit to window'
        self.description = f"{label} | {' / '.join(info['backends'])} | {info['fps']:.2f} source fps"
        self.description += ' | GPU textures' if self.gpu_allowed and 'd3d11va' in info['backends'] else ' | CPU transfer'
        self.status_label.setText(note)
        if info['audio']:
            try:
                self.sound = AudioOutput(self)
                self.set_volume()
            except Exception as exc:
                self.status_label.setText(note + ' | Audio unavailable: ' + str(exc))
        self.mute_button.setEnabled(self.sound is not None)
        self.volume_slider.setEnabled(self.sound is not None)
        self.metrics_label.setToolTip('Playback backend: PyAV / OpenGL | ' + self.description)
        self.duration_changed.emit()
        self.video_surface_layout.setCurrentWidget(self.viewport)
        self._raise_video_overlays()
        if self.restart_position is not None:
            position, playing = self.restart_position
            self.restart_position = None
            self.seek(position)
            self.set_playing(playing)

        if self._pending_marker is not None:
            marker, self._pending_marker = self._pending_marker, None
            self.set_marker_timestamp(marker)

    def clock(self):
        if not self.playing or self.awaiting:
            return self.position
        if self.sound:
            return self.sound.position()
        return self.base + time.monotonic() - self.started

    def set_playing(self, playing):
        self.position = self.clock()
        self.playing = playing
        if self.session:
            self.session.set_clock(self.position, playing and not self.awaiting)
        self.base, self.started = self.position, time.monotonic()
        if self.sound:
            if playing and not self.awaiting:
                self.sound.sink.resume()
            else:
                self.sound.sink.suspend()
        self._refresh_control_icons()

    def toggle_playback(self):
        if self.ready:
            if self._outside_marker:
                self.seek(self.position)
            if not self.playing and self.position >= self.duration - 0.05:
                self.seek(0)
            self.set_playing(not self.playing)

    def seek(self, seconds, *, publish=True):
        if not self.ready:
            return
        if self._outside_marker:
            self._outside_marker = False
            self.apply_theme()
        self.video_surface_layout.setCurrentWidget(self.viewport)
        self._publish_after_seek = publish
        self.position = self.base = max(0.0, min(seconds, max(0.0, self.duration - 0.001)))
        self.started = time.monotonic()
        self.awaiting = True
        self.next_picture = None
        self.session.seek(self.position)
        self.session.set_clock(self.position, False)
        if self.sound:
            try:
                self.sound.reset(self.position)
            except Exception as exc:
                self.disable_audio(str(exc))
        self.timeline.setValue(round(self.position * 1000))

    def _begin_timeline_drag(self):
        self.scrub_was_playing = self.playing
        self.set_playing(False)

    def _finish_timeline_drag(self):
        self.seek(self.timeline.value() / 1000)
        self.set_playing(getattr(self, 'scrub_was_playing', False))

    def timeline_action(self, action):
        if not self.timeline.isSliderDown():
            self.seek(self.timeline.sliderPosition() / 1000)

    def _restore_header_control_states(self) -> None:
        super()._restore_header_control_states()
        self.manual_quality.setVisible(self.quality.currentIndex() == 1)

    def change_quality(self):
        automatic = self.quality.currentIndex() == 0
        if self.manual_quality.isHidden() == (not automatic):
            self.manual_quality.setVisible(not automatic)
            self._layout_header_controls()
        size = self.viewport.suggested_preview_size() if automatic else self.manual_quality.value()
        self.quality.setToolTip('Preview size: ' + ('calculating…' if size is None else f'{size} px')
                                if automatic else 'Preview size: set manually')
        if size is None or size == self.max_side:
            return
        self.max_side = size
        self.viewport.max_side = self.max_side
        self.viewport.upload = True
        self.viewport.update()
        if self.session:
            self.session.max_side = self.max_side
            # Playing readers adopt the new size as their bounded queues drain.
            # Refresh a paused CPU frame without resuming playback.
            picture = self.viewport.picture
            gpu_frame = picture is not None and all(f.format.name == 'd3d11' for f in picture.frames)
            if self.ready and not self.playing and not self.awaiting and not gpu_frame:
                self.seek(self.position)

    def workspace_state(self):
        return {"folder": str(self.folder), "path": str(self.video_path), "offset": self.offset_seconds,
                "position": self.clock() if self.ready else (self.restart_position or (self.position,))[0],
                "volume": self.volume_slider.value(), "muted": self.mute_button.isChecked(),
                "preview": {"automatic": self.quality.currentIndex() == 0, "size": self.manual_quality.value()}}

    def restore_workspace_state(self, state):
        """Show the saved frame, paused, with the saved sound and preview settings."""
        preview = state.get("preview", {})
        try:
            self.manual_quality.setValue(int(preview.get("size", self.manual_quality.value())))
            self.quality.setCurrentIndex(0 if preview.get("automatic", True) else 1)
            self.volume_slider.setValue(int(state.get("volume", self.volume_slider.value())))
            self.mute_button.setChecked(bool(state.get("muted", False)))
            position = float(state.get("position", 0.0))
        except (TypeError, ValueError):
            return
        if self.ready:
            self.seek(position)
            self.set_playing(False)
        else:
            self.restart_position = (position, False)

    def set_volume(self, *_):
        muted = self.mute_button.isChecked()
        self._refresh_control_icons()
        if self.sound:
            self.sound.sink.setVolume(0.0 if muted else self.volume_slider.value() / 100)

    def disable_audio(self, message):
        self.position = self.clock()
        if self.sound:
            self.sound.close()
            self.sound = None
        self.base, self.started = self.position, time.monotonic()
        self.status_label.setText('Audio unavailable: ' + message)
        self.mute_button.setEnabled(False)
        self.volume_slider.setEnabled(False)

    def render_error(self, message):
        if self._closed:
            return
        if 'D3D11' in message and self.gpu_allowed:
            LOG.warning('%s; restarting with CPU transfer fallback', message)
            self.gpu_allowed = False
            self.restart_position = (self.position, self.playing)
            QTimer.singleShot(0, self._open_video)
            return
        self.set_playing(False)
        self.status_label.setText(message)
        self._outside_marker = False
        self.apply_theme()
        self.frame_label.setText(message)
        self.video_surface_layout.setCurrentWidget(self.frame_label)
        self.ready = False
        self.play_button.setEnabled(False)
        LOG.error(message)

    def tick(self):
        if not self.session or self._closed:
            return
        while not self.session.events.empty():
            kind, value = self.session.events.get_nowait()
            if kind == 'ready':
                self._ready(value)
            elif kind == 'error':
                self.set_playing(False)
                self.status_label.setText('Cannot play: ' + value)
                self._outside_marker = False
                self.apply_theme()
                self.frame_label.setText('Cannot play: ' + value)
                self.video_surface_layout.setCurrentWidget(self.frame_label)
                self.ready = False
                self.play_button.setEnabled(False)
                self.session.close()
                return
            elif kind == 'audio_error':
                self.disable_audio(value)
        if not self.ready or self._outside_marker:
            return
        if self.sound and self.playing and not self.awaiting:
            try:
                self.sound.pump(self.session, self.duration)
            except Exception as exc:
                self.disable_audio(str(exc))
        position = self.clock()
        self.session.set_clock(position, self.playing and not self.awaiting)
        generation = self.session.request()[0]
        latest = None
        for _ in range(8):
            if self.next_picture is None:
                try:
                    self.next_picture = self.session.video.get_nowait()
                except queue.Empty:
                    break
            picture = self.next_picture
            if picture.generation != generation:
                self.next_picture = None
                continue
            if not self.awaiting and picture.seconds > position + 0.005:
                break
            if not self.playing and not self.awaiting:
                break
            self.next_picture = None
            if latest is not None:
                self.dropped += 1
            latest = picture
            if self.awaiting:
                self.awaiting = False
                self.base, self.started = self.position, time.monotonic()
                if self.sound and self.playing:
                    try:
                        self.sound.sink.resume()
                        self.sound.pump(self.session, self.duration)
                    except Exception as exc:
                        self.disable_audio(str(exc))
                self._refresh_control_icons()
                break
        if latest is not None:
            self.viewport.set_picture(latest)
            self.presented += 1
            self._presented_frame_count += 1
            if self._publish_after_seek:
                self._publish_after_seek = False
                self._publish_marker_timestamp(force=True)
        # A seek to the end can have no remaining frame; do not hang awaiting it.
        if self.awaiting and self.session.video_eof == generation and self.session.video.empty():
            self.awaiting = False
            self.set_playing(False)
        self.position = min(self.duration, self.clock())
        if self.playing and self.position >= self.duration:
            self.set_playing(False)
        if not self.timeline.isSliderDown():
            self.timeline.setValue(round(self.position * 1000))
            self.status_label.setText(self._status_text())
        if self.playing:
            self._publish_marker_timestamp()

    def filmstrip_source(self) -> PlotSource:
        """This video, with its current sync offset, for a Multi-series filmstrip."""
        return PlotSource("video", self.video_path, folder=self.folder, offset=self.offset_seconds)

    def set_timeline_markers(self, first: float | None, second: float | None) -> None:
        """Show the data markers' times on the Timeline slider."""
        self._timeline_marker_timestamps = (first, second)
        self._update_timeline_marks()

    def set_sync_marker(self, timestamp: float | None) -> None:
        """Show the stored sync data marker on the Timeline slider."""
        self._sync_marker_timestamp = timestamp
        self._update_timeline_marks()

    def _update_timeline_marks(self) -> None:
        marks = []
        timestamps = tuple(getattr(self, "_timeline_marker_timestamps", (None, None)))
        for timestamp in timestamps + (getattr(self, "_sync_marker_timestamp", None),):
            position = (None if timestamp is None or self.data_start is None
                        else float(timestamp) - self.data_start - self.offset_seconds)
            marks.append(round(position * 1000) if position is not None and 0.0 <= position <= self.duration else None)
        self.timeline.set_marks(*marks[:2])
        self.timeline.set_sync_mark(marks[2])

    def set_marker_timestamp(self, timestamp: float) -> None:
        """Jump to a linked global marker, respecting the configured offset."""
        if self.data_start is None:
            return
        if not self.ready:
            self._pending_marker = float(timestamp)
            return
        position = float(timestamp) - self.data_start - self.offset_seconds
        start, stop = self._visible_window or (0.0, self.duration)
        if position < max(0.0, start) or position > min(self.duration, stop):
            self._show_outside_marker_frame()
            return
        self.seek(position, publish=False)

    def set_visible_window(self, window: tuple[float, float] | None) -> None:
        """The part of the video kept in the Plot navigator, in video seconds; None keeps all of it."""
        self._visible_window = None if window is None else tuple(sorted(float(value) for value in window))

    def _show_outside_marker_frame(self) -> None:
        """Show black instead of a stale frame while a linked marker is outside the shown video."""
        self.set_playing(False)
        self._outside_marker = True
        self._publish_after_seek = False
        self.viewport.clear_picture()
        self.frame_label.clear()
        self.frame_label.setStyleSheet("background: black; color: #9CA3AF;")
        self.frame_label.setText("Marker is outside the shown video")
        self.video_surface_layout.setCurrentWidget(self.frame_label)
        self.status_label.setText("Marker is outside the shown video")

    def set_marker_link_enabled(self, enabled: bool) -> None:
        self._marker_link_enabled = bool(enabled)

    @property
    def has_sync_anchor(self) -> bool:
        return self._sync_anchor_timestamp is not None

    @property
    def has_sync_video_position(self) -> bool:
        return self._sync_video_position is not None

    def capture_sync_anchor(self, timestamp: float) -> None:
        """Store the data marker time to sync; it stays even if the marker moves."""
        self._sync_anchor_timestamp = float(timestamp)
        self._show_sync_state()

    def capture_sync_video_position(self) -> None:
        """Store the current video position as the frame matching the data marker."""
        self._sync_video_position = float(self.position)
        self._show_sync_state()

    def apply_sync_anchor(self) -> float | None:
        """Return and apply the offset implied by both sync points, then clear them."""
        if self._sync_anchor_timestamp is None or self._sync_video_position is None or self.data_start is None:
            return None
        offset = self._sync_anchor_timestamp - self.data_start - self._sync_video_position
        self._sync_anchor_timestamp = self._sync_video_position = None
        self._reset_offset = offset
        self.set_offset(offset)
        self._show_sync_state()
        self.sync_status_label.setText(f"Synced: {self.offset_seconds:+.3f} s")
        # A completed sync returns the bar to its compact state.
        self.sync_button.setChecked(False)
        return self.offset_seconds

    @property
    def time_offset(self) -> float:
        """A video's time offset is its sync offset."""
        return self.offset_seconds

    def set_time_offset(self, seconds: float) -> None:
        """A sync offset set by hand: applied at once, saved once it stops changing."""
        if abs(float(seconds) - self.offset_seconds) < 0.0005:
            return
        self.set_offset(seconds)
        self._offset_save_timer.start()

    def reset_time_offset(self) -> None:
        """Return to the offset the card opened with, or the last completed sync."""
        self.set_time_offset(self._reset_offset)

    def set_offset(self, seconds: float) -> None:
        """Use a new sync offset: refresh its displays and keep a linked marker's frame."""
        previous = self.offset_seconds
        self.offset_seconds = float(seconds)
        offset_input = getattr(self, "offset_input", None)
        if offset_input is not None and abs(offset_input.value() - self.offset_seconds) >= 0.0005:
            offset_input.blockSignals(True)
            offset_input.setValue(self.offset_seconds)
            offset_input.blockSignals(False)
        self._show_time_offset()
        self._update_timeline_marks()
        self.status_label.setText(self._status_text())
        marker = tuple(getattr(self, "_timeline_marker_timestamps", (None, None)))[0]
        if self._marker_link_enabled and marker is not None and getattr(self, "ready", False) and not self.playing:
            self.set_marker_timestamp(marker)
        if abs(self.offset_seconds - previous) >= 1e-9:
            self.time_offset_changed.emit(self, previous)

    def set_sync_expanded(self, expanded: bool) -> None:
        """Show the sync buttons, status and offset input, or only the Sync button."""
        if self.sync_button.isChecked() != bool(expanded):
            self.sync_button.setChecked(bool(expanded))
            return
        self.sync_panel.setVisible(bool(expanded))
        self.sync_button.setToolTip("Hide sync controls" if expanded else "Sync video with data")
        self._place_playback_bar()

    def _show_sync_state(self) -> None:
        """Each sync button shows whether its point is set; the status says what is left."""
        data_set, video_set = self.has_sync_anchor, self.has_sync_video_position
        self.sync_data_button.setIcon(plot_control_icon(self, "sync-data-set" if data_set else "sync-data-ready"))
        self.sync_video_button.setIcon(plot_control_icon(self, "sync-video-set" if video_set else "sync-video-ready"))
        self.sync_data_button.setToolTip(
            f"Sync data marker set: {self._sync_anchor_timestamp:.3f}. Click to use the current marker instead."
            if data_set else "Set data marker: use the red marker's time as the sync point")
        self.sync_video_button.setToolTip(
            f"Sync video position set: {self._sync_video_position:.3f} s. Click to use the current position instead."
            if video_set else "Set video marker: the current video position matches the sync point")
        if data_set and not video_set:
            self.sync_status_label.setText("Sync: find the matching video frame")
        elif video_set and not data_set:
            self.sync_status_label.setText("Sync: place the data marker")
        elif not data_set and not video_set and self.sync_status_label.text().startswith("Sync: "):
            self.sync_status_label.setText("Sync: no anchor")

    def show_sync_message(self, message: str) -> None:
        """Show a concise sync workflow message without changing playback."""
        self.sync_status_label.setText(message)

    def _update_playback_status(self) -> None:
        timeline = getattr(self, "timeline", None)
        if timeline is not None:
            timeline.setToolTip(self.status_label.text() or self._status_text())

    def _status_text(self) -> str:
        start = "unknown" if self.data_start is None else f"{self.data_start:.3f}"
        return f"{self.position:.2f} / {self.duration:.2f} s  |  data start: {start}  |  offset: {self.offset_seconds:+.3f} s"

    def _publish_marker_timestamp(self, force=False) -> None:
        """Publish at 10 Hz while video advances; plot updates are expensive."""
        if not self._marker_link_enabled or self.data_start is None:
            return
        now = time.monotonic()
        if not force and now - self._last_marker_emit_at < 0.1:
            return
        self._last_marker_emit_at = now
        self.marker_timestamp_changed.emit(self.data_start + self.offset_seconds + self.position)

    def _refresh_control_icons(self):
        if not hasattr(self, 'volume_slider'):
            return
        self.play_button.setIcon(plot_control_icon(self, 'video-pause' if self.playing else 'video-play'))
        self.play_button.setToolTip('Pause video' if self.playing else 'Play video')
        muted = self.mute_button.isChecked()
        self.mute_button.setIcon(plot_control_icon(self, 'audio-muted' if muted else 'audio-unmuted'))
        self.mute_button.setToolTip('Unmute audio' if muted else 'Mute audio')
        self.volume_icon.setPixmap(plot_control_icon(self, 'volume').pixmap(self.volume_icon.size()))
        self._show_sync_state()

    def eventFilter(self, watched, event):
        if watched is self.viewport and event.type() == QEvent.Type.MouseButtonPress:
            self.activated.emit(self)
        if watched is getattr(self, "video_surface", None) and event.type() == QEvent.Type.Resize:
            self._place_playback_bar()
        return super().eventFilter(watched, event)

    def apply_theme(self) -> None:
        color = canvas_background(self).name()
        if not self._outside_marker:
            self.frame_label.setStyleSheet(f"background: {color}; color: palette(text);")
        self.viewport.setStyleSheet(f"background: {color};")
        self.viewport.update()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in {QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange}:
            self._refresh_control_icons()

    def _raise_video_overlays(self, *_args) -> None:
        self._layout_header_controls()
        self._place_playback_bar()

    def _place_playback_bar(self) -> None:
        """Keep the Timeline row and playback bar across the bottom of the video, above it."""
        if not hasattr(self, "playback_bar"):
            return
        surface = self.video_surface
        width = max(1, surface.width() - 8)
        height = self.playback_bar.sizeHint().height()
        bar_top = max(0, surface.height() - height - 4)
        self.playback_bar.setGeometry(4, bar_top, width, height)
        row_height = self.timeline.sizeHint().height() + 4
        self.timeline_row.setGeometry(4, max(0, bar_top - row_height - 2), width, row_height)
        self._place_timeline()
        self.timeline_row.raise_()
        self.playback_bar.raise_()

    def set_timeline_span(self, left_global: int | None, right_global: int | None = None) -> None:
        """Align the Timeline slider's start and end with the time cards' plot-area edges.

        The edges are screen x coordinates; None returns the slider to the full row.
        """
        span = None
        if left_global is not None and right_global is not None and right_global > left_global:
            span = (self.mapFromGlobal(QPoint(int(left_global), 0)).x(),
                    self.mapFromGlobal(QPoint(int(right_global), 0)).x())
        if span != self._timeline_span:
            self._timeline_span = span
            self._place_timeline()

    def _place_timeline(self) -> None:
        row = self.timeline_row
        height = self.timeline.sizeHint().height()
        top = max(0, (row.height() - height) // 2)
        geometry = (4, max(1, row.width() - 8))
        if self._timeline_span is not None:
            # Card coordinates to the row's; follow the edges only when both fall inside the row.
            left = row.mapFrom(self, QPoint(self._timeline_span[0], 0)).x()
            right = row.mapFrom(self, QPoint(self._timeline_span[1], 0)).x()
            inset_left, inset_right = self.timeline.value_insets()
            x = round(left - inset_left)
            end = round(right + inset_right)
            if 0 <= x and end <= row.width() and end - x > 40:
                geometry = (x, end - x)
        if self.timeline.geometry().getRect() != (geometry[0], top, geometry[1], height):
            self.timeline.setGeometry(geometry[0], top, geometry[1], height)

    def _update_metrics(self) -> None:
        """Show the presentation-update rate."""
        now = time.monotonic()
        elapsed = max(0.001, now - self._last_metrics_at)
        fps = self._presented_frame_count / elapsed
        self.metrics_label.setText(f"FPS: {fps:.1f}")
        self.metrics_label.adjustSize()
        self._layout_header_controls()
        self._presented_frame_count = 0
        self._last_metrics_at = now

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if hasattr(self, "metrics_label"):
            self.metrics_label.adjustSize()
            self._layout_header_controls()
        if event.oldSize().width() != event.size().width():
            self.timeline_alignment_requested.emit()

    def shutdown(self):
        """Stop timers/readers before detachment; containers remain owned by workers."""
        if self._closed:
            return
        self._closed = True
        self.timer.stop()
        self.quality_timer.stop()
        self._metrics_timer.stop()
        if self._offset_save_timer.isActive():
            # Save a manual offset edited just before closing.
            self._offset_save_timer.stop()
            self.offset_edited.emit(self)
        self.release()
        self.next_picture = None
        self.viewport.clear_picture()
        self.viewport.cleanup()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)


def folder_start_unix_seconds(folder: Path) -> float | None:
    """Read the common log start stored in any H5 cache in the attachment folder.

    Audio H5 files are skipped: their log start includes their own offset.
    """
    for h5_path in sorted(folder.glob("*.h5")):
        try:
            with h5py.File(h5_path, "r") as h5_file:
                if str(h5_file.attrs.get(MEDIA_KIND_ATTR, "")):
                    continue
                value = h5_file.attrs.get("log_start_unix_seconds")
                if value is not None:
                    return float(value)
        except OSError:
            continue
    return None
