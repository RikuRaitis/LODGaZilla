"""Optional desktop/OpenGL smoke tests: python -m unittest tests.test_video_gui -v."""
from pathlib import Path
import tempfile
import time
import unittest

import av
import h5py
import numpy as np
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QSurfaceFormat, QWheelEvent
from PySide6.QtTest import QTest
from tests.qt_wait import qt_wait
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaDevices, QAudioFormat

from src.gui.plots.video import VideoPlotWidget
from src.gui.plots.video.calibration import Metadata
from src.gui.plots.video.decoder import Picture
from tests.test_video_decoder import make_clip
from src.gui.plots.video.viewport import Viewport


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        QSurfaceFormat.setDefaultFormat(fmt)
        cls.app = QApplication.instance() or QApplication([])
        if cls.app.platformName() in ('offscreen', 'minimal'):
            raise unittest.SkipTest('Desktop OpenGL tests require a window-system platform')

    def until(self, predicate, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not predicate():
            qt_wait(20)
        self.assertTrue(predicate())

    def test_ctrl_wheel_zoom_and_ctrl_drag_roll(self):
        widget = Viewport()
        widget.resize(600, 360)
        try:
            for modifiers, should_zoom in ((Qt.KeyboardModifier.NoModifier, False),
                                            (Qt.KeyboardModifier.ControlModifier, True)):
                event = QWheelEvent(QPointF(100, 100), QPointF(100, 100), QPoint(), QPoint(0, 120),
                                    Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False)
                event.setAccepted(True)
                old_fov = widget.fov
                widget.wheelEvent(event)
                self.assertEqual(widget.fov < old_fov, should_zoom)
                self.assertEqual(event.isAccepted(), should_zoom)
            widget.look(35, 80)
            forward = widget.view_rotation[:, 2].copy()
            right = widget.view_rotation[:, 0].copy()
            widget.drag = QPointF(100, 100)
            event = QMouseEvent(QEvent.Type.MouseMove, QPointF(220, 100), QPointF(220, 100),
                                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.ControlModifier)
            widget.mouseMoveEvent(event)
            np.testing.assert_allclose(widget.view_rotation[:, 2], forward, atol=1e-12)
            self.assertFalse(np.allclose(widget.view_rotation[:, 0], right))
            widget.reset_view()
            np.testing.assert_array_equal(widget.view_rotation, np.eye(3))
            self.assertEqual(widget.fov, 90)
        finally:
            widget.close()

    def test_workspace_sync_offset_marker_roundtrip_and_card_removal(self):
        from src.app.lodgazilla import PlotWorkspace
        from src.data.folder_info import FolderInfoData, read_info_file, write_info_file
        from src.gui.plots.base import PlotBaseWidget
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path = folder / 'clip.mp4'
            make_clip(path, tracks=1, size=(320, 180), audio=False)
            write_info_file(folder, FolderInfoData(videos=[{"path": "clip.mp4", "offset_seconds": "-2"}]))
            start = 1_700_000_000.0
            with h5py.File(folder / 'data.h5', 'w') as file:
                file.attrs['log_start_unix_seconds'] = start
            workspace = PlotWorkspace(settings_path=folder / 'settings.json')
            workspace.resize(1000, 650)
            workspace.show()
            card = workspace.add_video_plot(folder, path, offset_seconds=-2)
            session = card.session
            emitted = []
            card.marker_timestamp_changed.connect(emitted.append)
            try:
                self.until(lambda: card.ready and not card.awaiting)
                self.assertIsInstance(card, PlotBaseWidget)
                self.assertFalse(card.playing)
                # Row rearrangement must keep the decoder/session and its sync
                # behavior intact while a real video surface is alive.
                image = workspace.add_image_plot(folder, folder / 'missing.png')
                qt_wait(50)
                self.assertTrue(workspace.move_plot(card, image, 'right'))
                qt_wait(50)
                self.assertIs(card.session, session)
                self.assertTrue(card.ready)
                workspace.set_markers_linked(True)
                self.assertTrue(card._marker_link_enabled)
                workspace._last_data_marker_timestamp = start + 10
                # The sync controls hide behind the Sync button until it is pressed.
                self.assertFalse(card.sync_panel.isVisibleTo(card.playback_bar))
                card.sync_button.click()
                self.assertTrue(card.sync_panel.isVisibleTo(card.playback_bar))
                card.sync_data_button.click()
                self.assertTrue(card.has_sync_anchor)
                card.seek(1.25)
                self.until(lambda: not card.awaiting)
                card.sync_video_button.click()
                self.assertAlmostEqual(card.offset_seconds, 8.75)
                self.assertFalse(card.has_sync_anchor)
                self.assertAlmostEqual(float(read_info_file(folder).videos[0]['offset_seconds']), 8.75)
                # A completed sync collapses the controls and shows the new offset.
                self.assertFalse(card.sync_button.isChecked())
                self.assertFalse(card.sync_panel.isVisibleTo(card.playback_bar))
                self.assertAlmostEqual(card.offset_input.value(), 8.75)
                # A manual offset applies at once and is saved when it settles.
                card.offset_input.setValue(8.85)
                self.assertAlmostEqual(card.offset_seconds, 8.85)
                self.until(lambda: abs(float(read_info_file(folder).videos[0]['offset_seconds']) - 8.85) < 1e-6)
                card.offset_input.setValue(8.75)
                self.until(lambda: abs(float(read_info_file(folder).videos[0]['offset_seconds']) - 8.75) < 1e-6)
                # The Timeline slider's first and last values follow given plot-area edges.
                row = card.timeline_row
                left = row.mapToGlobal(QPoint(60, 0)).x()
                right = row.mapToGlobal(QPoint(row.width() - 80, 0)).x()
                card.set_timeline_span(left, right)
                timeline = card.timeline
                first = timeline.mapToGlobal(QPoint(round(timeline.mark_x(timeline.minimum())), 0)).x()
                last = timeline.mapToGlobal(QPoint(round(timeline.mark_x(timeline.maximum())), 0)).x()
                self.assertLessEqual(abs(first - left), 1)
                self.assertLessEqual(abs(last - right), 1)
                card.set_timeline_span(None)
                self.assertEqual(timeline.x(), 4)
                self.assertEqual(workspace._plot_time_range(card), (start + 8.75, start + 10.75))
                card.set_marker_link_enabled(True)
                card.seek(0.5)
                self.until(lambda: not card.awaiting)
                self.assertAlmostEqual(emitted[-1], start + 9.25)
                count = len(emitted)
                card.set_marker_timestamp(start + 9.5)
                self.until(lambda: not card.awaiting)
                self.assertAlmostEqual(card.position, 0.75)
                self.assertEqual(len(emitted), count, 'Linked seeks must not feed back a marker')
                card.set_marker_timestamp(start + 100)
                self.assertIs(card.video_surface_layout.currentWidget(), card.frame_label)
                self.assertIsNone(card.viewport.picture)
                self.assertFalse(card.playing)
                card.set_marker_timestamp(start + 9.25)
                self.until(lambda: not card.awaiting)
                self.assertIs(card.video_surface_layout.currentWidget(), card.viewport)
                self.assertIsNotNone(card.viewport.picture)
                # A marker inside the video but outside its trimmed range shows black.
                workspace.set_plot_navigator_range(card, start + 9.5, start + 10.5)
                self.until(lambda: not card.awaiting)
                card.set_marker_timestamp(start + 9.0)
                self.assertIs(card.video_surface_layout.currentWidget(), card.frame_label)
                self.assertIn("background: black", card.frame_label.styleSheet())
                card.set_marker_timestamp(start + 10.0)
                self.until(lambda: not card.awaiting)
                self.assertIs(card.video_surface_layout.currentWidget(), card.viewport)
                self.assertNotIn("background: black", card.frame_label.styleSheet())
                workspace.reset_plot_navigator_range(card)
                self.assertIsNone(card._visible_window)
                # The header's Offset button edits the sync offset, which is saved when it settles.
                card.offset_button.click()
                card.offset_editor.setValue(8.5)
                self.assertAlmostEqual(card.offset_seconds, 8.5)
                self.assertAlmostEqual(card.offset_input.value(), 8.5)
                self.until(lambda: abs(float(read_info_file(folder).videos[0]['offset_seconds']) - 8.5) < 1e-6)
                card.reset_time_offset()
                self.assertAlmostEqual(card.offset_seconds, 8.75)
                self.until(lambda: abs(float(read_info_file(folder).videos[0]['offset_seconds']) - 8.75) < 1e-6)
                self.assertTrue(workspace.move_plot(card, image, 'below'))
                self.assertIs(card.session, session)
                workspace.remove_plot(image)
                card.close_button.click()
                self.assertEqual(workspace.splitter.count(), 0)
                self.until(lambda: all(not thread.is_alive() for thread in session.threads))
            finally:
                # The card may already be deleted by its close button.
                session.close()
                workspace.close()
                for thread in session.threads:
                    thread.join(5)

    def test_horizontal_look_moves_sideways_even_at_poles(self):
        widget = Viewport()
        try:
            for elevation in (-90, -80, 0, 80, 90):
                with self.subTest(elevation=elevation):
                    widget.reset_view()
                    widget.look(35, elevation)
                    right, down, forward = widget.view_rotation.T.copy()
                    widget.look(90, 0)
                    # A quarter-turn must look toward the previous screen's
                    # right, preserving its up/down axis even at a pole.
                    np.testing.assert_allclose(widget.view_rotation[:, 2], right, atol=1e-12)
                    np.testing.assert_allclose(widget.view_rotation[:, 1], down, atol=1e-12)
                    widget.look(-90, 0)
                    np.testing.assert_allclose(widget.view_rotation[:, 2], forward, atol=1e-12)
            widget.fov = 45
            widget.reset_view()
            np.testing.assert_array_equal(widget.view_rotation, np.eye(3))
            self.assertEqual(widget.fov, 90)
        finally:
            widget.close()

    def test_gpu_front_back_and_repaint_without_upload(self):
        widget = Viewport()
        widget.resize(600, 360)
        errors = []
        widget.failed.connect(errors.append)
        widget.show()
        try:
            qt_wait(150)
            frames = []
            for channel in (0, 2):
                pixels = np.zeros((128, 128, 3), dtype=np.uint8)
                pixels[:, :, channel] = 220
                frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
                frames.append(frame.reformat(format='yuv420p'))
            widget.metadata = Metadata(reversed_tracks=False)
            widget.set_picture(Picture(0, 0, tuple(frames)))
            qt_wait(150)
            self.assertFalse(errors, errors)
            front = widget.grabFramebuffer().pixelColor(300, 180)
            self.assertGreater(front.red(), 150)
            self.assertLess(front.blue(), 40)
            self.assertFalse(widget.upload)
            widget.look(180, 0)
            qt_wait(100)
            back = widget.grabFramebuffer().pixelColor(300, 180)
            self.assertGreater(back.blue(), 150)
            self.assertLess(back.red(), 40)
            self.assertFalse(errors, errors)
        finally:
            widget.cleanup()
            widget.close()

    def test_player_pause_seek_volume_and_eof(self):
        self.check_playback('dual.insv', tracks=2, size=(256, 256))

    def test_mp4_pause_seek_volume_and_eof(self):
        self.check_playback('standard.mp4', tracks=1, size=(320, 180))

    def check_playback(self, filename, tracks, size):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / filename
            make_clip(path, tracks=tracks, size=size)
            window = VideoPlotWidget(path.parent, path, max_side=128, hardware='none')
            window.show()
            session = window.session
            try:
                self.until(lambda: window.ready and not window.awaiting)
                window.set_playing(True)
                self.assertEqual(len(window.viewport.picture.frames), tracks)
                self.until(lambda: window.position > 0.20)
                fmt = QAudioFormat()
                fmt.setSampleRate(48000)
                fmt.setChannelCount(2)
                fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
                device = QMediaDevices.defaultAudioOutput()
                if not device.isNull() and device.isFormatSupported(fmt):
                    self.assertIsNotNone(window.sound, window.status_label.text())
                window.set_playing(False)
                paused = window.position
                qt_wait(150)
                self.assertAlmostEqual(window.position, paused, delta=0.01)
                timeline = window.timeline
                click = QPoint(timeline.width() * 3 // 4, timeline.height() // 2)
                QTest.mousePress(timeline, Qt.MouseButton.LeftButton, pos=click)
                self.assertGreater(window.position, window.duration * 0.6)
                self.assertTrue(timeline.isSliderDown())
                QTest.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=click)
                self.until(lambda: not window.awaiting)
                self.assertFalse(window.playing)
                # The same press can continue as a drag, and playing state resumes.
                window.set_playing(True)
                start = QPoint(timeline.width() // 4, timeline.height() // 2)
                end = QPoint(timeline.width() // 2, timeline.height() // 2)
                QTest.mousePress(timeline, Qt.MouseButton.LeftButton, pos=start)
                QTest.mouseMove(timeline, end)
                QTest.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=end)
                self.assertAlmostEqual(window.position, window.duration / 2, delta=0.1)
                self.assertTrue(window.playing)
                window.set_playing(False)
                window.seek(1.25)
                self.until(lambda: not window.awaiting)
                self.assertAlmostEqual(window.position, 1.25, delta=0.03)
                self.assertAlmostEqual(window.viewport.picture.seconds, 1.25, delta=1/24)
                window.mute_button.setChecked(True)
                if window.sound:
                    self.assertEqual(window.sound.sink.volume(), 0.0)
                window.mute_button.setChecked(False)
                window.volume_slider.setValue(40)
                if window.sound:
                    self.assertAlmostEqual(window.sound.sink.volume(), 0.4, places=5)
                window.set_playing(True)
                self.until(lambda: not window.playing)
                self.assertGreater(window.position, 1.9)
                self.assertIsNone(window.viewport.error)
                window.seek(0)
                self.until(lambda: not window.awaiting)
                window.set_playing(True)
                self.until(lambda: window.position > 0.1)
                window.close()
                self.assertFalse(window.timer.isActive())
                self.assertIsNone(window.viewport.program)
                self.assertEqual(window.viewport.textures, [])
                window.shutdown()  # Repeated teardown is harmless.
            finally:
                window.close()
                for thread in session.threads:
                    thread.join(5)

    def test_flat_video_fit_orientation_and_mode_switch(self):
        widget = Viewport()
        widget.resize(600, 360)
        errors = []
        widget.failed.connect(errors.append)
        widget.show()
        try:
            pixels = np.zeros((80, 160, 3), dtype=np.uint8)
            pixels[:40, :, 0] = 220
            pixels[40:, :, 2] = 220
            frame = av.VideoFrame.from_ndarray(pixels, format='rgb24').reformat(format='yuv420p')
            for aspect, rotation in ((2.0, 0), (0.5, 0), (1.0, 0), (2.0, 90)):
                with self.subTest(aspect=aspect, rotation=rotation):
                    widget.set_picture(Picture(0, 0, (frame,), 'flat', aspect, rotation))
                    qt_wait(80)
                    picture = widget.grabFramebuffer()
                    width, height = picture.width(), picture.height()
                    red_at, blue_at = (((0.45, 0.5), (0.55, 0.5)) if rotation else
                                      ((0.5, 0.4), (0.5, 0.6)))
                    red = picture.pixelColor(int(width*red_at[0]), int(height*red_at[1]))
                    blue = picture.pixelColor(int(width*blue_at[0]), int(height*blue_at[1]))
                    self.assertGreater(red.red(), 150)
                    self.assertLess(red.blue(), 40)
                    self.assertGreater(blue.blue(), 150)
                    self.assertLess(blue.red(), 40)
                    border = picture.pixelColor(width//2, 5) if aspect == 2 and not rotation else picture.pixelColor(5, height//2)
                    self.assertLess(max(border.red(), border.green(), border.blue()), 20)
                    self.assertFalse(widget.upload)
            widget.set_picture(Picture(0, 0, (frame, frame)))
            qt_wait(80)
            widget.grabFramebuffer()
            widget.set_picture(Picture(0, 0, (frame,), 'flat', 2))
            qt_wait(80)
            widget.grabFramebuffer()
            self.assertFalse(errors, errors)
        finally:
            widget.cleanup()
            widget.close()

    def test_automatic_preview_resize_manual_override_and_paused_refresh(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preview.mp4'
            make_clip(path, tracks=1, size=(1280, 720), audio=False)
            window = VideoPlotWidget(path.parent, path, hardware='none')
            window.resize(1040, 750)
            window.show()
            session = window.session
            try:
                self.until(lambda: window.ready and not window.awaiting)
                window.set_playing(True)
                self.until(lambda: window.max_side == window.viewport.suggested_preview_size())
                self.assertEqual(window.quality.currentText(), 'Auto')
                self.assertEqual(session.request()[0], 0, 'Auto sizing must not seek during playback')
                window.set_playing(False)
                paused = window.position
                previous_size = window.max_side
                window.resize(800, 650)
                self.until(lambda: window.max_side != previous_size and not window.awaiting)
                self.assertEqual(window.max_side, window.viewport.suggested_preview_size())
                self.assertEqual(window.viewport.picture.frames[0].width, window.max_side)
                self.assertFalse(window.playing)
                self.assertAlmostEqual(window.position, paused, delta=0.01)
                window.manual_quality.setValue(1038)
                window.quality.setCurrentIndex(1)
                self.until(lambda: not window.awaiting)
                self.assertEqual(window.max_side, 1038)
                self.assertEqual(window.viewport.picture.frames[0].width, 1038)
                window.resize(900, 700)
                qt_wait(300)
                self.assertEqual(window.max_side, 1038)
                window.quality.setCurrentIndex(0)
                self.until(lambda: not window.awaiting)
                self.assertEqual(window.max_side, window.viewport.suggested_preview_size())
                self.assertFalse(window.playing)
            finally:
                window.close()
                for thread in session.threads:
                    thread.join(5)


if __name__ == '__main__':
    unittest.main()
