"""Two markers, the selection and values boxes on X/Y, 3D plot and Map cards."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import h5py
import numpy as np
from PySide6.QtCore import QPointF, Qt, QThreadPool
from tests.qt_wait import qt_wait
from PySide6.QtWidgets import QApplication

from src.app.lodgazilla import PlotWorkspace
from src.gui.plots.gnss import track_distance_m
from src.gui.plots.xyz import XYZPlotWidget
from tests.test_xyz_plot import FakeView


class PointMarkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # A closed card's background read can still hold the file briefly on Windows.
        directory = self.enterContext(tempfile.TemporaryDirectory(ignore_cleanup_errors=True))
        self.path = Path(directory) / "data.h5"
        with h5py.File(self.path, "w") as cache:
            for index, name in enumerate("xyz"):
                group = cache.create_group("series/" + name)
                group.attrs.update(time_dataset="time", time_unit="s", navigator_name=name, unit="V")
                group.create_dataset("time", data=np.arange(10, dtype=float))
                group.create_dataset("value", data=np.arange(10, dtype=float) * (index + 1))
        self.workspace = PlotWorkspace(default_plot_heights={"xy": 300, "xyz": 300, "line": 200})
        # Cleanups run in reverse: close the cards, then let background reads release the file.
        self.addCleanup(lambda: (QThreadPool.globalInstance().waitForDone(3000), qt_wait(300)))
        self.addCleanup(self.workspace.close_all_plots)
        self.workspace.resize(900, 700)
        self.workspace.show()

    def wait_loaded(self, card):
        for _ in range(500):
            if not card._loading:
                return
            qt_wait(10)
        self.fail("Card did not finish loading")

    def wait_for(self, predicate):
        for _ in range(500):
            if predicate():
                return
            qt_wait(10)
        self.fail("Condition not met")

    def click(self, card, button, time_index, double=False):
        event = mock.Mock()
        event.button.return_value = button
        event.double.return_value = double
        point = QPointF(card._x[time_index], card._y[time_index])
        with mock.patch.object(card.plot_item.vb, "mapSceneToView", return_value=point):
            card._on_scene_click(event)

    def test_xy_two_markers_selection_and_one_box_per_graph(self):
        card = self.workspace.add_xy_plot((self.path, "x"), (self.path, "y"))
        self.wait_loaded(card)
        self.assertFalse(card.plot_item.menuEnabled())
        second = []
        card.second_marker_placed.connect(second.append)
        self.click(card, Qt.MouseButton.LeftButton, 2)
        self.click(card, Qt.MouseButton.RightButton, 6)
        self.assertEqual((card.marker_values(), second), ((2.0, 6.0), [6.0]))
        self.assertEqual([len(highlight.data) for highlight in card.highlights], [1, 1])
        # The graph's points between the markers are drawn as the selection.
        np.testing.assert_allclose(card._selection_items[0].getData()[0], [2, 3, 4, 5, 6])
        # Drawn above the graph so it stays visible, below the marker points.
        curve, points = card._graph_items[0]
        self.assertGreater(card._selection_items[0].zValue(), max(curve.zValue(), points.zValue()))
        self.assertLess(card._selection_items[0].zValue(), card.highlights[0].zValue())
        self.wait_for(lambda: card.value_boxes.statistics)
        card.show_marker_data(2.0, 0)
        self.wait_for(lambda: card.value_boxes.visible_boxes(0))
        box = card.value_boxes.visible_boxes(0)[0]
        self.assertEqual(box.item.x_value, 2.0)
        self.assertEqual(box.item.y_value, 4.0)
        self.assertEqual(box.toPlainText(), "2 V, 4 V")
        self.assertIn("Selection Δt: 4 s", box.details_text())
        self.assertIn("X Min: 2 V", box.details_text())
        self.assertIn("Y Max: 12 V", box.details_text())
        # A right double-click removes the second marker and the selection.
        self.click(card, Qt.MouseButton.RightButton, 6, double=True)
        self.assertEqual(card.marker_values(), (2.0, None))
        self.assertEqual(len(card._selection_items[0].getData()[0] or []), 0)
        self.assertNotIn("Selection", box.details_text())

    def test_xy_markers_link_with_time_cards(self):
        line = self.workspace.add_line_plot(self.path, "x")
        card = self.workspace.add_xy_plot((self.path, "x"), (self.path, "y"))
        self.wait_loaded(card)
        self.workspace.set_markers_linked(True)
        self.click(card, Qt.MouseButton.RightButton, 5)
        self.assertEqual(line.marker_values()[1], 5.0)
        line.set_vertical_marker(3.0, 0)
        self.assertEqual(card.marker_values(), (3.0, 5.0))

    def test_3d_plot_click_markers_selection_and_boxes(self):
        with mock.patch.object(XYZPlotWidget, "view_class", FakeView):
            card = self.workspace.add_xyz_plot(*[(self.path, name) for name in "xyz"])
        self.wait_loaded(card)
        pixels = card.view.project(card._scene_positions[0])
        card.view.clicked.emit(Qt.MouseButton.LeftButton, QPointF(*pixels[3]))
        card.view.clicked.emit(Qt.MouseButton.RightButton, QPointF(*pixels[7]))
        self.assertEqual(card.marker_values(), (3.0, 7.0))
        marker_points = [item for item in card._marker_items if type(item).__name__ == "GLScatterPlotItem"]
        self.assertEqual(len(marker_points), 2)
        card.show_marker_data(3.0, 0)
        box = card.value_boxes.visible_boxes(0)[0]
        self.assertEqual(box.item.anchor(), tuple(float(value) for value in pixels[3]))
        self.assertEqual(box.toPlainText(), "3, 6, 9")
        self.assertIn("Points: 5 (drawn)", box.details_text())
        card.view.double_clicked.emit(Qt.MouseButton.LeftButton)
        self.assertEqual(card.marker_values(), (None, 7.0))

    def test_map_selection_reports_points_and_distance(self):
        # One degree of latitude is about 111.2 km.
        self.assertAlmostEqual(track_distance_m([60.0, 61.0], [24.0, 24.0]), 111_195, delta=10)
        from src.gui.plots.gnss import GnssPlotWidget
        layer = mock.Mock()
        card = SimpleNamespace(
            _marker_times={0: 1.0, 1: 3.0}, value_boxes=layer,
            _track_data=[{"time": [0.0, 1.0, 2.0, 3.0], "lat": [60.0, 60.0, 61.0, 62.0], "lon": [24.0] * 4}],
            grouped_series=SimpleNamespace(groups=[]),
        )
        for name in ("marker_values", "_update_selection", "_track_key"):
            setattr(card, name, getattr(GnssPlotWidget, name).__get__(card))
        card._update_selection()
        layer.set_selection_duration.assert_called_once_with(2.0)
        points, distance = layer.set_statistics.call_args.args[0][(0,)]
        self.assertEqual(points, "Points: 3 (drawn)")
        self.assertIn("Distance: 222", distance)

    def test_video_timeline_shows_markers_and_selection_inside_the_video(self):
        from src.gui.plots.video.widget import TimelineSlider, VideoPlotWidget
        timeline = TimelineSlider(Qt.Orientation.Horizontal)
        self.addCleanup(timeline.deleteLater)
        timeline.resize(400, 24)
        timeline.setRange(0, 10_000)
        card = SimpleNamespace(data_start=1000.0, offset_seconds=2.0, duration=10.0, timeline=timeline)
        for name in ("set_timeline_markers", "_update_timeline_marks"):
            setattr(card, name, getattr(VideoPlotWidget, name).__get__(card))
        # Video time = marker time - data start - offset; outside the video, no mark.
        card.set_timeline_markers(1004.0, 1009.5)
        self.assertEqual(timeline.marks, [2000, 7500])
        card.set_timeline_markers(1004.0, 1020.0)
        self.assertEqual(timeline.marks, [2000, None])
        card.set_timeline_markers(1004.0, 1009.5)
        image = timeline.grab().toImage()
        first = image.pixelColor(round(timeline.mark_x(2000)), 12)
        inside = image.pixelColor(round(timeline.mark_x(5000)), 4)
        outside = image.pixelColor(round(timeline.mark_x(9000)), 4)
        self.assertEqual(first.name(), "#ff3b30")
        self.assertNotEqual(inside, outside)

    def test_workspace_updates_video_timelines_when_markers_move(self):
        from src.gui.plots.video.widget import VideoPlotWidget
        video = mock.Mock(spec=VideoPlotWidget)
        self.workspace._global_second_marker_timestamp = 5.0
        with mock.patch.object(self.workspace, "plots", return_value=[video]):
            self.workspace.global_second_marker_changed.emit(5.0)
        video.set_timeline_markers.assert_called_once_with(None, 5.0)


if __name__ == "__main__":
    unittest.main()
