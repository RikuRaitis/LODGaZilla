"""Multi-series lane layout: lane height, stacking, gap filling and the setting."""

import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np
from PySide6.QtWidgets import QApplication

from src.app.settings import PLOT_SECTION, load_plot_settings, sanitize_plot_settings, save_settings_section
from src.gui.plots.multi import MultiPlotWidget, next_lane_center
from src.gui.settings_widget import SettingsEditorWidget
from src.data.h5_series import series_value_range
from src.gui.plots.diagnostic_areas import SAMPLE_LOSS  # noqa: E402
from unittest.mock import patch


class LanePlacementTests(unittest.TestCase):
    def test_stacks_one_lane_apart_then_fills_gaps_lowest_first(self):
        self.assertAlmostEqual(next_lane_center([], 0.1), 0.05)
        self.assertAlmostEqual(next_lane_center([0.05, 0.15], 0.1), 0.25)
        full = [0.05 + 0.1 * index for index in range(10)]
        # A full card places new series halfway between lanes, then a quarter apart.
        self.assertAlmostEqual(next_lane_center(full, 0.1), 0.10)
        halves = full + [0.10 + 0.1 * index for index in range(9)]
        self.assertAlmostEqual(next_lane_center(halves, 0.1), 0.075)

    def test_a_series_moved_to_the_top_leaves_lower_lanes_for_new_series(self):
        # The highest series was dragged near the top, so the next one takes the lowest free lane.
        self.assertAlmostEqual(next_lane_center([0.05, 0.92], 0.1), 0.15)


class MultiSeriesLaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'data.h5'
        self.settings = Path(self.temp.name) / 'settings.json'
        with h5py.File(self.path, 'w') as h5:
            for name, values, unit, kind in (
                    ('voltage', [0., 1., 2., 1.], 'V', ''),
                    ('pressure', [1000., 2000., 3000., 2000.], 'Pa', ''),
                    ('state', [0, 1, 1, 0], 'bool', 'state')):
                group = h5.create_group('series/' + name)
                group.attrs.update(time_dataset='time_s', time_unit='s', unit=unit,
                                   event_state_kind=kind, navigator_name=name)
                group.create_dataset('time_s', data=[0., 1., 2., 3.])
                group.create_dataset('value', data=values)
            # The top LOD level reports the whole series, wider than its first samples.
            lod = h5['series/pressure'].create_group('lod')
            lod.attrs['top_level'] = 'level_001'
            level = lod.create_group('level_001')
            level.create_dataset('value_min', data=[500.])
            level.create_dataset('value_max', data=[4500.])
        # The lane layout below assumes 10% lanes with sample loss areas shown, whatever the defaults are.
        save_settings_section(self.settings, PLOT_SECTION,
                              {'multi_series_lane_height_percent': 10, 'show_sample_loss_areas': True})

    def tearDown(self):
        self.temp.cleanup()

    def card(self):
        card = MultiPlotWidget(self.path, 'voltage', settings_path=self.settings)
        self.addCleanup(lambda: (card.dispose(), card.close(), card.deleteLater(), self.app.processEvents()))
        card.resize(800, 400)
        card.show()
        self.app.processEvents()
        return card

    def lane(self, plot, name, low, high):
        gain, offset = plot.transforms[(self.path, name)]
        return low * gain + offset, high * gain + offset

    def test_series_fill_one_lane_each_without_overlap(self):
        card = self.card()
        card.add_series(self.path, 'pressure')
        card.add_series(self.path, 'state')
        plot = card.series_plot
        np.testing.assert_allclose(self.lane(plot, 'voltage', 0, 2), (0.0, 0.1), atol=1e-12)
        # The whole-series LOD range, not the first visible samples, sets the scale.
        np.testing.assert_allclose(self.lane(plot, 'pressure', 500, 4500), (0.1, 0.2), atol=1e-12)
        gain, offset = plot.transforms[(self.path, 'state')]
        self.assertAlmostEqual(gain, 1.0)
        self.assertAlmostEqual(offset, 0.25)
        np.testing.assert_allclose(plot.plot_item.getViewBox().viewRange()[1], (0, 1), atol=0.02)

    def test_state_marked_only_by_event_state_kind_is_a_labelled_bar_lane(self):
        # As in the demo data: no bool/index unit, only event_state_kind and labels.
        with h5py.File(self.path, 'a') as h5:
            group = h5.create_group('series/phase')
            group.attrs.update(time_dataset='time_s', time_unit='s', unit='', event_state_kind='state',
                               navigator_name='phase', state_labels_json='{"0": "Warm-up", "1": "Flat"}')
            group.create_dataset('time_s', data=[0., 2.])
            group.create_dataset('value', data=[0., 1.])
        from src.gui.plots.event_state import is_event_state_series
        from src.jobs.plot_drop import series_card_types
        self.assertTrue(is_event_state_series(self.path, 'phase'))
        self.assertFalse(is_event_state_series(self.path, 'voltage'))
        # Card selection uses the same rule: no Frequency card for it.
        self.assertNotIn('frequency', series_card_types(self.path, 'phase'))
        card = self.card()
        card.add_series(self.path, 'phase')
        plot = card.series_plot
        key = (self.path, 'phase')
        self.assertTrue(plot.event_kinds[key])
        self.assertTrue(plot.timeline_items.get(key))
        curve = next(curve for item_key, curve, _markers, _data in plot.items() if item_key == key)
        self.assertFalse(curve.isVisible())

    def test_labelled_events_stay_separate_named_and_readable(self):
        import pyqtgraph as pg
        from PySide6.QtGui import QFontMetricsF
        from src.gui.plots.event_state import render_event_state_lane
        widget = pg.PlotWidget()
        self.addCleanup(widget.deleteLater)
        widget.resize(500, 120)
        widget.show()
        self.app.processEvents()
        plot = widget.getPlotItem()
        # Footstrikes three times a second for a minute, as in the demo data.
        times = np.arange(0, 60, 1 / 3)
        values = np.tile([1., 2.], len(times))[:len(times)]
        labels = {"1": "Left foot", "2": "Right foot"}

        def render(kind):
            items = render_event_state_lane(plot, 0, {"time": times, "value": values, "labels": labels,
                                                       "kind": kind, "unit": ""}, "#888888")
            bars = next(item for item in items if isinstance(item, pg.BarGraphItem))
            texts = [item for item in items if isinstance(item, pg.TextItem)]
            for item in items:
                plot.removeItem(item)
            return np.asarray(bars.opts["x0"]), np.asarray(bars.opts["x1"]), texts

        starts, ends, texts = render("event")
        # Events are separate boxes despite their label map, named from it.
        self.assertTrue(np.all(ends[:-1] < starts[1:]))
        self.assertTrue(texts)
        self.assertEqual({text.textItem.toPlainText() for text in texts}, {"Left foot", "Right foot"})
        # No more labels than fit side by side at their own width.
        width = QFontMetricsF(plot.vb.getViewWidget().font()).horizontalAdvance("Right foot") + 12
        self.assertLessEqual(len(texts), plot.vb.width() / width + 1)
        # Without a kind, a label map still means states: each lasts until the next.
        starts, ends, _texts = render("")
        np.testing.assert_allclose(ends[:-1], starts[1:])


    def test_dense_lanes_draw_a_bounded_number_of_bars(self):
        import pyqtgraph as pg
        from src.gui.plots.event_state import render_event_state_lane
        widget = pg.PlotWidget()
        self.addCleanup(widget.deleteLater)
        widget.resize(500, 120)
        widget.show()
        self.app.processEvents()
        plot = widget.getPlotItem()

        def bars(times, values, kind):
            items = render_event_state_lane(plot, 0, {"time": times, "value": values, "labels": {},
                                                       "kind": kind, "unit": ""}, "#888888")
            for item in items:
                plot.removeItem(item)
            boxes = [item for item in items if isinstance(item, pg.BarGraphItem)]
            return (np.concatenate([np.asarray(box.opts["x0"]) for box in boxes]),
                    np.concatenate([np.asarray(box.opts["x1"]) for box in boxes]))

        # Repeated samples of a state are one bar, lasting until the state changes.
        starts, ends = bars(np.arange(6.0), np.asarray([0., 0., 1., 1., 1., 0.]), "state")
        np.testing.assert_allclose(sorted(zip(starts, ends)), [(0., 2.), (2., 5.), (5., 5.)])
        # 200,000 events in a lane a few hundred pixels wide: at most two bars per pixel.
        times = np.arange(200_000) * 0.001
        starts, ends = bars(times, np.arange(200_000) % 7, "event")
        self.assertLessEqual(len(starts), 2 * plot.vb.width())
        self.assertAlmostEqual(starts.min(), 0.0)
        self.assertAlmostEqual(ends.max(), times[-1])

    def test_sample_loss_areas_cover_each_series_lane(self):
        with h5py.File(self.path, 'a') as h5:
            for name in ('voltage', 'pressure'):
                losses = h5[f'series/{name}'].create_group('sample_loss_list')
                losses.create_dataset('sample_loss_start_s', data=[1.0])
                losses.create_dataset('sample_loss_stop_s', data=[2.0])
        card = self.card()
        card.add_series(self.path, 'pressure')
        plot = card.series_plot
        plot.plot_item.getViewBox().setYRange(0, 1, padding=0)
        self.app.processEvents()
        voltage, pressure = sorted(plot.diagnostic_items(SAMPLE_LOSS), key=lambda item: item.series_index)
        np.testing.assert_allclose(voltage.value_range(), (0.0, 0.1), atol=1e-6)
        lane = pressure.value_range()
        self.assertGreater(min(lane), 0.1 - 1e-6)
        # Moving the series moves its diagnostic area with it.
        plot.select_series((self.path, 'voltage'))
        plot.move_selected(0.5)
        np.testing.assert_allclose(voltage.value_range(), (0.5, 0.6), atol=1e-6)

    def test_lane_height_setting_applies_to_new_series(self):
        save_settings_section(self.settings, PLOT_SECTION, {'multi_series_lane_height_percent': 20})
        card = self.card()
        card.add_series(self.path, 'pressure')
        plot = card.series_plot
        np.testing.assert_allclose(self.lane(plot, 'voltage', 0, 2), (0.0, 0.2), atol=1e-12)
        np.testing.assert_allclose(self.lane(plot, 'pressure', 500, 4500), (0.2, 0.4), atol=1e-12)

    def test_setting_is_bounded_and_saved_from_the_settings_dialog(self):
        self.assertEqual(sanitize_plot_settings({})['multi_series_lane_height_percent'], 20)
        self.assertEqual(load_plot_settings(self.settings)['multi_series_lane_height_percent'], 10)
        self.assertEqual(sanitize_plot_settings({'multi_series_lane_height_percent': 90})
                         ['multi_series_lane_height_percent'], 50)
        editor = SettingsEditorWidget(self.settings)
        self.addCleanup(editor.deleteLater)
        editor.multi_series_lane_height_spin.setValue(15)
        editor.save()
        self.assertEqual(load_plot_settings(self.settings)['multi_series_lane_height_percent'], 15)

    def arranged_plot(self):
        card = self.card()
        card.add_series(self.path, 'pressure')
        card.add_series(self.path, 'state')
        plot = card.series_plot
        for name, center in (('voltage', .2), ('pressure', .5), ('state', .8)):
            key = (self.path, name)
            plot.select_series(key)
            gain, offset = plot.transforms[key]
            plot.move_selected(center - (plot.value_midpoints[key] * gain + offset))
        plot.select_series(None)
        return card, plot

    def assert_lanes_inside(self, plot):
        for name, low, high in (('voltage', 0, 2), ('pressure', 500, 4500)):
            bottom, top = self.lane(plot, name, low, high)
            self.assertGreaterEqual(bottom, .05 - 1e-12)
            self.assertLessEqual(top, .95 + 1e-12)

    def test_collective_zoom_compresses_all_lanes_allows_overlap_and_restores_manual_positions(self):
        _, plot = self.arranged_plot()
        original = dict(plot.transforms)
        x_range = list(plot.plot_item.vb.viewRange()[0])
        plot.zoom_selected(6, .9)
        self.assert_lanes_inside(plot)
        centers = plot.lane_centers()
        self.assertEqual(centers, sorted(centers))
        self.assertLess(centers[-1] - centers[0], .6)
        self.assertGreater(self.lane(plot, 'voltage', 0, 2)[1], self.lane(plot, 'pressure', 500, 4500)[0])
        for key in original:
            factor = 1 if plot.event_kinds.get(key) else 6
            self.assertAlmostEqual(plot.transforms[key][0], original[key][0] * factor)
        self.assertEqual(plot.plot_item.vb.viewRange()[0], x_range)
        self.assertIsNone(plot.selected)
        plot.zoom_selected(1 / 6, .1)
        for key in original:
            np.testing.assert_allclose(plot.transforms[key], original[key], atol=1e-12)

    def test_collective_zoom_caps_at_tallest_series_and_zoom_out_responds_immediately(self):
        _, plot = self.arranged_plot()
        plot.zoom_selected(100, .1)
        self.assert_lanes_inside(plot)
        bottom, top = self.lane(plot, 'pressure', 500, 4500)
        self.assertAlmostEqual(top - bottom, .9)
        at_limit = dict(plot.transforms)
        for _ in range(5):
            plot.zoom_selected(1 / .85, .9)
        for key in at_limit:
            np.testing.assert_allclose(plot.transforms[key], at_limit[key], atol=1e-12)
        plot.zoom_selected(.85, .9)
        self.assert_lanes_inside(plot)
        for key in at_limit:
            factor = 1 if plot.event_kinds.get(key) else .85
            self.assertAlmostEqual(plot.transforms[key][0], at_limit[key][0] * factor)
        self.assertGreater(plot.lane_centers()[-1] - plot.lane_centers()[0], 0)

    def test_horizontal_reload_keeps_full_range_and_collective_spacing_memory(self):
        _, plot = self.arranged_plot()
        original = dict(plot.transforms)
        plot.zoom_selected(6, .5)
        compressed = dict(plot.transforms)
        # No file scans belong in the wheel handler; full bounds were loaded already.
        with patch('src.gui.plots.multi._whole_series_range', side_effect=AssertionError('range reloaded')):
            plot.plot_item.setXRange(1, 2, padding=0)
            plot.update_visible_data()
            self.assertEqual(plot.transforms, compressed)
            self.assertEqual(plot.value_ranges[(self.path, 'pressure')], (500, 4500))
            plot.zoom_selected(1 / 6, .5)
        for key in original:
            np.testing.assert_allclose(plot.transforms[key], original[key], atol=1e-12)

    def test_manual_move_after_compression_becomes_new_preferred_position(self):
        _, plot = self.arranged_plot()
        plot.zoom_selected(2, .5)
        key = (self.path, 'voltage')
        plot.select_series(key)
        plot.move_selected(.1)
        plot.select_series(None)
        manual = dict(plot.transforms)
        plot.zoom_selected(3, .5)
        plot.zoom_selected(1 / 3, .5)
        for key in manual:
            np.testing.assert_allclose(plot.transforms[key], manual[key], atol=1e-12)

    def test_raw_range_fallback_reads_all_chunks_and_ignores_nonfinite_values(self):
        with h5py.File(self.path, 'r+') as h5:
            group = h5['series/voltage']
            del group['value']
            values = np.ones(70_000)
            values[0], values[1], values[2] = np.nan, np.inf, -np.inf
            values[-2:] = [-100, 200]
            group.create_dataset('value', data=values)
            self.assertIsNone(series_value_range(h5, 'voltage'))
            self.assertEqual(series_value_range(h5, 'voltage', scan_raw=True), (-100, 200))
            group['value'][:] = np.nan
            self.assertIsNone(series_value_range(h5, 'voltage', scan_raw=True))

    def test_partial_view_defers_raw_range_scan_to_background_reader(self):
        from concurrent.futures import ThreadPoolExecutor
        from src.gui.plots.multi import _whole_series_range
        plot = self.card().series_plot
        key = (self.path, 'voltage')
        plot.value_ranges.pop(key)
        with patch('src.gui.plots.multi._whole_series_range', wraps=_whole_series_range) as bounds:
            payload = plot._load_entry(self.path, 'voltage', maximum_points=1000, x_range=(1, 2))
            self.assertFalse(payload[-1])
            bounds.assert_called_once_with(self.path, 'voltage', scan_raw=False)
            bounds.reset_mock()
            with ThreadPoolExecutor(max_workers=1) as worker:
                payload = worker.submit(plot._load_entry, self.path, 'voltage',
                                        maximum_points=1000, x_range=(1, 2)).result(timeout=10)
            bounds.assert_called_once_with(self.path, 'voltage', scan_raw=True)
            self.assertTrue(payload[-1])
            plot._accept_entry_payload(self.path, 'voltage', payload)
            self.assertEqual(plot.value_ranges[key], (0, 2))

    def test_constant_and_missing_values_do_not_break_collective_zoom(self):
        with h5py.File(self.path, 'r+') as h5:
            h5['series/voltage/value'][:] = 4
            h5['series/pressure/value'][:] = np.nan
            del h5['series/pressure/lod']
        card = self.card()
        card.add_series(self.path, 'pressure')
        plot = card.series_plot
        plot.zoom_selected(100, .5)
        self.assertTrue(all(np.all(np.isfinite(transform)) for transform in plot.transforms.values()))
        bottom, top = self.lane(plot, 'voltage', 4, 4)
        self.assertGreaterEqual(bottom, .05 - 1e-12)
        self.assertLessEqual(top, .95 + 1e-12)


if __name__ == '__main__':
    unittest.main()
