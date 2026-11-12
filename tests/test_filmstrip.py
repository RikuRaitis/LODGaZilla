"""Video filmstrip lanes in Multi-series cards: frames, placement and card policy."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import threading
import time
import unittest
from pathlib import Path

import av
import h5py
import numpy as np
from PySide6.QtCore import QThreadPool
from tests.qt_wait import qt_wait
from PySide6.QtWidgets import QApplication

from src.app.lodgazilla import PlotWorkspace
from src.data.plot_policy import PlotSource, plan_cards
from src.gui.plots.multi import MultiPlotWidget
from src.gui.plots.video.filmstrip import read_frames
from src.jobs.plot_drop import inspect_source, inspect_sources

LOG_START = 1_700_000_000.0


def write_video(path: Path, seconds: float = 2.0, fps: int = 10) -> None:
    """A small test video whose frame brightness rises with time, a keyframe every 5 frames."""
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        stream.codec_context.gop_size = 5
        for index in range(int(seconds * fps)):
            image = np.full((48, 64, 3), int(255 * index / (seconds * fps)), dtype=np.uint8)
            for packet in stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


class FilmstripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # A closed card's background read can still hold a file briefly on Windows.
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(ignore_cleanup_errors=True)))
        self.video = self.folder / "clip.mp4"
        write_video(self.video)
        self.h5 = self.folder / "data.h5"
        with h5py.File(self.h5, "w") as cache:
            cache.attrs["log_start_unix_seconds"] = LOG_START
            group = cache.create_group("series/voltage")
            group.attrs.update(time_dataset="time_unix", time_unit="s", navigator_name="voltage", unit="V")
            group.create_dataset("time_unix", data=LOG_START + np.arange(10, dtype=float))
            group.create_dataset("value", data=np.arange(10, dtype=float))
        self.source = PlotSource("video", self.video, folder=self.folder, offset=1.0)
        self.addCleanup(lambda: (QThreadPool.globalInstance().waitForDone(3000), qt_wait(100)))

    def wait_for(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate() and time.monotonic() < deadline:
            # Sleeping lets the frame decoder thread run; QTest.qWait keeps Python's lock.
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(predicate())

    def card(self, workspace=None):
        card = MultiPlotWidget(self.h5, "voltage")
        self.addCleanup(lambda: (card.dispose(), card.close(), card.deleteLater()))
        card.resize(900, 400)
        card.show()
        self.app.processEvents()
        return card

    def test_frames_are_read_at_keyframes_with_the_video_size(self):
        info, frames = read_frames(self.video, [0.0, 1.0, 5.0], 30, threading.Event())
        self.assertAlmostEqual(info.duration, 2.0, delta=0.15)
        self.assertAlmostEqual(info.aspect, 64 / 48)
        # Beyond the end of the video there is no frame.
        self.assertEqual(sorted(frames), [0.0, 1.0])
        self.assertEqual(frames[1.0].shape, (30, 40, 3))
        # The frame at 1 s comes from the keyframe at 1 s: brighter than the first.
        self.assertGreater(frames[1.0].mean(), frames[0.0].mean() + 50)

    def test_lane_shows_frames_at_their_data_time_beside_the_series(self):
        card = self.card()
        plot = card.series_plot
        self.assertIsNone(card.add_filmstrip(self.source))
        self.assertEqual(len(plot.filmstrips), 1)
        lane = plot.filmstrips[0]
        # The lane takes the next free lane above the series: one and a half lanes up.
        from src.app.settings import DEFAULT_PLOT_SETTINGS
        lane_height = DEFAULT_PLOT_SETTINGS["multi_series_lane_height_percent"] / 100
        self.assertAlmostEqual(lane.center, 1.5 * lane_height)
        self.assertEqual(lane.start, LOG_START + 1.0)
        plot.plot_item.vb.setXRange(LOG_START, LOG_START + 4, padding=0)
        self.wait_for(lambda: any(image.isVisible() for image in lane.images))
        for image in (image for image in lane.images if image.isVisible()):
            rect = image.mapRectToParent(image.boundingRect())
            self.assertGreaterEqual(rect.center().x(), lane.start - 0.01)
            self.assertLessEqual(rect.center().x(), lane.start + 2.2)
            self.assertAlmostEqual(rect.center().y(), lane.center, delta=0.01)
        # Panning by one frame slot uploads only the frames that are new in view.
        self.wait_for(lambda: not any(placeholder.isVisible() for placeholder in lane.placeholders))
        slot_width = plot._filmstrip_slots(lane)[0][1]
        uploads = []
        for image in lane.images:
            original = image.setImage
            image.setImage = lambda *args, original=original, **kwargs: (uploads.append(1), original(*args, **kwargs))
        plot.plot_item.vb.setXRange(LOG_START + slot_width, LOG_START + 4 + slot_width, padding=0)
        plot._refresh_filmstrips(request=False)
        shown = sum(image.isVisible() for image in lane.images)
        self.assertLessEqual(len(uploads), 2)
        self.assertGreater(shown, len(uploads))
        # Values boxes show the video time; the legend has a fixed row for the lane.
        boxes = plot.extra_marker_box_items(LOG_START + 1.5)
        self.assertEqual(boxes[0].entry.value, "0.500 s")
        self.assertEqual([entry.drag_source for entry in card.legend.entries], [None, self.source])
        self.assertEqual(card.filmstrip_time_ranges(), [(lane.start, lane.start + plot._frames.info[str(self.video)].duration)])
        # Removing the series keeps the card while the filmstrip remains; removing that closes it.
        closed = []
        card.close_requested.connect(closed.append)
        card.remove_series_at(0)
        self.assertEqual((card.series, len(plot.filmstrips), closed), ([], 1, []))
        card.remove_series_at(0)
        self.assertEqual(closed, [card])

    def test_pending_frames_show_outlines_and_a_lane_can_be_selected_and_dragged(self):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        card = self.card()
        plot = card.series_plot
        plot.plot_item.vb.setXRange(LOG_START, LOG_START + 4, padding=0)
        plot.plot_item.vb.setYRange(0, 1, padding=0)
        card.add_filmstrip(self.source)
        lane = plot.filmstrips[0]
        # Before decoding finishes, the slots inside the video show dashed outlines.
        self.assertTrue(any(placeholder.isVisible() for placeholder in lane.placeholders))
        self.wait_for(lambda: not any(placeholder.isVisible() for placeholder in lane.placeholders))
        self.assertTrue(any(image.isVisible() for image in lane.images))
        widget = plot.plot_widget
        view = plot.plot_item.vb

        def send(kind, point, button=Qt.MouseButton.LeftButton):
            buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else button
            QApplication.sendEvent(widget.viewport(), QMouseEvent(kind, point, point, button, buttons,
                                                                  Qt.KeyboardModifier.NoModifier))

        start = QPointF(widget.mapFromScene(view.mapViewToScene(QPointF(lane.start + 0.5, lane.center))))
        self.assertEqual(plot.hit_series(widget.mapToScene(start.toPoint())), lane.key)
        center = lane.center
        send(QEvent.Type.MouseButtonPress, start)
        send(QEvent.Type.MouseMove, start - QPointF(0, 40))
        send(QEvent.Type.MouseButtonRelease, start - QPointF(0, 40))
        # Selected and moved up; its frames follow.
        self.assertEqual(plot.selected, lane.key)
        self.assertGreater(lane.center, center + 0.05)
        self.assertTrue(lane.outline.isVisible())
        shown = next(image for image in lane.images if image.isVisible())
        self.assertAlmostEqual(shown.mapRectToParent(shown.boundingRect()).center().y(), lane.center, delta=0.01)
        # A click on empty space deselects it.
        empty = QPointF(widget.mapFromScene(view.mapViewToScene(QPointF(LOG_START + 3.5, 0.95))))
        send(QEvent.Type.MouseButtonPress, empty)
        send(QEvent.Type.MouseButtonRelease, empty)
        self.assertIsNone(plot.selected)
        self.assertFalse(lane.outline.isVisible())

    def test_selected_filmstrip_offset_is_its_video_sync_offset(self):
        card = self.card()
        plot = card.series_plot
        card.add_filmstrip(self.source)
        lane = plot.filmstrips[0]
        edited = []
        card.filmstrip_offset_edited.connect(lambda *args: edited.append(args))
        plot.select_series(lane.key)
        self.assertEqual(card.edited_time_offset(), 1.0)
        self.assertIn("clip.mp4", card.offset_button.toolTip())
        card.set_edited_time_offset(2.5)
        self.assertEqual(lane.source.offset, 2.5)
        self.assertEqual(card.time_offset, 0.0)
        self.assertEqual(plot.series_offsets, {})
        # Saved once the value stops changing, like a Video card's sync offset.
        self.wait_for(lambda: edited)
        self.assertEqual(edited, [(self.folder, self.video, 2.5)])

    def test_video_without_log_start_is_refused(self):
        card = self.card()
        other = self.folder / "other"
        other.mkdir()
        problem = card.add_filmstrip(PlotSource("video", self.video, folder=other))
        self.assertIn("no log start time", problem)
        self.assertEqual(card.series_plot.filmstrips, [])

    def test_video_only_card_policy_and_drop_into_an_existing_card(self):
        item = inspect_source(self.source)
        self.assertEqual(item.supported, ("video", "multi"))
        series = inspect_sources([PlotSource("series", self.h5, "voltage")])[0]
        # By default a video opens a Video card; Multi-series takes both into one card.
        self.assertEqual([plan.kind for plan in plan_cards([series, item])], ["multi", "video"])
        self.assertEqual([plan.kind for plan in plan_cards([series, item], "multi")], ["multi"])
        workspace = PlotWorkspace(default_plot_heights={"multi": 300})
        self.addCleanup(workspace.close_all_plots)
        card = workspace.open_plot_inputs([item], "multi")[0]
        self.assertIsInstance(card, MultiPlotWidget)
        self.assertEqual(card.series, [])
        self.assertTrue(card.series_plot.time_axis_metadata.is_unix_time)
        self.wait_for(lambda: workspace._plot_time_range(card) is not None)
        self.assertAlmostEqual(workspace._plot_time_range(card)[0], LOG_START + 1.0)
        # A new sync offset moves the filmstrip.
        card.series_plot.set_filmstrip_offset(self.video, 3.0)
        self.assertEqual(card.series_plot.filmstrips[0].start, LOG_START + 3.0)
        # The same video is not added twice; a series joins the card.
        workspace.add_plot_inputs_to_card([item, series], card)
        self.assertEqual(len(card.series_plot.filmstrips), 1)
        self.assertEqual(card.series, [(self.h5, "voltage")])


    def test_navigator_drags_a_video_as_a_selection(self):
        import json
        from types import SimpleNamespace
        from unittest import mock
        from PySide6.QtCore import Qt
        from src.data.plot_policy import SELECTION_MIME_TYPE
        from src.gui.navigator.widget import CopyableTreeWidget
        tree = CopyableTreeWidget()
        self.addCleanup(tree.deleteLater)
        tree.navigator = SimpleNamespace(selected_plot_sources=lambda: [self.source])
        payloads = []

        def capture(drag, _actions):
            payloads.append(json.loads(bytes(drag.mimeData().data(SELECTION_MIME_TYPE)).decode()))
            return Qt.DropAction.IgnoreAction

        with mock.patch("src.gui.navigator.widget.exec_data_drag", side_effect=capture):
            tree.startDrag(Qt.DropAction.CopyAction)
        self.assertEqual(payloads, [[self.source.payload()]])


    def test_video_card_dropped_on_a_multi_series_legend_adds_a_filmstrip(self):
        from PySide6.QtCore import QPoint
        from src.gui.plots.base import PlotBaseWidget
        source = self.source

        class VideoCard(PlotBaseWidget):
            # Stands in for a Video card, whose OpenGL view cannot run headless.
            supports_horizontal_layout = True

            def filmstrip_source(self):
                return source

        workspace = PlotWorkspace(default_plot_heights={"multi": 300, "video": 200})
        self.addCleanup(workspace.close_all_plots)
        workspace.resize(900, 900)
        workspace.show()
        multi = workspace.add_multi_plot([(self.h5, "voltage")])
        video = VideoCard(self.folder, "clip.mp4")
        workspace._register_plot(video, plot_type="video")
        self.app.processEvents()
        controller = workspace._drop_controller
        controller.source = video
        stack = workspace.splitter
        legend_point = multi.legend.mapTo(stack, multi.legend.rect().center())
        target, position, _preview = controller.destination(legend_point)
        self.assertEqual((target, position), (multi, "filmstrip"))
        # Elsewhere on the card the drag still reorders cards.
        canvas_point = multi.mapTo(stack, QPoint(multi.width() // 2, multi.height() - 20))
        self.assertIn(controller.destination(canvas_point)[1], {"above", "below"})
        controller.source = None
        workspace._card_dropped(video, multi, "filmstrip")
        self.assertEqual([lane.path for lane in multi.series_plot.filmstrips], [self.video])
        self.assertIn(video, workspace.plots())

    def test_filmstrip_row_drags_its_video_and_moves_it(self):
        import json
        from unittest import mock
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from src.data.plot_policy import SELECTION_MIME_TYPE
        from src.gui.plots.legend import _LegendLabel
        card = self.card()
        card.add_filmstrip(self.source)
        self.app.processEvents()
        label = next(label for label in card.legend.findChildren(_LegendLabel) if label.entry.key == 1)
        payloads = []

        def capture(drag, _actions):
            payloads.append(json.loads(bytes(drag.mimeData().data(SELECTION_MIME_TYPE)).decode()))
            return Qt.DropAction.MoveAction

        def send(kind, point):
            buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
            QApplication.sendEvent(label, QMouseEvent(kind, point, point, Qt.MouseButton.LeftButton, buttons,
                                                      Qt.KeyboardModifier.NoModifier))

        with mock.patch("src.gui.plots.legend.exec_data_drag", side_effect=capture):
            send(QEvent.Type.MouseButtonPress, QPointF(5, 5))
            send(QEvent.Type.MouseMove, QPointF(40, 5))
        self.assertEqual(payloads, [[self.source.payload()]])
        # A completed move removes the filmstrip from this card; the series stays.
        self.assertEqual(card.series_plot.filmstrips, [])
        self.assertEqual(card.series, [(self.h5, "voltage")])


if __name__ == "__main__":
    unittest.main()
