"""Regression tests for plot-core review findings (colors, loaders, time axes, drops)."""

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import h5py
import numpy as np
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QScrollArea, QSplitter, QWidget

from src.data.xyz import AxisSource
from src.gui.plots.base import assign_series_colors, first_unused_color
from src.gui.plots.data_drop import DataDropController, _GuardedDropMetadataTask
from src.gui.plots.event_state import EVENT_COLORS, EventStatePlotWidget
from src.gui.plots.interactive import InteractivePlotWidget
from src.gui.plots.legend_groups import GROUP_COLORS, LegendGroups
from src.gui.plots.line import LinePlotWidget
from src.gui.plots.marker_info import marker_entries_from_samples
from src.gui.plots.navigator import PlotNavigatorEntry, PlotNavigatorWidget
from src.gui.plots.series import (
    DEFAULT_SERIES_COLORS,
    MarkerDataLoader,
    SelectionStatisticsLoadCoordinator,
    SeriesPlotWidget,
    TimeAxisMetadata,
    _display_time_axis_metadata,
    load_marker_sample,
    load_plot_series,
)

LOG_START = 1_800_000_000.0


def _write_series(group, times, values, **attrs):
    group.attrs.update(attrs)
    group.create_dataset(attrs.get("time_dataset", "time_unix"), data=np.asarray(times, dtype=float))
    group.create_dataset("value", data=np.asarray(values, dtype=float))


