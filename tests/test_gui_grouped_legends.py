"""Shared grouped-legend behavior across Cartesian and geographic cards."""

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import h5py
import numpy as np
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QThreadPool
from PySide6.QtGui import QColor
from tests.qt_wait import qt_wait
from PySide6.QtWidgets import QApplication, QToolButton

from src.app.lodgazilla import PlotWorkspace
from src.data.xyz import AxisSource
from src.gui.plots.base import SERIES_MIME_TYPE
from src.gui.plots.gnss import GnssPlotWidget, load_gnss_track
from src.gui.plots.legend import LegendDragSource, _LegendLabel
from src.gui.plots.legend_groups import GROUP_MIME_TYPE
from src.gui.plots.xyz import XYZPlotWidget
from tests.test_xyz_plot import FakeView
from tests.test_gui_gnss_3d_plot_widget import ScriptMapView


# Background loads share a thread pool; wait on wall time, not iteration counts.
WAIT_SECONDS = 10.0


class GroupedLegendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "data.h5"
        self.names = ["GNSS/latitude", "GNSS/longitude", "GNSS/altitude"]
        with h5py.File(self.path, "w") as cache:
            for name, values in zip(self.names, ([60., 61., 62.], [24., 25., 26.], [100., 101., 102.])):
                group = cache.create_group("series/" + name)
                group.attrs.update(time_dataset="time_unix", time_unit="s", navigator_name=name)
                group.create_dataset("time_unix", data=[0., 1., 2.])
                group.create_dataset("value", data=values)
        self.axes = [AxisSource(self.path, name) for name in self.names]
        self.entries = [(axis.path, axis.name) for axis in self.axes]
        self.patches = [mock.patch.object(XYZPlotWidget, "view_class", FakeView),
                        mock.patch.object(GnssPlotWidget, "_initialize_map", lambda self: None),
                        mock.patch("src.gui.plots.gnss_3d.QWebEngineView", ScriptMapView)]
        for patch in self.patches:
            patch.start()
        self.workspace = PlotWorkspace(settings_path=Path(self.temp.name) / "settings.json")
        self.workspace.resize(1100, 1000)
        self.workspace.show()
        self.controller = self.workspace._data_drop_controller
        self.controller.start()

    def tearDown(self):
        self.controller.stop()
        self.workspace.close_all_plots()
        QThreadPool.globalInstance().waitForDone(int(WAIT_SECONDS * 1000))
        qt_wait(100)
        self.workspace.close()
        self.workspace.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for patch in reversed(self.patches):
            patch.stop()
        self.temp.cleanup()

    def make_card(self, kind):
        card = {"xy": lambda: self.workspace.add_xy_plot(*self.entries[:2]),
                "xyz": lambda: self.workspace.add_xyz_plot(*self.entries),
                "gnss": lambda: self.workspace.add_gnss_plot(*self.entries[0]),
                "gnss_3d": lambda: self.workspace.add_gnss_3d_plot(*self.entries[0])}[kind]()
        self.wait_loaded(card)
        self.app.processEvents()
        return card

    def test_a_click_selects_a_graph_trace_or_track_and_its_legend_group(self):
        from src.gui.plots.series_style import series_line_width
        selected, normal = series_line_width(selected=True), series_line_width()
        # X/Y: a left click near the graph selects it (and still places the first marker).
        xy = self.make_card("xy")
        xy.grouped_series.insert((AxisSource(self.path, self.names[1]), AxisSource(self.path, self.names[2])))
        self.wait_loaded(xy)
        self.app.processEvents()
        curve = xy._graph_items[1][0]
        x, y = curve.getData()
        scene = xy.plot_item.vb.mapViewToScene(QPointF(float(x[1]), float(y[1])))
        click = mock.Mock(scenePos=mock.Mock(return_value=scene), double=mock.Mock(return_value=False))
        click.button.return_value = Qt.MouseButton.LeftButton
        xy._on_scene_click(click)
        self.assertEqual(xy.selected_series, 1)
        self.assertEqual([graph[0].opts["pen"].width() for graph in xy._graph_items], [normal, selected])
        self.assertEqual(len(xy.legend.selected_rows()), 1 + 2)  # its heading and X, Y rows
        click.scenePos.return_value = QPointF(-5000, -5000)
        xy._on_scene_click(click)
        self.assertIsNone(xy.selected_series)
        # 3D plot: the trace under the click; an axis line comes first.
        xyz = self.make_card("xyz")
        positions = xyz._scene_positions[0]
        pixel = xyz.view.project(positions[1:2])[0]
        xyz._on_view_clicked(Qt.MouseButton.LeftButton, QPointF(*pixel))
        self.assertEqual(xyz.selected_series, 0)
        self.assertEqual(xyz.legend.selected_rows()[-1], xyz.legend.group_headers[0])
        xyz._on_view_clicked(Qt.MouseButton.LeftButton, QPointF(-500, -500))
        self.assertIsNone(xyz.selected_series)
        # Map (its page is off here): the selected track's Legend group is highlighted; the page
        # reports a click's track through select_track and draws it with setTrackSelection.
        from src.gui.plots.gnss import map_html, track_selection_script
        gnss = self.make_card("gnss")
        gnss.select_series(0)
        self.assertEqual(gnss.legend.selected_rows()[-1], gnss.legend.group_headers[0])
        gnss.select_series(None)
        self.assertEqual(gnss.legend.selected_rows(), [])
        self.assertIn("bridge.select_track(trackAt(e.containerPoint.x,e.containerPoint.y,8))", map_html())
        self.assertEqual(track_selection_script(1), f"setTrackSelection(1,{normal},{selected});")
        # 3D Map: the page draws the selected track wider.
        gnss_3d = self.make_card("gnss_3d")
        gnss_3d._auto_pending = False
        gnss_3d.map_view.runJavaScript("map.events.load()")
        gnss_3d.map_view.loadFinished.emit(True)
        gnss_3d.bridge.track_clicked.emit(0)
        js = gnss_3d.map_view.runJavaScript
        self.assertEqual(js("viewSettings.selectedTrack"), 0)
        self.assertEqual(js("map.overlay.props.layers[0].props.getWidth(tracks[0],{index:0})"), selected)
        gnss_3d.bridge.track_clicked.emit(-1)
        self.assertEqual(js("map.overlay.props.layers[0].props.getWidth(tracks[0],{index:0})"), normal)

    def test_xy_center_view_brings_every_graph_back(self):
        xy = self.make_card("xy")
        view = xy.plot_item.vb
        full = [list(axis) for axis in view.viewRange()]
        view.setRange(xRange=(1000, 1001), yRange=(1000, 1001), padding=0)
        self.assertIn(xy.center_view_button, xy.header_controls)
        xy.center_view_button.click()
        for (low, high), (full_low, full_high) in zip(view.viewRange(), full):
            self.assertAlmostEqual(low, full_low, delta=abs(full_high - full_low) * 0.1 + 1e-6)
            self.assertAlmostEqual(high, full_high, delta=abs(full_high - full_low) * 0.1 + 1e-6)

    def test_map_center_view_fits_the_tracks(self):
        from src.gui.plots.gnss import map_html
        gnss = self.make_card("gnss")
        self.assertIn(gnss.center_view_button, gnss.header_controls)
        self.assertIn("function fitTracks()", map_html())
        gnss.map_view, gnss._map_ready = mock.Mock(), True
        gnss.center_view()
        gnss.map_view.page.return_value.runJavaScript.assert_called_once_with("fitTracks();")
        gnss.map_view = None

    def test_track_transform_keeps_the_track_shape(self):
        from PySide6.QtQml import QJSEngine
        from src.gui.plots.gnss import TRACK_TRANSFORM_JS
        engine = QJSEngine()
        # Top-level const bindings stay local to one evaluate call: check in the same call.
        result = engine.evaluate(TRACK_TRANSFORM_JS + r"""
            function metres(a,b){let k=Math.cos((a[0]+b[0])/2*DEGREE);
              return Math.hypot((b[0]-a[0])*DEGREE*EARTH_RADIUS_M,(b[1]-a[1])*k*DEGREE*EARTH_RADIUS_M)}
            let pts=[[60,24,0],[60.01,24,1],[60.01,24.02,2]],c=boxCenter(pts);
            let same=movePoints(pts,c,{dlat:0,dlon:0,angle:0});
            let turned=movePoints(pts,c,{dlat:0,dlon:0,angle:90});
            let north=movePoints(pts,c,{dlat:10,dlon:5,angle:0});
            JSON.stringify({same:same===pts,center:c,
              sides:[metres(pts[0],pts[1]),metres(pts[1],pts[2])],
              turned:[metres(turned[0],turned[1]),metres(turned[1],turned[2])],
              north:[metres(north[0],north[1]),metres(north[1],north[2])],
              turnedCenter:boxCenter(turned),northCenter:boxCenter(north),times:turned.map(p=>p[2]),
              angle:[normalAngle(190),normalAngle(-180)],wrap:wrapLongitude(181),
              text:moveText([0,0],{dlat:0,dlon:-0.001}),angleText:angleText(15)})
        """)
        self.assertFalse(result.isError(), result.toString())
        data = json.loads(result.toString())
        self.assertTrue(data["same"])
        self.assertEqual(data["times"], [0, 1, 2])
        # Rotating and moving north keep every side's length in metres (within 1%).
        for key in ("turned", "north"):
            for side, original in zip(data[key], data["sides"]):
                self.assertAlmostEqual(side, original, delta=original * 0.01)
        self.assertAlmostEqual(data["turnedCenter"][0], data["center"][0], places=6)
        self.assertAlmostEqual(data["northCenter"][0], data["center"][0] + 10, places=6)
        self.assertAlmostEqual(data["northCenter"][1], data["center"][1] + 5, places=6)
        self.assertEqual(data["angle"], [-170, 180])
        self.assertAlmostEqual(data["wrap"], -179)
        self.assertEqual(data["text"], "111.2 m W, 0.0 m N")
        self.assertEqual(data["angleText"], "15.0°")

    def test_map_page_moves_and_rotates_only_the_selected_track(self):
        from src.gui.plots.gnss import map_html
        html = map_html()
        # Ctrl moves, Shift rotates, only with a track selected; the press never reaches Leaflet.
        self.assertIn("!(e.ctrlKey||e.shiftKey)||selectedTrack<0", html)
        self.assertIn("rotate:!e.ctrlKey", html)
        self.assertIn("e.preventDefault();e.stopPropagation();", html)
        self.assertIn("bridge.move_track(d.index,t.dlat,t.dlon,t.angle)", html)
        self.assertIn("trackTransforms.push(track.transform||null)", html)
        self.assertIn("function movePoints(", html)

    def test_moved_tracks_last_for_the_card_only(self):
        card = self.make_card("gnss")
        card.grouped_series.insert((AxisSource(self.path, self.names[1]), AxisSource(self.path, self.names[0])))
        self.wait_loaded(card)
        self.app.processEvents()
        self.assertEqual(card._track_groups, [0, 1])
        card.controls_button.click()
        self.app.processEvents()
        self.assertTrue(card.reset_position_button.isHidden())
        # The page reports a move of the second track: its heading says so, Reset position shows.
        card._track_moved(1, 0.5, -0.25, 30.0)
        key = card._track_key(1)
        self.assertEqual(card.moved_tracks, {key: (0.5, -0.25, 30.0)})
        self.assertEqual(card._drawn_track_transforms(), [None, {"dlat": 0.5, "dlon": -0.25, "angle": 30.0}])
        headings = [entry.group for entry in card.legend.entries]
        self.assertIn("Track 2 (moved)", headings)
        self.assertIn("Track 1", headings)
        self.assertFalse(card.reset_position_button.isHidden())
        # Not saved in workspaces.
        self.assertNotIn("transform", json.dumps(card.workspace_state(), default=str))
        # A reorder keeps the move with its track; a removed track drops it.
        card.grouped_series.reorder(1, 0)
        self.wait_loaded(card)
        self.assertEqual(card._track_key(0), key)
        self.assertEqual(list(card.moved_tracks), [key])
        self.assertEqual(card._drawn_track_transforms()[0], {"dlat": 0.5, "dlon": -0.25, "angle": 30.0})
        self.assertEqual([entry.group for entry in card.legend.entries][0], "Track 1 (moved)")
        card.remove_series_at(0)
        self.wait_loaded(card)
        self.assertEqual(card.moved_tracks, {})
        self.assertTrue(card.reset_position_button.isHidden())
        # Reset position puts every track back; moving back to zero also clears it.
        card._track_moved(0, 0.1, 0.0, 0.0)
        card._track_moved(0, 0.0, 0.0, 0.0)
        self.assertEqual(card.moved_tracks, {})
        card._track_moved(0, 0.1, 0.0, 0.0)
        card.reset_position_button.click()
        self.assertEqual(card.moved_tracks, {})
        self.assertEqual([entry.group for entry in card.legend.entries][0], "Track 1")

    def test_map_header_controls_share_one_height(self):
        card = self.make_card("gnss")
        card.controls_button.click()
        self.app.processEvents()
        # Street/Satellite and Scale bar are as tall as the Draw style buttons and Sample limit selector.
        panels = (card.map_type, card.scale_bar_panel, card.draw_style_selector, card.sample_limit_combo)
        self.assertTrue(all(panel.isVisible() for panel in panels))
        self.assertEqual({panel.height() for panel in panels}, {card.close_button.height() - 2})
        # And on one line: their centres are level.
        centres = {panel.geometry().center().y() for panel in panels}
        self.assertLessEqual(max(centres) - min(centres), 1)

    def wait_loaded(self, card):
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            if not card._loading:
                break
            qt_wait(10)
        self.assertFalse(card._loading)
        self.assertFalse(any(isinstance(result, str) for result in card.results), card.results)

    def mime(self, axis):
        mime = QMimeData()
        mime.setData(SERIES_MIME_TYPE, json.dumps({"h5_path": str(axis.path), "series_name": axis.name,
                                                "component": axis.component}).encode())
        return mime

    def prime(self, mime):
        self.assertTrue(self.controller._read_payload(mime))
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            if self.controller._job is None:
                break
            qt_wait(10)
        self.assertIsNone(self.controller._job)

    def point(self, card, key=None):
        if key is not None:
            row = card.legend.rows[key]
            card.legend._scroll.ensureWidgetVisible(row, 0, 0)
            self.app.processEvents()
            return row.mapTo(self.workspace.viewport(), row.rect().center())
        return card.mapTo(self.workspace.viewport(), QPoint(15, card.height() - 25))

    def drop(self, card, axis, key=None):
        self.prime(self.mime(axis))
        destination = self.controller.update_preview(self.point(card, key))
        self.assertIsNotNone(destination)
        self.assertEqual(destination[0], "groups" if key is None else "replace")
        if key is None:
            self.assertTrue(self.controller.card_hint.isVisible())
            self.assertFalse(self.controller.line.isVisible())
            self.assertFalse(card.legend.drop_hint.isVisible())
        else:
            self.assertTrue(card.legend.drop_hint.isVisible())
            self.assertFalse(self.controller.card_hint.isVisible())
        self.controller.apply_drop(destination, self.controller.payload)
        self.controller.finish()
        self.wait_loaded(card)
        self.app.processEvents()

    def test_all_grouped_cards_have_header_color_and_wait_for_empty_axes(self):
        for kind, roles in (("xy", ("X", "Y")), ("xyz", ("X", "Y", "Z")),
                            ("gnss", ("Lat", "Lon")), ("gnss_3d", ("Lat", "Lon", "Alt"))):
            with self.subTest(card=kind):
                card = self.make_card(kind)
                self.assertEqual([entry.role for entry in card.legend.entries], list(roles))
                self.assertEqual(len(card.legend.group_headers[0].findChildren(QToolButton)), 1)
                self.assertFalse(any(row.findChildren(QToolButton) for row in card.legend.rows))
                self.drop(card, self.axes[0])
                self.assertEqual(len(card.grouped_series.groups), 2)
                self.assertIsNone(card.results[1])
                self.assertEqual([entry.text for entry in card.legend.entries[len(roles) + 1:]],
                                 ["empty"] * (len(roles) - 1))
                for index in range(1, len(roles)):
                    self.drop(card, self.axes[index], len(roles) + index)
                self.assertIsNotNone(card.results[1])
                if kind == "xy":
                    self.assertGreater(len(card._graph_items[1][0].xData), 0)
                if kind.startswith("gnss"):
                    self.assertEqual(len(card._track_data), 2)
                self.workspace.remove_plot(card)
                self.app.processEvents()

    def drag_group(self, source, target, group_index=0):
        label = source.legend.group_headers[group_index].findChild(_LegendLabel)
        def perform(drag, actions):
            self.assertEqual(actions, Qt.DropAction.MoveAction)
            self.assertTrue(drag.mimeData().hasFormat(GROUP_MIME_TYPE))
            self.prime(drag.mimeData())
            destination = self.controller.update_preview(self.point(target), drag.source())
            self.assertIsNotNone(destination)
            self.controller.apply_drop(destination, self.controller.payload, drag.source())
            self.controller.finish()
            return Qt.DropAction.MoveAction
        label._press_position = QPoint()
        event = mock.Mock()
        event.buttons.return_value = Qt.MouseButton.LeftButton
        event.position.return_value = QPointF(30, 10)
        with mock.patch("src.gui.plots.legend.exec_data_drag", side_effect=perform):
            label.mouseMoveEvent(event)
        if hasattr(target, "grouped_series"):
            self.wait_loaded(target)

    def test_three_axes_split_into_xy_pairs_and_move_keeps_group_color(self):
        source, target = self.make_card("xyz"), self.make_card("xy")
        source.grouped_series.set_color(0, "#654321")
        self.wait_loaded(source)
        self.drag_group(source, target)
        self.assertNotIn(source, self.workspace.plots())
        groups = target.grouped_series.groups
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[1].axes, tuple(self.axes[:2]))
        self.assertEqual(groups[2].axes, (self.axes[2], None))
        self.assertEqual([group.color for group in groups[1:]], ["#654321", "#654321"])
        self.assertIsNone(target.results[2])

    def test_two_axes_transferred_to_3d_leave_z_empty(self):
        source, target = self.make_card("xy"), self.make_card("xyz")
        self.drag_group(source, target)
        self.assertEqual(target.grouped_series.groups[1].axes, (*self.axes[:2], None))
        self.assertIsNone(target.results[1])
        self.drop(target, self.axes[2], 5)
        self.assertIsNotNone(target.results[1])

    def test_group_reordering_and_color_change_use_shared_controls(self):
        card = self.make_card("xy")
        card.add_legend_group(tuple(reversed(self.axes[:2])))
        self.wait_loaded(card)
        first = card.grouped_series.groups[0]
        self.drag_group(card, card)
        self.assertEqual(len(card.grouped_series.groups), 2)
        self.assertIs(card.grouped_series.groups[1], first)
        with mock.patch("src.gui.plots.legend.QColorDialog.getColor", return_value=QColor("#abcdef")):
            card.legend.group_headers[1].findChild(QToolButton).click()
        self.wait_loaded(card)
        self.assertEqual(first.color, "#abcdef")
        self.assertEqual(card._graph_items[1][0].opts["pen"].color().name(), "#abcdef")

    def test_group_can_move_to_an_ordinary_line_card(self):
        source = self.make_card("xyz")
        target = self.workspace.add_line_plot(*self.entries[0])
        self.app.processEvents()
        self.drag_group(source, target)
        self.assertEqual(target.series, self.entries)
        self.assertEqual(target.legend.group_headers, [])

    def test_gnss_coordinates_can_come_from_different_files_and_time_units(self):
        other = Path(self.temp.name) / "other.h5"
        with h5py.File(other, "w") as cache:
            group = cache.create_group("series/lon")
            group.attrs.update(time_dataset="time_ms", time_unit="ms", time_reference="unix")
            group.create_dataset("time_ms", data=[0., 2000.])
            group.create_dataset("value", data=[20., 30.])
        track = load_gnss_track(self.path, self.names[0], other, "lon")
        np.testing.assert_equal(track["lon"], [20., 20., 30.])

    def test_3d_gnss_group_updates_keep_camera_and_render_both_colors(self):
        card = self.make_card("gnss_3d")
        card.map_view.runJavaScript("map.events.load()")
        card.map_view.loadFinished.emit(True)
        initial_fit = card.map_view.runJavaScript("map.fitCount")
        card.add_legend_group(self.axes, color="#ff0000")
        self.wait_loaded(card)
        self.assertEqual(card.map_view.runJavaScript("tracks.length"), 2)
        self.assertEqual(card.map_view.runJavaScript("map.fitCount"), initial_fit)
        self.assertEqual(card.map_view.runJavaScript("map.overlay.props.layers[0].props.getColor(tracks[1])"), [255, 0, 0])

    def test_scrolled_axis_drop_and_drag_edge_scrolling_stop_on_cancel(self):
        card = self.make_card("xy")
        card.add_legend_group(tuple(self.axes[:2]) * 8)
        self.wait_loaded(card)
        self.app.processEvents()
        scroll = card.legend._scroll.verticalScrollBar()
        self.assertGreater(scroll.maximum(), 0)
        self.prime(self.mime(self.axes[2]))
        point = card.legend.mapTo(self.workspace.viewport(), QPoint(12, card.legend.height() - 4))
        self.controller.update_preview(point)
        self.assertTrue(card.legend._scroll_timer.isActive())
        qt_wait(150)
        self.assertGreater(scroll.value(), 0)
        self.controller.finish()
        self.assertFalse(card.legend._scroll_timer.isActive())
        key = len(card.legend.entries) - 1
        self.drop(card, self.axes[2], key)
        self.assertEqual(card.grouped_series.axis(key), self.axes[2])

    def test_cancelled_group_drag_leaves_source_intact(self):
        card = self.make_card("xyz")
        label = card.legend.group_headers[0].findChild(_LegendLabel)
        original = card.grouped_series.groups[0]
        label._press_position = QPoint()
        event = mock.Mock()
        event.buttons.return_value = Qt.MouseButton.LeftButton
        event.position.return_value = QPointF(30, 10)
        with mock.patch("src.gui.plots.legend.exec_data_drag", return_value=Qt.DropAction.IgnoreAction):
            label.mouseMoveEvent(event)
        self.assertEqual(card.grouped_series.groups, [original])
        self.assertIn(card, self.workspace.plots())

    def drag_axis(self, card, key, destination=None):
        label = card.legend.rows[key].findChild(_LegendLabel)

        def perform(drag, actions):
            self.assertEqual(actions, Qt.DropAction.MoveAction | Qt.DropAction.LinkAction)
            self.assertFalse(drag.dragCursor(Qt.DropAction.LinkAction).isNull())
            if destination is None:
                return Qt.DropAction.IgnoreAction
            self.prime(drag.mimeData())
            target = destination(drag.source()) if callable(destination) else destination
            self.assertIsNotNone(target)
            self.controller.apply_drop(target, self.controller.payload, drag.source())
            self.controller.finish()
            # Inserting/swapping groups can destroy the original label mid-drag.
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            return Qt.DropAction.LinkAction if target[0] == "swap" else Qt.DropAction.MoveAction

        label._press_position = QPoint()
        event = mock.Mock()
        event.buttons.return_value = Qt.MouseButton.LeftButton
        event.position.return_value = QPointF(30, 10)
        with mock.patch("src.gui.plots.legend.exec_data_drag", side_effect=perform):
            label.mouseMoveEvent(event)
        self.wait_loaded(card)

    def test_axis_moves_between_grouped_cards_leave_source_empty(self):
        for kind in ("xy", "xyz", "gnss", "gnss_3d"):
            for key in (None, 1):
                with self.subTest(card=kind, replacement=key is not None):
                    source, target = self.make_card(kind), self.make_card(kind)
                    self.drag_axis(source, 0, lambda drag_source: self.controller.update_preview(
                        self.point(target, key), drag_source))
                    self.wait_loaded(target)
                    self.assertIsNone(source.grouped_series.axis(0))
                    self.assertEqual(source.legend.entries[0].text, "empty")
                    self.assertIsNone(source.results[0])
                    self.assertNotIn(self.entries[0], source.series)
                    self.assertIn(source, self.workspace.plots())
                    target_key = target.grouped_series.width if key is None else key
                    self.assertEqual(target.grouped_series.axis(target_key), self.axes[0])
                    self.workspace.remove_plot(source)
                    self.workspace.remove_plot(target)
                    self.app.processEvents()

    def test_axis_move_to_new_group_tracks_original_group_after_insertion(self):
        card = self.make_card("xy")
        card.add_legend_group(self.axes[:2])
        self.wait_loaded(card)
        original = card.grouped_series.groups[1]
        self.drag_axis(card, 2, ("groups", card, 0))
        self.assertEqual(card.grouped_series.groups[0].axes, (self.axes[0], None))
        self.assertEqual(card.grouped_series.groups[1].axes, tuple(self.axes[:2]))
        self.assertIs(card.grouped_series.groups[2], original)
        self.assertEqual(original.axes, (None, self.axes[1]))
        self.assertIsNone(card.results[0])
        self.assertIsNotNone(card.results[1])
        self.assertIsNone(card.results[2])

    def test_axis_swap_with_empty_slot_clears_only_original_assignment(self):
        card = self.make_card("xy")
        card.add_legend_group([self.axes[2]])
        self.wait_loaded(card)
        self.drag_axis(card, 0, lambda source: self.controller.update_preview(
            self.point(card, 3), source, Qt.KeyboardModifier.ControlModifier))
        self.assertEqual(card.grouped_series.groups[0].axes, (None, self.axes[1]))
        self.assertEqual(card.grouped_series.groups[1].axes, (self.axes[2], self.axes[0]))
        self.assertIsNone(card.results[0])
        self.assertIsNotNone(card.results[1])

    def test_axis_move_to_new_card_leaves_source_empty(self):
        card = self.make_card("xy")
        self.drag_axis(card, 0, ("gap", 1, "line"))
        target = next(plot for plot in self.workspace.plots() if plot is not card)
        self.assertEqual(target.series, [self.entries[0]])
        self.assertEqual(card.grouped_series.groups[0].axes, (None, self.axes[1]))
        self.assertIsNone(card.results[0])

    def test_axis_move_to_existing_ordinary_card_leaves_source_empty(self):
        card = self.make_card("xy")
        target = self.workspace.add_line_plot(*self.entries[2])
        self.app.processEvents()
        self.drag_axis(card, 0, lambda source: self.controller.update_preview(self.point(target), source))
        self.assertEqual(target.series, [self.entries[2], self.entries[0]])
        self.assertIsNone(card.grouped_series.axis(0))
        self.assertIsNone(card.results[0])

    def test_time_axis_move_preserves_component(self):
        source, target = self.make_card("xyz"), self.make_card("xyz")
        axis = AxisSource(self.path, self.names[0], "time")
        source.grouped_series.replace(0, axis)
        self.wait_loaded(source)
        self.drag_axis(source, 0, lambda drag_source: self.controller.update_preview(
            self.point(target, 0), drag_source))
        self.wait_loaded(target)
        self.assertEqual(target.grouped_series.axis(0), axis)
        self.assertIsNone(source.grouped_series.axis(0))

    def test_cancelled_axis_drag_leaves_source_intact(self):
        card = self.make_card("xyz")
        self.drag_axis(card, 0)
        self.assertEqual(card.grouped_series.groups[0].axes, tuple(self.axes))
        self.assertIsNotNone(card.results[0])

    def test_same_card_axis_move_or_ctrl_swap_across_all_grouped_cards(self):
        for kind in ("xy", "xyz", "gnss", "gnss_3d"):
            for swap in (False, True):
                with self.subTest(card=kind, swap=swap):
                    card = self.make_card(kind)
                    modifiers = Qt.KeyboardModifier.ControlModifier if swap else Qt.KeyboardModifier.NoModifier
                    self.drag_axis(card, 0, lambda source: self.controller.update_preview(
                        self.point(card, 1), source, modifiers))
                    self.assertEqual(card.grouped_series.axis(1), self.axes[0])
                    self.assertEqual(card.grouped_series.axis(0), self.axes[1] if swap else None)
                    if not swap:
                        self.assertIsNone(card.results[0])
                        self.assertEqual(card.legend.entries[0].text, "empty")
                    self.workspace.remove_plot(card)
                    self.app.processEvents()

    def test_ctrl_swap_between_groups_preserves_both_assignments(self):
        card = self.make_card("xy")
        card.add_legend_group(self.axes[1:])
        self.wait_loaded(card)
        self.drag_axis(card, 0, lambda source: self.controller.update_preview(
            self.point(card, 3), source, Qt.KeyboardModifier.ControlModifier))
        self.assertEqual(card.grouped_series.groups[0].axes, (self.axes[2], self.axes[1]))
        self.assertEqual(card.grouped_series.groups[1].axes, (self.axes[1], self.axes[0]))

    def test_ctrl_on_another_card_or_new_group_still_moves(self):
        source, target = self.make_card("xy"), self.make_card("xy")
        self.drag_axis(source, 0, lambda drag_source: self.controller.update_preview(
            self.point(target, 1), drag_source, Qt.KeyboardModifier.ControlModifier))
        self.wait_loaded(target)
        self.assertIsNone(source.grouped_series.axis(0))
        self.assertEqual(target.grouped_series.axis(1), self.axes[0])
        self.drag_axis(source, 1, lambda drag_source: self.controller.update_preview(
            self.point(source), drag_source, Qt.KeyboardModifier.ControlModifier))
        self.assertEqual(source.grouped_series.groups[0].axes, (None, None))
        self.assertEqual(source.grouped_series.groups[1].axes, (self.axes[1], None))

    def test_grouped_card_outline_axis_outline_and_group_insertion_line_are_exclusive(self):
        for kind in ("xy", "xyz", "gnss", "gnss_3d"):
            with self.subTest(card=kind):
                card = self.make_card(kind)
                self.prime(self.mime(self.axes[0]))
                self.assertEqual(self.controller.update_preview(self.point(card)), ("groups", card, 1))
                self.assertTrue(self.controller.card_hint.isVisible())
                self.assertFalse(self.controller.line.isVisible())
                self.assertFalse(card.legend.drop_hint.isVisible())

                self.assertEqual(self.controller.update_preview(self.point(card, 1)), ("replace", card, 1))
                self.assertTrue(card.legend.drop_hint.isVisible())
                self.assertIn("border: 2px solid", card.legend.drop_hint.styleSheet())
                self.assertFalse(self.controller.card_hint.isVisible())
                self.assertFalse(self.controller.line.isVisible())

                header = card.legend.group_headers[0]
                point = header.mapTo(self.workspace.viewport(), header.rect().center())
                self.assertEqual(self.controller.update_preview(point), ("groups", card, 0))
                self.assertTrue(card.legend.drop_hint.isVisible())
                self.assertEqual(card.legend.drop_hint.height(), 2)
                self.assertFalse(self.controller.card_hint.isVisible())
                self.assertFalse(self.controller.line.isVisible())
                self.controller.finish()
                self.assertFalse(card.legend.drop_hint.isVisible())
                self.workspace.remove_plot(card)
                self.app.processEvents()

    def test_selector_creates_partial_grouped_cards_and_legend_completes_them(self):
        # An ordinary numeric source without any GNSS partner must work too.
        with h5py.File(self.path, "a") as cache:
            cache.copy("series/GNSS/latitude", "series/ordinary")
            cache["series/ordinary"].attrs["navigator_name"] = "ordinary"
        axis = AxisSource(self.path, "ordinary")
        for kind, roles in (("xy", ("X", "Y")), ("xyz", ("X", "Y", "Z")),
                            ("gnss", ("Lat", "Lon")), ("gnss_3d", ("Lat", "Lon", "Alt"))):
            with self.subTest(card=kind):
                first = self.workspace.add_line_plot(*self.entries[0])
                self.app.processEvents()
                self.prime(self.mime(axis))
                point = first.mapTo(self.workspace.viewport(), QPoint(100, 1))
                self.assertEqual(self.controller.update_preview(point), ("gap", 0, None))
                self.controller._show_selector()
                selector = self.controller.selector
                self.assertTrue({"xy", "xyz", "gnss", "gnss_3d"}.issubset(selector.options))
                index = selector.options.index(kind)
                point = selector.mapTo(self.workspace.viewport(), QPoint(
                    int((index + 0.5) * selector.width() / len(selector.options)), selector.height() // 2))
                destination = self.controller.update_preview(point)
                self.assertEqual(destination, ("gap", 0, kind))
                self.controller.apply_drop(destination, self.controller.payload)
                self.controller.finish()
                card = next(plot for plot in self.workspace.plots() if plot is not first)
                self.wait_loaded(card)
                self.assertIs(self.workspace.plots()[0], card)
                self.assertEqual(card.drop_card_type, kind)
                self.assertEqual(card.grouped_series.groups[0].axes, (axis,) + (None,) * (len(roles) - 1))
                self.assertEqual([entry.role for entry in card.legend.entries], list(roles))
                self.assertEqual([entry.text for entry in card.legend.entries[1:]], ["empty"] * (len(roles) - 1))
                self.assertEqual(card.results, [None])
                for key in range(1, len(roles)):
                    self.drop(card, self.axes[key], key)
                self.assertIsNotNone(card.results[0])
                self.workspace.remove_plot(card)
                self.workspace.remove_plot(first)
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
