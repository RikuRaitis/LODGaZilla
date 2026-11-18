import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import tempfile
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import h5py
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QPoint, Qt, QMimeData
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QMenu

from src.gui.plots.series_style import series_line_width, set_series_line_widths  # noqa: E402
from src.app.lodgazilla import PlotWorkspace, LODGaZillaWindow
from src.app.settings import save_settings_section, PLOT_SECTION
from src.gui.plots.multi import MultiPlotWidget
from src.gui.plots.event_state import EventStatePlotWidget, event_state_lane_height
from src.gui.plots.base import SERIES_MIME_TYPE
from src.gui.plots.legend import _LegendLabel


from tests.markers_shown import with_markers_shown  # noqa: E402


@with_markers_shown
class MultiPlotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'data.h5'
        self.settings = Path(self.temp.name) / 'settings.json'
        with h5py.File(self.path, 'w') as h5:
            for name, values, unit, kind in (
                    ('voltage', [0, 1, 2, 1], 'V', ''),
                    ('pressure', [1000, 2000, 3000, 2000], 'Pa', ''),
                    ('state', [0, 1, 1, 0], 'bool', 'state'),
                    ('event', [1, 2, 3, 4], 'index', 'event')):
                group = h5.create_group('series/' + name)
                group.attrs.update(time_dataset='time_s', time_unit='s', unit=unit,
                                   event_state_kind=kind, navigator_name=name)
                group.create_dataset('time_s', data=[0., 1., 2., 3.])
                group.create_dataset('value', data=values)
        self.card = MultiPlotWidget(self.path, 'voltage', settings_path=self.settings)
        self.card.resize(800, 400)
        self.card.show()
        self.app.processEvents()

    def tearDown(self):
        self.card.dispose()
        self.card.close()
        self.card.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_independent_scales_survive_reload_add_remove_and_settings(self):
        self.card.add_series(self.path, 'pressure')
        plot = self.card.series_plot
        selected = (self.path, 'pressure')
        plot.select_series(selected)
        original = plot.transforms[(self.path, 'voltage')]
        plot.move_selected(.2)
        plot.zoom_selected(2, .5)
        adjusted = plot.transforms[selected]
        plot.update_visible_data()
        self.card.add_series(self.path, 'state')
        self.assertEqual(plot.selected, selected)
        self.assertEqual(plot.transforms[selected], adjusted)
        self.assertEqual(plot.transforms[(self.path, 'voltage')], original)
        self.assertEqual(plot.additional_series[0].curve.opts['pen'].width(), 5)
        # The widths are global (Settings > Plot > Cards); the main window sets them and redraws the cards.
        set_series_line_widths(2, 9)
        self.addCleanup(set_series_line_widths, 2, 5)
        self.card.apply_series_widths()
        self.assertEqual(plot.additional_series[0].curve.opts['pen'].width(), 9)
        self.card.remove_series_at(0)
        self.assertEqual(plot.selected, selected)
        self.assertEqual(plot.transforms[selected], adjusted)
        self.assertEqual(plot.curve.opts['pen'].width(), 9)
        with h5py.File(self.path) as h5:
            np.testing.assert_equal(h5['series/pressure/value'][:], [1000, 2000, 3000, 2000])

    def test_axis_wheel_without_selection_zooms_all_series_vertically(self):
        for name in ('pressure', 'state', 'event'):
            self.card.add_series(self.path, name)
        plot = self.card.series_plot
        widget = plot.plot_widget
        # Leave enough room for one wheel step without needing gap compression.
        plot.transforms = {key: (gain, offset + .15) for key, (gain, offset) in plot.transforms.items()}
        plot.apply_transforms()
        self.app.processEvents()
        self.assertIsNone(plot.selected)
        axis = plot.selected_axis.sceneBoundingRect()
        view = plot.plot_item.vb.sceneBoundingRect()
        # Axis scene bounds include grid lines inside the canvas; aim at the
        # actual left margin, including when deselection hides the axis label.
        point = QPointF(widget.mapFromScene(QPointF((axis.left() + view.left()) / 2, view.center().y())))
        scene_point = widget.mapToScene(point.toPoint())
        self.assertFalse(plot.plot_item.vb.sceneBoundingRect().contains(scene_point))
        original = dict(plot.transforms)
        x_range = list(plot.plot_item.vb.viewRange()[0])
        for delta, factor in ((120, 1 / .85), (-120, .85)):
            before = dict(plot.transforms)
            wheel = QWheelEvent(point, point, QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                                Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(widget.viewport(), wheel)
            self.assertTrue(wheel.isAccepted())
            self.assertIsNone(plot.selected)
            self.assertEqual(plot.plot_item.vb.viewRange()[0], x_range)
            for key, (gain, offset) in before.items():
                actual = plot.transforms[key]
                self.assertAlmostEqual(actual[0], gain if plot.event_kinds.get(key) else gain * factor)
                center = plot.value_midpoints[key] * gain + offset
                self.assertAlmostEqual(plot.value_midpoints[key] * actual[0] + actual[1], center)
            for key, curve, _, _ in plot.items():
                self.assertAlmostEqual(curve.transform().m22(), plot.transforms[key][0])
                self.assertEqual(curve.opts['pen'].width(), series_line_width())
            for key, items in plot.timeline_items.items():
                for item in items:
                    if not isinstance(item, pg.TextItem):
                        self.assertAlmostEqual(item.transform().m22(), plot.transforms[key][0])
        for key in original:
            np.testing.assert_allclose(plot.transforms[key], original[key])

    def test_axis_wheel_without_ctrl_scrolls_without_changing_series_scales(self):
        self.card.add_series(self.path, 'pressure')
        plot = self.card.series_plot
        widget = plot.plot_widget
        self.app.processEvents()
        axis = plot.selected_axis.sceneBoundingRect()
        view = plot.plot_item.vb.sceneBoundingRect()
        point = QPointF(widget.mapFromScene(QPointF((axis.left() + view.left()) / 2, view.center().y())))
        before = dict(plot.transforms)
        scrolls = []
        widget.scroll_requested.connect(scrolls.append)
        wheel = QWheelEvent(point, point, QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(widget.viewport(), wheel)
        self.assertEqual(scrolls, [120])
        self.assertEqual(plot.transforms, before)

    def test_new_series_remain_unselected_and_existing_choice_survives_insertions(self):
        plot = self.card.series_plot
        self.assertIsNone(plot.selected)
        self.assertEqual(plot.curve.opts['pen'].width(), series_line_width())
        self.card.insert_series(self.path, 'pressure', 0)
        self.assertIsNone(plot.selected)
        self.assertTrue(all(curve.opts['pen'].width() == series_line_width() for _, curve, _, _ in plot.items()))
        selected = (self.path, 'voltage')
        plot.select_series(selected)
        self.card.insert_series(self.path, 'state', 0)
        self.card.add_series(self.path, 'event')
        plot.update_visible_data()
        self.assertEqual(plot.selected, selected)
        for key, curve, _, _ in plot.items():
            self.assertEqual(curve.opts['pen'].width(), series_line_width(selected=key == selected))
        for items in plot.timeline_items.values():
            self.assertTrue(all(item.opts['pen'].width() == series_line_width() for item in items if not isinstance(item, pg.TextItem)))

    def test_batch_add_preserves_deselection_and_removing_selected_series_does_not_select_another(self):
        plot = self.card.series_plot
        self.card.add_series(self.path, 'pressure')
        plot.select_series(None)
        # Batch drops rebuild the series list in one update.
        self.card.series[0:0] = [(self.path, 'state'), (self.path, 'event')]
        self.card.on_series_changed()
        self.assertIsNone(plot.selected)
        self.assertTrue(all(curve.opts['pen'].width() == series_line_width() for _, curve, _, _ in plot.items()))
        plot.select_series((self.path, 'pressure'))
        self.card.remove_series_at(self.card.series.index((self.path, 'pressure')))
        plot.update_visible_data()
        self.assertIsNone(plot.selected)
        self.assertTrue(all(curve.opts['pen'].width() == series_line_width() for _, curve, _, _ in plot.items()))

    def test_state_event_bars_labels_and_independent_transforms(self):
        self.card.add_series(self.path, 'state')
        self.card.add_series(self.path, 'event')
        plot = self.card.series_plot
        key = (self.path, 'state')
        bars = [item for item in plot.timeline_items[key] if isinstance(item, pg.BarGraphItem)]
        self.assertEqual(len(bars), 2)
        self.assertFalse(plot.additional_series[0].curve.isVisible())
        self.assertFalse(plot.additional_series[1].curve.isVisible())
        self.assertTrue(any(isinstance(item, pg.TextItem) for item in plot.timeline_items[key]))
        gain, offset = plot.transforms[key]
        point = plot.plot_item.vb.mapViewToScene(QPointF(.5, offset))
        self.assertEqual(plot.hit_series(point), key)
        plot.select_series(key)
        original_line = plot.transforms[(self.path, 'voltage')]
        plot.move_selected(.1)
        plot.zoom_selected(2, offset + .1)
        self.assertAlmostEqual(bars[0].transform().m22(), gain)
        self.assertEqual(plot.transforms[(self.path, 'voltage')], original_line)
        self.assertEqual(plot.selected_axis.tickStrings([offset], 1, .1), ['state'])
        self.card.remove_series_at(1)
        self.assertNotIn(key, plot.timeline_items)

    def test_event_state_pixel_height_matches_across_cards_and_survives_zoom_and_resize(self):
        self.card.add_series(self.path, 'state')
        self.card.add_series(self.path, 'event')
        plot = self.card.series_plot
        event_card = EventStatePlotWidget(self.path, 'state')
        event_card.add_series(self.path, 'event')
        self.addCleanup(lambda: (event_card.dispose(), event_card.close(), event_card.deleteLater()))
        event_card.resize(800, 250)
        event_card.show()
        self.app.processEvents()

        def assert_height(items, plot_item):
            bars = [item for item in items if isinstance(item, pg.BarGraphItem)]
            self.assertTrue(bars)
            for bar in bars:
                top = bar.mapToScene(QPointF(0, float(bar.opts['y1'][0]))).y()
                bottom = bar.mapToScene(QPointF(0, float(bar.opts['y0'][0]))).y()
                self.assertAlmostEqual(abs(top - bottom), event_state_lane_height(plot_item), delta=.1)

        for height in (300, 650, 220):
            self.card.resize(800, height)
            event_card.resize(800, height)
            self.app.processEvents()
            for name in ('state', 'event'):
                key = (self.path, name)
                before = plot.transforms[key]
                plot.select_series(key)
                plot.zoom_selected(5, .5)
                self.assertEqual(plot.transforms[key], before)
                plot.select_series(None)
                plot.zoom_selected(2, .5)
                self.assertEqual(plot.transforms[key], before)
                assert_height(plot.timeline_items[key], plot.plot_item)
            assert_height(event_card._items, event_card.plot_item)
            # Ctrl+wheel on the standalone card's left axis is a no-op.
            widget = event_card.plot_widget
            view = event_card.plot_item.vb.sceneBoundingRect()
            axis = event_card.plot_item.getAxis('left').sceneBoundingRect()
            point = QPointF(widget.mapFromScene(QPointF((axis.left() + view.left()) / 2, view.center().y())))
            ranges = event_card.plot_item.vb.viewRange()
            wheel = QWheelEvent(point, point, QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                                Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(widget.viewport(), wheel)
            self.assertEqual(event_card.plot_item.vb.viewRange(), ranges)
            assert_height(event_card._items, event_card.plot_item)

    def test_numeric_axis_uses_line_axis_tick_spacing_and_precision(self):
        plot = self.card.series_plot
        plot.select_series((self.path, 'voltage'))
        axis = plot.selected_axis
        reference = pg.AxisItem('left')
        reference.enableAutoSIPrefix(False)
        for gain, offset in ((.0137, .4913), (2.718, -.3456), (.00002, .7123)):
            plot.transforms[plot.selected] = (gain, offset)
            low, high = (0 - offset) / gain, (1 - offset) / gain
            actual = axis.tickValues(0, 1, 350)
            expected = reference.tickValues(low, high, 350)
            for (spacing, values), (raw_spacing, raw_values) in zip(actual, expected):
                self.assertAlmostEqual(spacing / gain, raw_spacing)
                self.assertEqual(axis.tickStrings(values, 1, spacing),
                                 reference.tickStrings(raw_values, 1, raw_spacing))

    def test_untyped_events_remain_discrete_and_labels_identify_states(self):
        with h5py.File(self.path, 'r+') as h5:
            del h5['series/event'].attrs['event_state_kind']
        self.card.add_series(self.path, 'event')
        plot = self.card.series_plot
        key = (self.path, 'event')
        boxes = next(item for item in plot.timeline_items[key] if isinstance(item, pg.BarGraphItem))
        self.assertTrue(np.all(boxes.opts['x1'] - boxes.opts['x0'] < .1))
        with h5py.File(self.path, 'r+') as h5:
            h5['series/event'].attrs['state_labels_json'] = '{"1": "Idle", "2": "Running"}'
        plot.update_visible_data(auto_range=True)
        labels = [item.toPlainText() for item in plot.timeline_items[key] if isinstance(item, pg.TextItem)]
        self.assertIn('Idle', labels)
        self.assertIn('Running', labels)

    def test_marker_snapping_ignores_synthetic_state_endpoint(self):
        with h5py.File(self.path, 'r+') as h5:
            h5.attrs['time_end_seconds'] = 20.
        self.card.add_series(self.path, 'state')
        self.card.remove_series_at(0)
        plot = self.card.series_plot
        bars = [item for item in plot.timeline_items[(self.path, 'state')] if isinstance(item, pg.BarGraphItem)]
        self.assertEqual(max(float(max(item.opts['x1'])) for item in bars), 20.)
        self.assertEqual(plot.nearest_visible_x(19.), 3.)

    def test_mouse_selection_drag_and_axis_zoom_only_change_selected(self):
        self.card.add_series(self.path, 'pressure')
        plot = self.card.series_plot
        widget = plot.plot_widget
        view = plot.plot_item.vb
        self.app.processEvents()
        key = (self.path, 'voltage')
        gain, offset = plot.transforms[key]
        point = QPointF(widget.mapFromScene(view.mapViewToScene(QPointF(1, gain + offset))))
        before = dict(plot.transforms)
        for kind, position, buttons in (
                (QEvent.Type.MouseButtonPress, point, Qt.MouseButton.LeftButton),
                (QEvent.Type.MouseMove, point + QPointF(0, 25), Qt.MouseButton.LeftButton),
                (QEvent.Type.MouseButtonRelease, point + QPointF(0, 25), Qt.MouseButton.NoButton)):
            event = QMouseEvent(kind, position, position, Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(widget.viewport(), event)
        self.assertEqual(plot.selected, key)
        self.assertNotEqual(plot.transforms[key], before[key])
        other = (self.path, 'pressure')
        self.assertEqual(plot.transforms[other], before[other])
        # Selecting from an unselected card reveals its Y-axis label.
        self.app.processEvents()
        axis_point = QPointF(widget.mapFromScene(plot.selected_axis.sceneBoundingRect().center()))
        previous = plot.transforms[key][0]
        ranges = view.viewRange()
        wheel = QWheelEvent(axis_point, axis_point, QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(widget.viewport(), wheel)
        self.assertGreater(plot.transforms[key][0], previous)
        self.assertEqual(plot.transforms[other], before[other])
        self.assertEqual(view.viewRange(), ranges)

    def test_left_click_empty_space_deselects_trace_and_right_click_keeps_selection(self):
        from PySide6.QtTest import QTest
        self.card.add_series(self.path, 'pressure')
        plot = self.card.series_plot
        widget = plot.plot_widget
        key = (self.path, 'pressure')
        plot.select_series(key)
        widget.set_vertical_marker(.5)
        widget.set_second_marker(1.5)
        self.app.processEvents()
        point = widget.mapFromScene(plot.plot_item.vb.mapViewToScene(QPointF(1, .9)))
        self.assertIsNone(plot.hit_series(widget.mapToScene(point)))
        transforms = dict(plot.transforms)
        QTest.mouseClick(widget.viewport(), Qt.MouseButton.RightButton, pos=point)
        self.assertEqual(plot.selected, key)
        QTest.mouseClick(widget.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertIsNone(plot.selected)
        self.assertEqual(plot.transforms, transforms)
        # The right click moved the second marker and the left click the first one.
        self.assertAlmostEqual(widget.marker_value(1), 1, delta=.05)
        self.assertAlmostEqual(widget.marker_value(0), 1, delta=.05)
        self.assertEqual(plot.selected_axis.labelText, '')
        self.assertEqual(plot.selected_axis.tickStrings([.5], 1, .1), [''])
        self.assertTrue(all(curve.opts['pen'].width() == series_line_width() for _, curve, _, _ in plot.items()))
        plot.update_visible_data()
        self.card.add_series(self.path, 'state')
        self.card.reload_plot_settings()
        self.assertIsNone(plot.selected)
        self.assertTrue(all(curve.opts['pen'].width() == series_line_width() for _, curve, _, _ in plot.items()))
        gain, offset = plot.transforms[key]
        self.app.processEvents()
        point = widget.mapFromScene(plot.plot_item.vb.mapViewToScene(QPointF(2, 3000 * gain + offset)))
        self.assertEqual(plot.hit_series(widget.mapToScene(point)), key)
        QTest.mouseClick(widget.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(plot.selected, key)
        self.assertEqual(plot.additional_series[0].curve.opts['pen'].width(), series_line_width(selected=True))
        # Clicking a trace selects it and also places the first marker there.
        self.assertAlmostEqual(widget.marker_value(0), 2, delta=.05)

    def test_left_drag_on_empty_space_pans_without_deselecting_trace(self):
        plot = self.card.series_plot
        plot.select_series((self.path, 'voltage'))
        widget = plot.plot_widget
        key = plot.selected
        start = QPointF(widget.mapFromScene(plot.plot_item.vb.mapViewToScene(QPointF(1, .9))))
        self.assertIsNone(plot.hit_series(widget.mapToScene(start.toPoint())))
        before = list(plot.plot_item.vb.viewRange()[0])
        for kind, point, buttons in (
            (QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseMove, start + QPointF(40, 0), Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseButtonRelease, start + QPointF(40, 0), Qt.MouseButton.NoButton),
        ):
            event = QMouseEvent(kind, point, point, Qt.MouseButton.LeftButton, buttons,
                                Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(widget.viewport(), event)
        self.assertEqual(plot.selected, key)
        self.assertNotEqual(plot.plot_item.vb.viewRange()[0], before)

    def test_right_click_and_trace_click_place_markers_around_a_time_range(self):
        from PySide6.QtTest import QTest
        plot = self.card.series_plot
        plot.select_series((self.path, 'voltage'))
        widget = plot.plot_widget
        key = plot.selected
        self.assertIsNotNone(key)
        self.app.processEvents()
        point = lambda x, y: widget.mapFromScene(plot.plot_item.vb.mapViewToScene(QPointF(x, y)))
        QTest.mouseClick(widget.viewport(), Qt.MouseButton.RightButton, pos=point(.5, .9))
        self.assertEqual(plot.selected, key)
        self.assertIsNone(widget.selection_region)
        gain, offset = plot.transforms[key]
        on_trace = point(2, 2 * gain + offset)
        self.assertEqual(plot.hit_series(widget.mapToScene(on_trace)), key)
        transforms = dict(plot.transforms)
        QTest.mouseClick(widget.viewport(), Qt.MouseButton.LeftButton, pos=on_trace)
        self.assertEqual(plot.selected, key)
        # A click on a trace places the marker without moving the series.
        self.assertEqual(plot.transforms, transforms)
        left, right = widget.selection_region.getRegion()
        self.assertAlmostEqual(left, .5, delta=.02)
        self.assertAlmostEqual(right, 2., delta=.02)

    def test_tree_and_legend_drops_and_self_drop_rejection(self):
        workspace = PlotWorkspace(default_plot_heights={"multi": 200})
        try:
            card = workspace.add_multi_plot([(self.path, 'voltage')])
            line = workspace.add_line_plot(self.path, 'pressure')
            label = line.legend.findChild(_LegendLabel)
            mime = QMimeData()
            mime.setData(SERIES_MIME_TYPE, json.dumps({'h5_path': str(self.path), 'series_name': 'pressure'}).encode())
            event = Mock()
            event.mimeData.return_value = mime
            event.source.return_value = label
            card.dropEvent(event)
            event.acceptProposedAction.assert_called_once()
            label.move_requested.emit(0)
            self.assertEqual(card.series, [(self.path, 'voltage'), (self.path, 'pressure')])
            self.assertEqual(workspace.splitter.count(), 1)
            event.reset_mock()
            event.source.return_value = card.legend.findChild(_LegendLabel)
            card.dropEvent(event)
            event.ignore.assert_called_once()
            event.acceptProposedAction.assert_not_called()
            mime.setData(SERIES_MIME_TYPE, json.dumps({'h5_path': str(self.path), 'series_name': 'state'}).encode())
            event.source.return_value = None
            card.dropEvent(event)
            self.assertIn((self.path, 'state'), card.series)
        finally:
            workspace.close_all_plots()
            workspace.close()

    def test_folder_open_and_workspace_drop_linking(self):
        workspace = PlotWorkspace(default_plot_heights={"multi": 200})
        window = SimpleNamespace(plot_workspace=workspace, internal_logger=Mock())
        try:
            LODGaZillaWindow.plot_folder_in_multi_widget(window, self.path.parent)
            # Folder H5 files are read in a background job.
            from tests.qt_wait import qt_wait
            for _ in range(300):
                if workspace.splitter.count():
                    break
                qt_wait(10)
            card = workspace.splitter.widget(0)
            self.assertIsInstance(card, MultiPlotWidget)
            self.assertEqual({name for _, name in card.series}, {'event', 'pressure', 'state', 'voltage'})
            workspace.add_series_to_plot(card, self.path, 'voltage')
            self.assertEqual(len(card.series), 4)
            line = workspace.add_line_plot(self.path, 'voltage')
            workspace.set_markers_linked(True)
            card.set_vertical_marker(1)
            self.assertEqual(line.series_plot.plot_widget.vertical_marker.value(), 1)
        finally:
            workspace.close_all_plots()
            workspace.close()

    def test_series_menu_multi_and_group_menu_default(self):
        from tests.plot_menu_helpers import wait_for_plot_menu
        from tests.qt_wait import qt_wait
        workspace = PlotWorkspace(default_plot_heights={"multi": 200})
        navigator = Mock()
        entries = [(self.path, 'voltage'), (self.path, 'pressure')]
        navigator.selected_series_entries.return_value = entries
        navigator.tree.viewport.return_value.mapToGlobal.return_value = QPoint()
        window = SimpleNamespace(navigator=navigator, plot_workspace=workspace)
        try:
            for group in (False, True):
                menu = QMenu()
                def choose_multi(*args):
                    wait_for_plot_menu(menu)
                    if group:
                        self.assertEqual([action.text() for action in menu.actions()], ['X/Y (default)'])
                        action = menu.actions()[0]
                    else:
                        action = next(action for action in menu.actions() if action.data() == 'multi')
                    action.trigger()
                with patch('src.app.lodgazilla.QMenu', return_value=menu), patch.object(menu, 'exec', side_effect=choose_multi):
                    if group:
                        LODGaZillaWindow.show_series_group_context_menu(window, entries, QPoint())
                    else:
                        LODGaZillaWindow.show_series_context_menu(window, self.path, 'voltage', QPoint())
                if group:
                    for _ in range(300):
                        if workspace.active_plot.drop_card_type == 'xy':
                            break
                        qt_wait(10)
                    self.assertEqual(workspace.active_plot.drop_card_type, 'xy')
                else:
                    self.assertIsInstance(workspace.active_plot, MultiPlotWidget)
                self.assertEqual(workspace.active_plot.series, entries)
                menu.deleteLater()
        finally:
            workspace.close_all_plots()
            workspace.close()
