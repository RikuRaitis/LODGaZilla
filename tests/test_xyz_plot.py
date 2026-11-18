"""3D alignment, card controls, asynchronous lifecycle and Navigator actions."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from dataclasses import replace
from unittest import mock

import h5py
import numpy as np
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, Signal, QThreadPool
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from tests.qt_wait import qt_wait
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from src.app.lodgazilla import LODGaZillaWindow, PlotWorkspace
from src.data.xyz import AxisSource, XYZData, load_xyz_data
from src.gui.plots.xyz import XYZPlotWidget, XYZView
from src.gui.plots.legend import _LegendLabel
from src.gui.plots.xy import load_xy_data
from src.jobs.xyz import XYZLoadCoordinator
from tests.plot_menu_helpers import wait_for_plot_menu


class XYZDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "data.h5"
        self.addCleanup(self.temp.cleanup)

    def write(self, name, times, values, unit="s", **attrs):
        with h5py.File(self.path, "a") as cache:
            group = cache.create_group("series/" + name)
            group.attrs.update(time_dataset="time", time_unit=unit, navigator_name=name, **attrs)
            group.create_dataset("time", data=times)
            group.create_dataset("value", data=values)
        return AxisSource(self.path, name)

    def test_aligns_different_rates_and_units_without_extrapolation(self):
        x = self.write("x", [0, 1, 2, 3, 4], [0, 10, 20, 30, 40])
        y = self.write("y", [500, 2500, 3500], [100, 200, 300], unit="ms")
        z = self.write("z", [0, 2, 4], [-1, -2, -3])
        result = load_xyz_data((x, y, z))
        np.testing.assert_equal(result.time, [1, 2, 3])
        np.testing.assert_equal(result.positions, [[10, 100, -1], [20, 200, -2], [30, 200, -2]])
        self.assertFalse(result.sampled)

    def test_units_and_gnss_roles_are_kept_for_equal_scale(self):
        lat = self.write("GNSS/latitude", [0, 1, 2], [60.0, 60.1, 60.2])
        lon = self.write("GNSS/longitude", [0, 1, 2], [24.0, 24.1, 24.2])
        alt = self.write("GNSS/altitude", [0, 1, 2], [5.0, 6.0, 7.0])
        with h5py.File(self.path, "a") as cache:
            for name, unit in (("GNSS/latitude", "deg"), ("GNSS/longitude", "deg"), ("GNSS/altitude", "m")):
                cache["series/" + name].attrs["unit"] = unit
        result = load_xyz_data((lat, lon, AxisSource(alt.path, alt.name, "time")))
        self.assertEqual(result.units, ("deg", "deg", "s"))
        self.assertEqual(result.roles, ("latitude", "longitude", "time"))
        result = load_xyz_data((lat, lon, alt))
        self.assertEqual((result.units[2], result.roles[2]), ("m", ""))

    def test_time_axis_and_absolute_cross_file_alignment(self):
        x = self.write("x", [0, 1, 2], [5, 6, 7])
        with h5py.File(self.path, "a") as cache:
            cache.attrs.update(log_start_unix_seconds=1000., time_start_seconds=0.)
        other = Path(self.temp.name) / "other.h5"
        with h5py.File(other, "w") as cache:
            group = cache.create_group("series/y")
            group.attrs.update(time_dataset="time_unix", time_unit="s", time_reference="unix")
            group.create_dataset("time_unix", data=[1000., 1001., 1002.])
            group.create_dataset("value", data=[10., 20., 30.])
        result = load_xyz_data((AxisSource(x.path, x.name, "time"), AxisSource(other, "y"), x))
        np.testing.assert_equal(result.positions[:, 0], [1000, 1001, 1002])
        np.testing.assert_equal(result.positions[:, 1], [10, 20, 30])
        self.assertIn("Unix s", result.labels[0])

    def test_bounded_sampling_range_and_chunk_boundary_alignment(self):
        times = np.arange(140_000, dtype=float)
        axes = tuple(self.write(name, times, times + i) for i, name in enumerate("xyz"))
        result = load_xyz_data(axes, maximum_points=200)
        self.assertLessEqual(len(result.time), 200)
        self.assertTrue(result.sampled)
        np.testing.assert_equal(result.positions[:, 1], result.time + 1)
        limited = load_xyz_data(axes, time_range=(65_534, 65_538))
        np.testing.assert_equal(limited.time, np.arange(65_534, 65_539))

    def test_nonfinite_values_preserve_breaks_and_invalid_inputs_report_errors(self):
        x = self.write("x", [0, 1, 2], [1, np.nan, 3])
        y = self.write("y", [0, 1, 2], [1, 2, 3])
        z = self.write("z", [0, 1, 2], [3, 2, 1])
        result = load_xyz_data((x, y, z))
        self.assertTrue(np.isnan(result.positions[1, 0]))
        disjoint = self.write("late", [10, 11], [1, 2])
        with self.assertRaisesRegex(ValueError, "overlapping"):
            load_xyz_data((x, y, disjoint))
        empty = self.write("empty", [], [])
        with self.assertRaisesRegex(ValueError, "nonempty"):
            load_xyz_data((x, y, empty))
        unsorted = self.write("unsorted", [0, 2, 1, 3], [1, 2, 3, 4])
        with self.assertRaisesRegex(ValueError, "ascending"):
            load_xyz_data((x, y, unsorted))
        cancelled = Event()
        cancelled.set()
        with self.assertRaises(InterruptedError):
            load_xyz_data((x, y, z), cancelled=cancelled)

    def test_xy_alignment_allows_repeated_nearest_samples(self):
        self.write("x", [0, 1, 2, 3, 4], [0, 10, 20, 30, 40])
        self.write("y", [0, 4], [100, 200])
        _, _, values, _, _ = load_xy_data(self.path, "x", self.path, "y")
        np.testing.assert_equal(values, [100, 100, 100, 200, 200])


class FakeView(QWidget):
    """Test card wiring without requiring a headless OpenGL context."""
    scroll_requested = Signal(int)
    clicked = Signal(object, object)
    double_clicked = Signal(object)
    camera_changed = Signal()
    axis_stretch_requested = Signal(int, int)
    selected_axis = None

    def __init__(self, parent):
        super().__init__(parent)
        self.items = []
        self.camera = {}

    def project(self, positions):
        # A fixed top-down projection: scene X/Y to pixels around (200, 200).
        positions = np.asarray(positions, dtype=float)
        return np.c_[200 + 100 * positions[:, 0], 200 - 100 * positions[:, 1]]

    def addItem(self, item):
        self.items.append(item)

    def removeItem(self, item):
        self.items.remove(item)

    def setCameraPosition(self, **values):
        self.camera.update(values)

    def setBackgroundColor(self, color):
        self.background = color


class XYZCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "data.h5"
        with h5py.File(self.path, "w") as cache:
            for index, name in enumerate("xyz"):
                group = cache.create_group("series/" + name)
                group.attrs.update(time_dataset="time_s", time_unit="s", navigator_name=name)
                group.create_dataset("time_s", data=[0., 1., 2.])
                group.create_dataset("value", data=np.arange(3.) + index)
        self.entries = [(self.path, name) for name in "xyz"]
        self.patch = mock.patch.object(XYZPlotWidget, "view_class", FakeView)
        self.patch.start()
        self.workspace = PlotWorkspace(settings_path=Path(self.temp.name) / "settings.json")

    def tearDown(self):
        self.workspace.close_all_plots()
        QThreadPool.globalInstance().waitForDone(3000)
        self.app.processEvents()
        self.workspace.close()
        self.workspace.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.patch.stop()
        self.temp.cleanup()

    def wait_loaded(self, card):
        for _ in range(300):
            if not card._loading:
                break
            qt_wait(10)
        self.assertFalse(card._loading)
        self.assertTrue(all(isinstance(item, XYZData) for item in card.results), card.current_status)

    def test_multiple_traces_time_assignment_camera_range_and_removal(self):
        card = self.workspace.add_xyz_plot(*self.entries)
        self.wait_loaded(card)
        self.assertEqual(self.workspace._plot_navigator_type(card), "xyz")
        self.assertFalse(card.accepts_series_drop)
        np.testing.assert_equal(card.results[0].positions, [[0, 1, 2], [1, 2, 3], [2, 3, 4]])
        self.workspace.resize(1000, 700)
        self.workspace.show()
        card.controls_button.click()
        self.app.processEvents()
        button = card.center_view_button
        self.assertFalse(button.icon().isNull())
        self.assertEqual(button.text(), "")
        self.assertEqual(button.toolTip(), "Center view: show all the data again")
        self.assertLess(button.geometry().right(), card.close_button.x())
        self.assertEqual(button.y(), card.close_button.y())
        self.assertIs(card.childAt(button.geometry().center()), button)
        self.assertEqual(card.content_layout.count(), 1)
        self.assertIs(card.content_layout.itemAt(0).widget(), card.view)
        card.view.camera["azimuth"] = 123
        QTest.mouseClick(card.center_view_button, Qt.MouseButton.LeftButton)
        self.assertEqual(card.view.camera["azimuth"], 45)
        self.assertEqual(card.view.camera["distance"], 6)
        card.view.camera["azimuth"] = 123
        card.add_trace(list(reversed(self.entries)))
        self.wait_loaded(card)
        self.assertEqual(len(card.results), 2)
        self.assertEqual(card.view.camera["azimuth"], 123)
        card.legend.component_requested.emit(3, "time")
        self.wait_loaded(card)
        self.assertEqual(card.traces[1].axes[0].component, "time")
        np.testing.assert_equal(card.results[1].positions[:, 0], [0, 1, 2])
        self.workspace.set_plot_navigator_range(card, 1, 2)
        self.wait_loaded(card)
        np.testing.assert_equal(card.results[0].time, [1, 2])
        card.remove_trace(0)
        self.wait_loaded(card)
        self.assertEqual(len(card.traces), 1)
        card.remove_trace(0)
        self.assertEqual(self.workspace.plots(), [])

    def test_menu_offers_partial_3d_and_can_append_to_existing_card(self):
        navigator = mock.Mock()
        navigator.tree.viewport.return_value.mapToGlobal.return_value = QPoint()
        window = SimpleNamespace(navigator=navigator, plot_workspace=self.workspace)
        for count in (1, 2, 3):
            navigator.selected_series_entries.return_value = self.entries[:count]
            menu = QMenu()
            with mock.patch("src.app.lodgazilla.QMenu", return_value=menu), mock.patch.object(menu, "exec"):
                LODGaZillaWindow.show_series_context_menu(window, *self.entries[0], QPoint())
            wait_for_plot_menu(menu)
            actions = {action.data(): action for action in menu.actions()}
            self.assertTrue("xyz" in actions or "3D" in actions["default"].text())
            if count == 3:
                actions["default"].trigger()
            menu.deleteLater()
        card = self.workspace.plots()[0]
        self.wait_loaded(card)
        menu = QMenu()
        def choose_existing(*_args):
            wait_for_plot_menu(menu)
            # Retain QAction wrappers while using their submenu (PySide ownership).
            actions = {action.text(): action for action in menu.actions()}
            submenu = actions["Existing 3D cards"].menu()
            submenu.actions()[0].trigger()
        with mock.patch("src.app.lodgazilla.QMenu", return_value=menu), mock.patch.object(menu, "exec", side_effect=choose_existing):
            LODGaZillaWindow.show_series_context_menu(window, *self.entries[0], QPoint())
        self.wait_loaded(card)
        self.assertEqual(len(card.traces), 2)
        menu.deleteLater()

    def test_grouped_legend_replaces_and_swaps_axes_across_traces(self):
        card = self.workspace.add_xyz_plot(*self.entries)
        card.add_trace(list(reversed(self.entries)))
        self.wait_loaded(card)
        self.assertEqual([entry.role for entry in card.legend.entries], list("XYZXYZ"))
        self.assertEqual(len(card.legend.group_headers), 2)
        colors = [trace.color for trace in card.traces]
        card.view.camera["azimuth"] = 123
        card.replace_legend_series(1, self.entries[2], "time")
        self.wait_loaded(card)
        self.assertEqual(card.traces[0].axes[1], AxisSource(*self.entries[2], "time"))
        np.testing.assert_equal(card.results[0].positions[:, 1], [0, 1, 2])
        card.swap_legend_series(1, 5)
        self.wait_loaded(card)
        self.assertEqual(card.traces[1].axes[2], AxisSource(*self.entries[2], "time"))
        self.assertEqual(card.traces[0].axes[1], AxisSource(*self.entries[0]))
        self.assertEqual(card.legend.entries[5].component, "time")
        self.assertEqual(set(card.traces[0].sources), {AxisSource(*entry) for entry in self.entries})
        self.assertEqual([trace.color for trace in card.traces], colors)
        self.assertEqual(card.view.camera["azimuth"], 123)

    def test_legend_context_menu_switches_values_and_time(self):
        card = self.workspace.add_xyz_plot(*self.entries)
        self.wait_loaded(card)
        for component, title in (("time", "Time"), ("value", "Values")):
            label = card.legend.rows[0].findChild(_LegendLabel)
            menu = QMenu()
            def choose(*_args):
                actions = {action.text(): action for action in menu.actions()}
                self.assertNotIn("Remove series from plot", actions)
                self.assertFalse(actions[title].isChecked())
                actions[title].trigger()
            with mock.patch("src.gui.plots.legend.QMenu", return_value=menu), mock.patch.object(menu, "exec", side_effect=choose):
                label.contextMenuEvent(mock.Mock(globalPos=lambda: QPoint()))
            self.wait_loaded(card)
            self.assertEqual(card.traces[0].axes[0].component, component)
            self.assertEqual(card.legend.entries[0].component, component)
            menu.deleteLater()

    def test_time_axis_drop_preserves_component_and_rejects_ordinary_cards(self):
        from PySide6.QtCore import QMimeData
        import json
        from src.gui.plots.base import SERIES_MIME_TYPE
        card = self.workspace.add_xyz_plot(*self.entries)
        self.wait_loaded(card)
        self.workspace.resize(1100, 900)
        self.workspace.show()
        self.app.processEvents()
        controller = self.workspace._data_drop_controller
        mime = QMimeData()
        mime.setData(SERIES_MIME_TYPE, json.dumps({"h5_path": str(self.path), "series_name": "y", "component": "time"}).encode())
        controller._read_payload(mime)
        for _ in range(300):
            if controller._job is None:
                break
            qt_wait(10)
        point = card.legend.rows[0].mapTo(self.workspace.viewport(), card.legend.rows[0].rect().center())
        destination = controller.update_preview(point)
        self.assertEqual(destination, ("replace", card, 0))
        self.assertGreater(card.legend.drop_hint.height(), 2)
        controller.apply_drop(destination, controller.payload)
        self.wait_loaded(card)
        self.assertEqual(card.traces[0].axes[0], AxisSource(*self.entries[1], "time"))
        xy = self.workspace.add_xy_plot(*self.entries[:2])
        self.app.processEvents()
        point = xy.legend.rows[0].mapTo(self.workspace.viewport(), xy.legend.rows[0].rect().center())
        self.assertIsNone(controller.update_preview(point))
        controller.finish()

    def test_a_clicked_axis_is_stretched_by_ctrl_wheel_and_fit_restores_the_box(self):
        from PySide6.QtCore import QPointF
        card = self.workspace.add_xyz_plot(*self.entries)
        self.wait_loaded(card)
        before = card._scene_positions[0].copy()
        # The fake view looks down on X and Y: the X axis line runs along y = 200 at the origin.
        x_line = card.view.project(card._axis_segments[0])
        on_x = QPointF(*(x_line[0] + x_line[1]) / 2)
        self.assertEqual(card.axis_at(on_x), 0)
        self.assertIsNone(card.axis_at(QPointF(5, 5)))
        # A click on its line selects the axis, and places no marker; the view then stretches it.
        with mock.patch.object(card, "_place_marker_from_view") as place:
            card.view.clicked.emit(Qt.MouseButton.LeftButton, on_x)
            place.assert_not_called()
            self.assertEqual((card.selected_axis, card.view.selected_axis), (0, 0))
            # A second click deselects it; a click elsewhere deselects it and places the marker.
            card.view.clicked.emit(Qt.MouseButton.LeftButton, on_x)
            self.assertIsNone(card.selected_axis)
            card.view.clicked.emit(Qt.MouseButton.LeftButton, on_x)
            card.view.clicked.emit(Qt.MouseButton.LeftButton, QPointF(5, 5))
            self.assertIsNone(card.view.selected_axis)
            place.assert_called_once()
        card.select_axis(0)
        card.view.axis_stretch_requested.emit(0, 240)
        self.assertAlmostEqual(card.axis_stretch[0], 1.15 ** 2)
        np.testing.assert_allclose(card._scene_positions[0][:, 0], before[:, 0] * 1.15 ** 2)
        np.testing.assert_allclose(card._scene_positions[0][:, 1:], before[:, 1:])
        # The axis line is drawn stretched too, and the hit test follows it.
        np.testing.assert_allclose(card._axis_segments[0][:, 0], [-1.15 ** 2, 1.15 ** 2])
        # Saved and restored with the workspace; Fit returns to the box.
        from PySide6.QtGui import QVector3D
        card.view.opts = {"center": QVector3D(), "distance": 6, "elevation": 25, "azimuth": 45, "fov": 60}
        self.assertEqual(card.workspace_state()["axis_stretch"], [1.15 ** 2, 1.0, 1.0])
        card.fit_axes_button.click()
        np.testing.assert_allclose(card._scene_positions[0], before)
        self.assertNotIn("axis_stretch", card.workspace_state())
        card.restore_workspace_state({"axis_stretch": [2.0, 0.5, 1.0]})
        np.testing.assert_allclose(card.axis_stretch, [2.0, 0.5, 1.0])
        # Stretching has limits either way.
        for _ in range(100):
            card.stretch_axis(2, -1200)
        self.assertAlmostEqual(card.axis_stretch[2], 0.001)

    def test_equal_scale_needs_distances_on_every_axis(self):
        card = self.workspace.add_xyz_plot(*self.entries)
        self.wait_loaded(card)
        # These series have no unit: Equal scale is off, and its tooltip says why.
        self.assertFalse(card.equal_scale_button.isEnabled())
        self.assertIn("X is in no unit", card.equal_scale_button.toolTip())
        # With latitude, longitude and metres it sets one metre to the same length on each axis.
        card.results = [replace(result, units=("deg", "deg", "m"), roles=("latitude", "longitude", ""))
                        for result in card.results]
        card._extent = (np.array([60.0, 24.0, 0.0]), np.array([60.01, 24.02, 50.0]))
        card._update_equal_scale_button()
        self.assertTrue(card.equal_scale_button.isEnabled())
        factors = card.equal_scale_factors()
        # 0.01° of latitude is about 1106 m; 0.02° of longitude at 60° about 1113 m: the longest.
        self.assertAlmostEqual(factors[1], 1.0)
        self.assertAlmostEqual(factors[0], 1105.74 / 1113.2, places=2)
        self.assertAlmostEqual(factors[2], 50 / 1113.2, places=3)

    def test_use_shader_draws_traces_with_wide_lines(self):
        from src.gui.plots.series_style import series_line_width
        from src.gui.plots.wide_lines import WideLinePlotItem
        card = self.workspace.add_xyz_plot(*self.entries)
        self.wait_loaded(card)
        # Off by default: plain lines, which OpenGL's core profile draws 1 px wide.
        self.assertFalse(card.use_shader or card.use_shader_check.isChecked())
        self.assertFalse(any(isinstance(item, WideLinePlotItem) for item in card.view.items))
        card.use_shader_check.setChecked(True)
        card.select_series(0)
        wide = [item for item in card.view.items if isinstance(item, WideLinePlotItem)]
        self.assertEqual([item.width for item in wide], [series_line_width(selected=True)])
        # In Auto the points are wider than their line, so they show on it.
        import pyqtgraph.opengl as gl
        sizes = [item.size for item in card.view.items if isinstance(item, gl.GLScatterPlotItem)]
        self.assertIn(series_line_width(selected=True) + 3, sizes)
        self.assertNotIn(3, sizes)
        from PySide6.QtGui import QVector3D
        card.view.opts = {"center": QVector3D(), "distance": 6, "elevation": 25, "azimuth": 45, "fov": 60}
        self.assertTrue(card.workspace_state()["use_shader"])
        card.restore_workspace_state({})
        self.assertFalse(card.use_shader_check.isChecked())
        self.assertNotIn("use_shader", card.workspace_state())

    def test_axes_buttons_choose_the_axes_position(self):
        card = self.workspace.add_xyz_plot(*self.entries)
        self.wait_loaded(card)
        buttons = card.axes_buttons
        self.assertEqual(list(buttons), ["origin", "corner"])
        self.assertTrue(buttons["origin"].isChecked() and not buttons["corner"].isChecked())
        self.assertEqual(buttons["corner"].toolTip(), "Axes at corner")
        buttons["corner"].click()
        self.assertEqual(card.axes_position, "corner")
        self.assertFalse(buttons["origin"].isChecked())
        # Restoring a workspace checks the matching button.
        card.set_axes_position("origin")
        self.assertTrue(buttons["origin"].isChecked() and not buttons["corner"].isChecked())
        self.assertFalse(hasattr(card, "axes_combo"))

    def test_render_keeps_unix_time_ticks_distinct_and_pads_constant_axes(self):
        card = self.workspace.add_xyz_plot(*self.entries)
        self.wait_loaded(card)
        card._loaded([XYZData(np.arange(3.), np.array([
            [1_700_000_000., 5, 0], [1_700_000_001., 5, 1], [1_700_000_002., 5, 2],
        ]), ("Time", "Constant", "Value"), False)])
        card.set_axes_position("corner")
        texts = [item.text for item in card.view.items if hasattr(item, "text")]
        # Each label is "<axis> <value>"; the shared corner's label lists the three minimums.
        ticks = {}
        for text in texts:
            for part in text.split("  "):
                axis, value = part.split(" ", 1)
                ticks.setdefault(axis, set()).add(value)
        self.assertEqual(len(ticks["X"]), 3)
        self.assertEqual(len(ticks["Y"]), 3)
        self.assertEqual(sum(text.startswith("X ") and " Y " in text and " Z " in text for text in texts), 1)

    def test_loading_coalesces_requests_and_shutdown_does_not_wait(self):
        parent = QWidget()
        coordinator = XYZLoadCoordinator(parent)
        entered, release = Event(), Event()
        results = []
        coordinator.loaded.connect(results.append)
        def slow(axes, **kwargs):
            entered.set()
            release.wait(3)
            return axes[0]
        with mock.patch("src.jobs.xyz.load_xyz_data", side_effect=slow):
            coordinator.request([("first", "y", "z")])
            self.assertTrue(entered.wait(3))
            coordinator.request([("discarded", "y", "z")])
            coordinator.request([("latest", "y", "z")])
            release.set()
            for _ in range(300):
                if results:
                    break
                qt_wait(10)
            self.assertEqual(results, [["latest"]])
            release.clear()
            entered.clear()
            coordinator.request([("closing", "y", "z")])
            self.assertTrue(entered.wait(3))
            coordinator.shutdown()
            self.assertTrue(coordinator.worker.cancelled.is_set())
            release.set()
            QThreadPool.globalInstance().waitForDone(3000)
            self.app.processEvents()
            self.assertEqual(results, [["latest"]])
        parent.deleteLater()

    def test_mouse_drag_rotates_real_view_camera(self):
        view = XYZView()
        old = view.opts["azimuth"]
        view.mousePos = QPointF(10, 10)
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(50, 30), QPointF(50, 30),
                            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        view.mouseMoveEvent(event)
        self.assertNotEqual(view.opts["azimuth"], old)
        view.deleteLater()


if __name__ == "__main__":
    unittest.main()