class _QtTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            if hasattr(widget, "dispose"):
                widget.dispose()
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def wait_for(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(predicate())

    def make_cache(self, name="data.h5", *, unix=True, names=("a", "b", "c"), count=100, log_start=None):
        path = self.directory / name
        with h5py.File(path, "w") as cache:
            if log_start is not None:
                cache.attrs["log_start_unix_seconds"] = log_start
            for index, series_name in enumerate(names):
                group = cache.create_group(f"series/{series_name}")
                times = (LOG_START if unix else 0.0) + np.arange(count, dtype=float)
                if unix:
                    _write_series(group, times, np.arange(count) + index, navigator_name=series_name)
                else:
                    _write_series(group, times * 1000.0, np.arange(count) % 2, time_dataset="time_ms",
                                  time_unit="ms", navigator_name=series_name, unit="bool")
        return path


class SeriesColorTests(_QtTestCase):
    def test_color_helpers_prefer_unused_palette_colors(self):
        self.assertEqual(first_unused_color(GROUP_COLORS, [GROUP_COLORS[0].upper()]), GROUP_COLORS[1])
        self.assertEqual(first_unused_color(("#1", "#2"), ["#1", "#2"], 3), "#2")
        colors = assign_series_colors(["c", "a", "b"], {"a": "#ff0000", "b": DEFAULT_SERIES_COLORS[0]},
                                      DEFAULT_SERIES_COLORS)
        self.assertEqual(colors, [DEFAULT_SERIES_COLORS[1], "#ff0000", DEFAULT_SERIES_COLORS[0]])

    def test_line_card_insert_and_removal_keep_original_series_colors(self):
        path = self.make_cache()
        card = LinePlotWidget(path, "a")
        self.widgets.append(card)
        card.add_series(path, "b")
        card.series_plot.set_series_color(0, "#ff0000")
        b_color = card.series_plot.series_colors()[1]

        card.insert_series(path, "c", 0)
        colors = card.series_plot.series_colors()
        self.assertEqual(colors[1:], ["#ff0000", b_color])
        self.assertNotIn(colors[0], {"#ff0000", b_color})
        self.assertEqual([entry.color for entry in card.legend.entries], colors)

        card.remove_series_at(0)
        self.assertEqual(card.series_plot.series_colors(), ["#ff0000", b_color])
        card.remove_series_at(0)
        self.assertEqual(card.series_plot.series_colors(), [b_color])

    def test_event_state_insert_and_removal_keep_original_series_colors(self):
        path = self.make_cache(unix=False, log_start=LOG_START)
        card = EventStatePlotWidget(path, "a")
        self.widgets.append(card)
        card.add_series(path, "b")
        card.set_series_color(0, "#ff0000")
        b_color = card._colors[1]

        card.insert_series(path, "c", 0)
        self.assertEqual(card._colors[1:], ["#ff0000", b_color])
        self.assertIn(card._colors[0], EVENT_COLORS)
        self.assertNotIn(card._colors[0], {"#ff0000", b_color})

        card.remove_series_at(1)
        self.assertEqual(card._colors[1], b_color)
        self.assertEqual(len(card._colors), 2)

    def test_new_legend_group_uses_an_unused_color(self):
        owner = QObject()
        groups = LegendGroups(owner, ("X", "Y"))
        source = AxisSource(Path("data.h5"), "a", "value")
        groups.insert((source, source))
        groups.insert((source, source), 0)
        self.assertEqual([group.color for group in groups.groups], [GROUP_COLORS[1], GROUP_COLORS[0]])
        groups.insert((source, source), 1, "#123456")
        self.assertEqual(groups.groups[1].color, "#123456")


class TimeAndMarkerTests(_QtTestCase):
    def test_elapsed_axis_counts_from_log_start_for_unix_time(self):
        metadata = TimeAxisMetadata(raw_origin=LOG_START + 10, seconds_per_unit=1.0,
                                    log_start_unix_seconds=LOG_START, is_unix_time=True)
        display = _display_time_axis_metadata(metadata)
        self.assertEqual(display.raw_origin, LOG_START)
        canvas = InteractivePlotWidget()
        self.widgets.append(canvas)
        canvas.configure_time_axis(**display.__dict__)
        canvas.time_axis.set_representation(canvas.time_axis.LOG_ELAPSED)
        self.assertEqual(canvas.time_axis.tickStrings([LOG_START + 300], 1, 60), ["5min"])

        no_log_start = TimeAxisMetadata(raw_origin=LOG_START * 1000, seconds_per_unit=0.001,
                                        log_start_unix_seconds=None, is_unix_time=True)
        self.assertEqual(_display_time_axis_metadata(no_log_start).raw_origin, LOG_START)

    def test_marker_canvases_repaint_fully_so_old_markers_are_erased(self):
        from PySide6.QtWidgets import QGraphicsView

        from src.gui.plots.frequency import FrequencyCanvas
        from src.gui.plots.multi import MultiInteractivePlot
        from src.gui.plots.xy import XYInteractivePlotWidget

        for canvas_type in (InteractivePlotWidget, FrequencyCanvas, XYInteractivePlotWidget):
            canvas = canvas_type()
            self.widgets.append(canvas)
            self.assertEqual(canvas.viewportUpdateMode(), QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.assertTrue(issubclass(MultiInteractivePlot, InteractivePlotWidget))

    def test_unix_marker_log_start_uses_the_common_log_start(self):
        path = self.directory / "data.h5"
        with h5py.File(path, "w") as cache:
            cache.attrs["log_start_unix_seconds"] = LOG_START
            _write_series(cache.create_group("series/late"), [LOG_START + 5, LOG_START + 6], [1, 2])
        sample = load_marker_sample(path, "late", LOG_START + 6, canonical_x=True)
        self.assertAlmostEqual(sample.log_elapsed_ms, 6000.0, places=3)

    def test_marker_colors_follow_the_requested_series_index(self):
        path = self.directory / "data.h5"
        with h5py.File(path, "w") as cache:
            _write_series(cache.create_group("series/early"), [LOG_START, LOG_START + 1], [1, 2])
            _write_series(cache.create_group("series/later"), [LOG_START + 5, LOG_START + 9], [3, 4])
        owner = QWidget()
        self.widgets.append(owner)
        samples = []
        loader = MarkerDataLoader(1, LOG_START + 6, [(path, "early"), (path, "later")], owner)
        loader.loaded.connect(lambda _id, _x, result: samples.extend(result))
        loader.run()
        self.assertEqual([(sample.series_name, sample.series_index) for sample in samples], [("later", 1)])
        entries = marker_entries_from_samples(samples, ["#blue", "#orange"])
        self.assertEqual(entries[0].color, "#orange")

    def test_raw_view_includes_one_sample_beyond_each_edge(self):
        path = self.directory / "data.h5"
        with h5py.File(path, "w") as cache:
            _write_series(cache.create_group("series/a"), np.arange(10.0), np.arange(10.0))
        data = load_plot_series(path, "a", x_range=(4.25, 4.75))
        np.testing.assert_array_equal(data.x, [4.0, 5.0])


class SelectionTests(_QtTestCase):
    def mouse(self, canvas, kind, point, button=Qt.MouseButton.RightButton):
        buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else button
        QApplication.sendEvent(canvas.viewport(), QMouseEvent(
            kind, point, point, button, buttons, Qt.KeyboardModifier.NoModifier))

    def test_marker_drag_reports_one_selection_on_release_and_right_drag_places_nothing(self):
        canvas = InteractivePlotWidget()
        self.widgets.append(canvas)
        canvas.resize(800, 400)
        canvas.show()
        canvas.getViewBox().setRange(xRange=(0, 100), yRange=(0, 1), padding=0)
        self.app.processEvents()
        selections = []
        canvas.selection_changed.connect(lambda left, right: selections.append((left, right)))
        view = canvas.getViewBox()
        points = [QPointF(canvas.mapFromScene(view.mapViewToScene(QPointF(x, 0.5)))) for x in (10, 30, 50, 70)]
        # A right drag is not a click, so it places no second marker.
        self.mouse(canvas, QEvent.Type.MouseButtonPress, points[0])
        self.mouse(canvas, QEvent.Type.MouseMove, points[1])
        self.mouse(canvas, QEvent.Type.MouseButtonRelease, points[1])
        self.assertIsNone(canvas.second_marker)
        canvas.set_vertical_marker(10)
        canvas.set_second_marker(30)
        selections.clear()
        left = Qt.MouseButton.LeftButton
        self.mouse(canvas, QEvent.Type.MouseButtonPress, points[1], left)
        for point in points[2:]:
            self.mouse(canvas, QEvent.Type.MouseMove, point, left)
        self.mouse(canvas, QEvent.Type.MouseButtonRelease, points[-1], left)
        self.assertEqual(len(selections), 1)
        self.assertAlmostEqual(selections[0][1], 70, delta=1)
        self.assertAlmostEqual(canvas.marker_value(1), 70, delta=1)

    def test_statistics_coordinator_runs_one_loader_and_keeps_only_the_latest_request(self):
        class FakeLoader(QObject):
            loaded = Signal(int, float, float, object)
            finished = Signal()
            created = []

            def __init__(self, request_id, left, right, entries, parent):
                super().__init__(parent)
                self.left, self.right = left, right
                self.__class__.created.append(self)

            def start(self):
                pass

            def requestInterruption(self):
                pass

        parent = QWidget()
        self.widgets.append(parent)
        coordinator = SelectionStatisticsLoadCoordinator(parent)
        with mock.patch("src.gui.plots.series.SelectionStatisticsLoader", FakeLoader):
            for right in (2.0, 3.0, 4.0):
                coordinator.request(1.0, right, [])
            self.assertEqual(len(FakeLoader.created), 1)
            FakeLoader.created[0].finished.emit()
            self.assertEqual([loader.right for loader in FakeLoader.created], [2.0, 4.0])

    def test_statistics_are_limited_to_the_data_time_range(self):
        path = self.make_cache(names=("a",))
        widget = SeriesPlotWidget()
        self.widgets.append(widget)
        widget.show_series_entries([(path, "a")])
        widget.set_data_time_range((LOG_START + 20, LOG_START + 40))
        with mock.patch.object(widget._selection_loads, "request", return_value=99) as request:
            widget.show_selection_statistics(LOG_START + 10, LOG_START + 30)
            widget.show_selection_statistics(LOG_START + 50, LOG_START + 60)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[:2], (LOG_START + 20, LOG_START + 30))
        # The displayed duration still describes the whole selection.
        self.assertAlmostEqual(widget.value_boxes.selection_duration, 10.0)


class VisibleWindowTests(_QtTestCase):
    def test_line_pan_loads_in_background_and_survives_a_missing_cache(self):
        path = self.make_cache(names=("a", "b"), count=50_000)
        widget = SeriesPlotWidget()
        self.widgets.append(widget)
        widget.show_series_entries([(path, "a"), (path, "b")])
        statuses = []
        widget.status_changed.connect(statuses.append)
        widget.plot_item.getViewBox().setXRange(LOG_START + 100, LOG_START + 110, padding=0)
        with mock.patch.object(widget, "update_visible_data", side_effect=AssertionError("GUI-thread load")):
            widget._view_update_timer.timeout.emit()
            self.wait_for(lambda: widget._current_data is not None and len(widget._current_data.x) <= 20)
        self.assertEqual(widget._current_data.x[0], LOG_START + 99)
        self.assertIsNotNone(widget.additional_series[0].data)

        path.unlink()
        widget.plot_item.getViewBox().setXRange(LOG_START + 200, LOG_START + 210, padding=0)
        widget._view_update_timer.timeout.emit()
        self.wait_for(lambda: any("Could not load" in status for status in statuses))
        self.assertEqual(widget._current_data.x[0], LOG_START + 99)

    def gated_loader(self, gate, threads, names=None):
        def load(path, name, **options):
            threads.append(threading.current_thread())
            if names is None or name in names:
                gate.wait(5)
            return load_plot_series(path, name, **options)
        return load

    def background_widget(self):
        widget = SeriesPlotWidget()
        self.widgets.append(widget)
        widget.background_loads = True
        return widget

    def test_series_assignment_loads_in_background_and_auto_ranges(self):
        path = self.make_cache(names=("a", "b"), count=1000)
        widget = self.background_widget()
        gate, threads = threading.Event(), []
        with mock.patch.object(widget, "load_data", side_effect=self.gated_loader(gate, threads)):
            self.assertEqual(widget.show_series_entries([(path, "a"), (path, "b")]), [])
            self.assertIsNone(widget._current_data)
            gate.set()
            self.wait_for(lambda: widget._current_data is not None and widget.additional_series[0].data is not None)
        self.assertTrue(threads)
        self.assertTrue(all(thread is not threading.main_thread() for thread in threads))
        x_range = widget.plot_item.getViewBox().viewRange()[0]
        self.assertLessEqual(x_range[0], LOG_START + 1)
        self.assertGreaterEqual(x_range[1], LOG_START + 998)

    def test_stale_series_load_does_not_replace_a_newer_assignment(self):
        path = self.make_cache(names=("a", "b"), count=1000)
        widget = self.background_widget()
        gate, threads = threading.Event(), []
        with mock.patch.object(widget, "load_data", side_effect=self.gated_loader(gate, threads, names={"a"})):
            widget.show_series_entries([(path, "a")])
            widget.show_series_entries([(path, "b")])
            gate.set()
            self.wait_for(lambda: widget._current_data is not None)
            self.wait_for(lambda: widget._visible_loads._active_loader is None)
        self.assertEqual(widget.series_name, "b")
        self.assertEqual(widget._current_data.y[0], 1.0)

    def test_data_range_change_loads_in_background_and_keeps_the_view(self):
        path = self.make_cache(names=("a",), count=1000)
        widget = SeriesPlotWidget()
        self.widgets.append(widget)
        widget.show_series_entries([(path, "a")])
        widget.plot_item.getViewBox().setXRange(LOG_START + 5, LOG_START + 50, padding=0)
        widget._view_update_timer.stop()
        widget.background_loads = True
        gate, threads = threading.Event(), []
        with mock.patch.object(widget, "load_data", side_effect=self.gated_loader(gate, threads)):
            widget.set_data_time_range((LOG_START + 10, LOG_START + 20))
            self.assertGreater(len(widget._current_data.x), 20)
            gate.set()
            self.wait_for(lambda: len(widget._current_data.x) <= 20)
        self.assertLessEqual(abs(widget._current_data.x[0] - (LOG_START + 10)), 1)
        self.assertTrue(all(thread is not threading.main_thread() for thread in threads))
        self.assertEqual(widget.plot_item.getViewBox().viewRange()[0], [LOG_START + 5, LOG_START + 50])

    def test_view_set_while_loading_is_kept(self):
        path = self.make_cache(names=("a",), count=1000)
        widget = self.background_widget()
        gate, threads = threading.Event(), []
        with mock.patch.object(widget, "load_data", side_effect=self.gated_loader(gate, threads)):
            widget.show_series_entries([(path, "a")])
            # For example a workspace restoring its saved zoom.
            widget.plot_item.getViewBox().setXRange(LOG_START + 100, LOG_START + 200, padding=0)
            gate.set()
            self.wait_for(lambda: widget._current_data is not None)
            self.wait_for(lambda: widget._visible_loads._active_loader is None
                          and not widget._view_update_timer.isActive())
        self.assertEqual(widget.plot_item.getViewBox().viewRange()[0], [LOG_START + 100, LOG_START + 200])

    def test_background_load_failure_is_shown_on_the_line_card(self):
        path = self.make_cache(names=("a",), count=100)
        card = LinePlotWidget(path, "a")
        self.widgets.append(card)
        card.series_plot.background_loads = True
        with mock.patch.object(card.series_plot, "load_data", side_effect=OSError("disk gone")):
            card.on_series_changed()
            self.wait_for(lambda: not card.error_label.isHidden())
        self.assertIn("disk gone", card.error_label.text())

    def test_event_state_window_reload_runs_in_background(self):
        path = self.make_cache(unix=False, log_start=LOG_START, names=("a",), count=1000)
        card = EventStatePlotWidget(path, "a")
        self.widgets.append(card)
        card.resize(800, 300)
        card.show()
        self.app.processEvents()
        card.plot_item.vb.setXRange(LOG_START + 100, LOG_START + 110, padding=0)
        with mock.patch.object(card, "on_series_changed", side_effect=AssertionError("GUI-thread load")):
            card._reload_visible_window()
            self.assertIsNone(card._visible_time_range)
        self.wait_for(lambda: card._visible_time_range is not None)
        times = np.asarray(card._timeline_data[0]["time"])
        self.assertLess(len(times), 20)
        lower, upper = card.plot_item.vb.viewRange()[0]
        self.assertAlmostEqual(lower, LOG_START + 100, places=3)
        self.assertAlmostEqual(upper, LOG_START + 110, places=3)


    def event_card(self, names=("a", "b")):
        path = self.make_cache(unix=False, log_start=LOG_START, names=names, count=1000)
        card = EventStatePlotWidget(path, "a")
        self.widgets.append(card)
        card.resize(800, 300)
        card.show()
        self.app.processEvents()
        card.background_loads = True
        return path, card

    def gated_event_loader(self, gate, threads):
        from src.gui.plots import event_state

        original = event_state.load_event_state_series

        def load(path, name, **options):
            threads.append(threading.current_thread())
            gate.wait(5)
            return original(path, name, **options)
        return mock.patch("src.gui.plots.event_state.load_event_state_series", side_effect=load)

    def test_event_state_series_assignment_loads_in_background(self):
        path, card = self.event_card()
        gate, threads = threading.Event(), []
        with self.gated_event_loader(gate, threads):
            card.add_series(path, "b")
            self.assertEqual(len(card._timeline_data), 1)
            gate.set()
            self.wait_for(lambda: len(card._timeline_data) == 2)
        self.assertTrue(threads)
        self.assertTrue(all(thread is not threading.main_thread() for thread in threads))

    def test_event_state_data_range_change_loads_in_background_and_keeps_the_view(self):
        _path, card = self.event_card(names=("a",))
        card.plot_item.vb.setXRange(LOG_START + 100, LOG_START + 200, padding=0)
        self.wait_for(lambda: card._visible_request is None and not card._visible_reload_timer.isActive())
        gate, threads = threading.Event(), []
        with self.gated_event_loader(gate, threads):
            card.set_data_time_range((LOG_START + 120, LOG_START + 180))
            gate.set()
            self.wait_for(lambda: card._explicit_load is None and card._visible_loads._active_loader is None)
        self.assertTrue(threads)
        self.assertTrue(all(thread is not threading.main_thread() for thread in threads))
        times = np.asarray(card._timeline_data[0]["time"])
        self.assertGreaterEqual(times[0], LOG_START + 119)
        self.assertLessEqual(times[-1], LOG_START + 181)
        lower, upper = card.plot_item.vb.viewRange()[0]
        self.assertAlmostEqual(lower, LOG_START + 100, places=3)
        self.assertAlmostEqual(upper, LOG_START + 200, places=3)

    def test_event_state_view_set_while_loading_is_kept(self):
        _path, card = self.event_card(names=("a",))
        gate, threads = threading.Event(), []
        with self.gated_event_loader(gate, threads):
            card.on_series_changed()
            # For example a workspace restoring its saved zoom.
            card.plot_item.vb.setXRange(LOG_START + 300, LOG_START + 400, padding=0)
            gate.set()
            self.wait_for(lambda: card._explicit_load is None and card._visible_loads._active_loader is None
                          and not card._visible_reload_timer.isActive())
        lower, upper = card.plot_item.vb.viewRange()[0]
        self.assertAlmostEqual(lower, LOG_START + 300, places=3)
        self.assertAlmostEqual(upper, LOG_START + 400, places=3)


class NavigatorReorderTests(_QtTestCase):
    def release(self, navigator, own, drop):
        navigator._drag_entry = navigator.entries[own]
        # The mode a press reaches once it is dragged up or down.
        navigator._drag_mode = "reorder"
        navigator._drop_index = drop
        requests = []
        navigator.reorder_requested.connect(lambda *args: requests.append(args))
        point = QPointF(5, 5)
        navigator.canvas.mouseReleaseEvent(QMouseEvent(
            QEvent.Type.MouseButtonRelease, point, point, Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        navigator.reorder_requested.disconnect()
        return requests

    def test_drop_index_matches_the_drawn_insertion_line(self):
        navigator = PlotNavigatorWidget()
        self.widgets.append(navigator)
        plots = ["p0", "p1", "p2"]
        navigator.entries = [PlotNavigatorEntry(plot, 0.0, 1.0, plot, "line") for plot in plots]
        self.assertEqual(self.release(navigator, 0, 1), [])
        self.assertEqual(self.release(navigator, 0, 0), [])
        self.assertEqual(self.release(navigator, 0, 2), [("p0", "p1", True)])
        self.assertEqual(self.release(navigator, 0, 3), [("p0", "p2", True)])
        self.assertEqual(self.release(navigator, 2, 0), [("p2", "p0", False)])
        self.assertEqual(self.release(navigator, 2, 3), [])


class NavigatorViewBoxTests(_QtTestCase):
    def mouse(self, navigator, kind, x, y=10.0):
        point = QPointF(x, y)
        buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
        event = QMouseEvent(kind, point, point, Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove
                            else Qt.MouseButton.NoButton, buttons, Qt.KeyboardModifier.NoModifier)
        handler = {QEvent.Type.MouseButtonPress: navigator.canvas.mousePressEvent,
                   QEvent.Type.MouseMove: navigator.canvas.mouseMoveEvent,
                   QEvent.Type.MouseButtonRelease: navigator.canvas.mouseReleaseEvent}[kind]
        handler(event)

    def test_dragging_view_box_edges_zooms_without_touching_markers_or_cards(self):
        navigator = PlotNavigatorWidget()
        self.widgets.append(navigator)
        navigator.resize(1052, 120)
        navigator.set_plot_area_margins(26, 26)  # 1000 px for 0..100 s
        navigator.set_entries([PlotNavigatorEntry("p0", 0.0, 100.0, "p0", "line"),
                               PlotNavigatorEntry("p1", 0.0, 100.0, "p1", "line")])
        navigator.show()
        self.app.processEvents()
        self.assertEqual(navigator.canvas.width(), 1052)
        navigator.set_view_range((20.0, 60.0))
        zooms, markers, activated = [], [], []
        navigator.view_range_changed.connect(lambda start, stop: zooms.append((start, stop)))
        navigator.marker_placed.connect(markers.append)
        navigator.plot_activated.connect(activated.append)
        left_edge = navigator.x_for_time(20.0)

        self.mouse(navigator, QEvent.Type.MouseMove, left_edge + 3)
        self.assertEqual(navigator.canvas.cursor().shape(), Qt.CursorShape.SizeHorCursor)
        self.mouse(navigator, QEvent.Type.MouseButtonPress, left_edge + 3)
        self.mouse(navigator, QEvent.Type.MouseMove, navigator.x_for_time(10.0))
        self.mouse(navigator, QEvent.Type.MouseButtonRelease, navigator.x_for_time(10.0))
        self.assertAlmostEqual(zooms[-1][0], 10.0, places=6)
        self.assertEqual(zooms[-1][1], 60.0)

        right_edge = navigator.x_for_time(60.0)
        self.mouse(navigator, QEvent.Type.MouseButtonPress, right_edge - 2)
        # Dragging past the other edge keeps a positive range.
        self.mouse(navigator, QEvent.Type.MouseMove, navigator.x_for_time(0.0))
        self.mouse(navigator, QEvent.Type.MouseButtonRelease, navigator.x_for_time(0.0))
        self.assertLess(zooms[-1][0], zooms[-1][1])
        self.assertEqual((markers, activated), ([], []))

        # Away from the edges the canvas still places markers; unlinked views show no box.
        self.mouse(navigator, QEvent.Type.MouseButtonPress, navigator.x_for_time(80.0), y=40.0)
        self.assertEqual(len(markers), 1)
        navigator.set_view_range(None)
        self.mouse(navigator, QEvent.Type.MouseMove, navigator.x_for_time(zooms[-1][0]))
        self.assertNotEqual(navigator.canvas.cursor().shape(), Qt.CursorShape.SizeHorCursor)


    def test_dragging_inside_the_view_box_pans_and_a_click_still_places_a_marker(self):
        navigator = PlotNavigatorWidget()
        self.widgets.append(navigator)
        navigator.resize(1052, 120)
        navigator.set_plot_area_margins(26, 26)  # 10 px per second for 0..100 s
        navigator.set_entries([PlotNavigatorEntry("p0", 0.0, 100.0, "p0", "line"),
                               PlotNavigatorEntry("p1", 50.0, 80.0, "p1", "line", 0.0, 100.0)])
        navigator.show()
        self.app.processEvents()
        navigator.set_view_range((20.0, 60.0))
        zooms, markers, activated = [], [], []
        navigator.view_range_changed.connect(lambda start, stop: zooms.append((start, stop)))
        navigator.marker_placed.connect(markers.append)
        navigator.plot_activated.connect(activated.append)
        strip = 4.0  # The handle strip above the first card bar.

        self.mouse(navigator, QEvent.Type.MouseMove, navigator.x_for_time(40.0), y=strip)
        self.assertEqual(navigator.canvas.cursor().shape(), Qt.CursorShape.OpenHandCursor)
        self.mouse(navigator, QEvent.Type.MouseButtonPress, navigator.x_for_time(40.0), y=strip)
        self.mouse(navigator, QEvent.Type.MouseMove, navigator.x_for_time(55.0), y=strip)
        self.mouse(navigator, QEvent.Type.MouseButtonRelease, navigator.x_for_time(55.0), y=strip)
        self.assertAlmostEqual(zooms[-1][0], 35.0, places=6)
        self.assertAlmostEqual(zooms[-1][1], 75.0, places=6)
        self.assertEqual((markers, activated), ([], []))

        # Panning stops at the end of the overview and keeps the zoom width.
        self.mouse(navigator, QEvent.Type.MouseButtonPress, navigator.x_for_time(50.0), y=strip)
        self.mouse(navigator, QEvent.Type.MouseMove, navigator.x_for_time(99.0), y=strip)
        self.mouse(navigator, QEvent.Type.MouseButtonRelease, navigator.x_for_time(99.0), y=strip)
        self.assertAlmostEqual(zooms[-1][1], 100.0, places=6)
        self.assertAlmostEqual(zooms[-1][1] - zooms[-1][0], 40.0, places=6)

        # A press released in place is an ordinary click that places the first marker.
        count = len(zooms)
        self.mouse(navigator, QEvent.Type.MouseButtonPress, navigator.x_for_time(62.0), y=strip)
        self.mouse(navigator, QEvent.Type.MouseButtonRelease, navigator.x_for_time(62.0), y=strip)
        self.assertEqual(len(zooms), count)
        self.assertAlmostEqual(markers[-1], 62.0, places=6)

        # Pressing a card bar inside the box still selects that card.
        self.mouse(navigator, QEvent.Type.MouseButtonPress, navigator.x_for_time(70.0), y=12.0)
        self.mouse(navigator, QEvent.Type.MouseButtonRelease, navigator.x_for_time(70.0), y=12.0)
        self.assertEqual(activated, ["p0"])

    def test_active_card_bar_takes_precedence_over_the_view_box_edge(self):
        navigator = PlotNavigatorWidget()
        self.widgets.append(navigator)
        navigator.resize(1052, 120)
        navigator.set_plot_area_margins(26, 26)
        entry = PlotNavigatorEntry("p1", 20.0, 80.0, "p1", "line", 0.0, 100.0)
        navigator.set_entries([PlotNavigatorEntry("p0", 0.0, 100.0, "p0", "line"), entry])
        navigator.show()
        self.app.processEvents()
        navigator.set_view_range((20.0, 60.0))  # Its left edge is on p1's left bar edge.
        zooms, ranges = [], []
        navigator.view_range_changed.connect(lambda start, stop: zooms.append((start, stop)))
        navigator.range_changed.connect(lambda plot, start, stop: ranges.append((plot, start, stop)))
        x, y = navigator.x_for_time(20.0), navigator.canvas._stroke_rect(1, entry).center().y()

        def drag():
            self.mouse(navigator, QEvent.Type.MouseButtonPress, x, y)
            self.mouse(navigator, QEvent.Type.MouseMove, navigator.x_for_time(30.0), y)
            self.mouse(navigator, QEvent.Type.MouseButtonRelease, navigator.x_for_time(30.0), y)

        # No active card: the view box edge moves.
        drag()
        self.assertAlmostEqual(zooms[-1][0], 30.0, places=6)
        self.assertEqual(ranges, [])
        # With p1 active, its bar's left edge moves instead.
        navigator.set_view_range((20.0, 60.0))
        navigator.set_active_plot("p1")
        count = len(zooms)
        drag()
        self.assertEqual(len(zooms), count)
        self.assertEqual(ranges[-1][0], "p1")
        self.assertAlmostEqual(ranges[-1][1], 30.0, places=6)
        # Elsewhere the view box edge still wins over other cards' bars.
        y0 = navigator.canvas._stroke_rect(0, navigator.entries[0]).center().y()
        self.mouse(navigator, QEvent.Type.MouseButtonPress, navigator.x_for_time(60.0), y0)
        self.assertEqual(navigator._drag_mode, "view_right")
        self.mouse(navigator, QEvent.Type.MouseButtonRelease, navigator.x_for_time(60.0), y0)
        # Clearing the active card restores the view box's precedence.
        navigator.set_active_plot(None)
        self.mouse(navigator, QEvent.Type.MouseButtonPress, x, y)
        self.assertEqual(navigator._drag_mode, "view_left")
        self.mouse(navigator, QEvent.Type.MouseButtonRelease, x, y)


class LinkedViewRangeTests(_QtTestCase):
    def test_workspace_follows_and_sets_the_linked_view_range(self):
        from src.app.lodgazilla import PlotWorkspace

        path = self.make_cache()
        workspace = PlotWorkspace(settings_path=self.directory / "settings.json")
        workspace.resize(900, 600)
        workspace.show()

        def cleanup():
            from PySide6.QtCore import QThreadPool

            workspace.close_all_plots()
            workspace.close()
            workspace.deleteLater()
            # Let the closed cards' background reads finish before later tests queue theirs.
            QThreadPool.globalInstance().waitForDone(5000)
            self.app.processEvents()

        self.addCleanup(cleanup)
        ranges = []
        workspace.linked_view_range_changed.connect(ranges.append)
        first = workspace.add_line_plot(path, "a")
        second = workspace.add_line_plot(path, "b")
        self.app.processEvents()

        workspace.set_linked_view_range(LOG_START + 10, LOG_START + 30)
        for card in (first, second):
            lower, upper = card.plot_view_box().viewRange()[0]
            self.assertAlmostEqual(lower, LOG_START + 10, places=3)
            self.assertAlmostEqual(upper, LOG_START + 30, places=3)
        self.assertAlmostEqual(ranges[-1][0], LOG_START + 10, places=3)

        second.plot_view_box().setXRange(LOG_START + 40, LOG_START + 50, padding=0)
        self.assertAlmostEqual(ranges[-1][1], LOG_START + 50, places=3)

        workspace.set_x_axes_linked(False)
        self.assertIsNone(ranges[-1])
        workspace.set_x_axes_linked(True)
        workspace.remove_plot(first)
        self.assertIs(workspace._linked_view_box, second.plot_view_box())
        self.assertIsNotNone(ranges[-1])
        workspace.remove_plot(second)
        self.assertIsNone(ranges[-1])


class DropInspectionTests(_QtTestCase):
    def test_failed_inspection_completes_and_allows_the_next_drag(self):
        completed = []
        task = _GuardedDropMetadataTask("key", (Path("data.h5"), "a"))
        task.signals.completed.connect(lambda key, options: completed.append((key, options)))
        with mock.patch("src.jobs.plot_drop.inspect_sources", side_effect=RuntimeError("h5py")):
            task.run()
        self.assertEqual(completed, [("key", ())])

        workspace = QScrollArea()
        workspace.splitter = QSplitter()
        self.widgets.append(workspace)
        controller = DataDropController(workspace)
        controller._key = "drag"
        controller.payload = {"series": (Path("data.h5"), "a")}
        with mock.patch("src.jobs.plot_drop.inspect_sources", side_effect=RuntimeError("h5py")):
            controller._start_metadata()
            self.wait_for(lambda: controller._job is None)
        self.assertEqual(controller.options, ())
        controller._start_metadata()
        self.assertIsNotNone(controller._job)
        self.wait_for(lambda: controller._job is None)


if __name__ == "__main__":
    unittest.main()
