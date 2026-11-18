import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import h5py
import numpy as np
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from src.gui.plots.event_state import EventStatePlotWidget
from src.gui.plots.frequency import FrequencyPlotWidget, _LIVE_WORKERS
from src.gui.plots.interactive import InteractivePlotWidget
from src.gui.plots.line import LinePlotWidget
from src.gui.plots.marker_info import MarkerInfoBox, MarkerInfoEntry
from src.gui.plots.multi import MultiPlotWidget
from src.gui.plots.selection_info import SelectionDurationInfo
from src.gui.plots.series import SelectionStatisticsLoader


from tests.markers_shown import with_markers_shown  # noqa: E402


@with_markers_shown
class SelectionDurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "signal.h5"
        with h5py.File(self.path, "w") as cache:
            group = cache.create_group("series/signal")
            group.attrs.update(time_dataset="time", time_unit="ms", time_reference="device_relative",
                               navigator_name="signal", unit="mV")
            group.create_dataset("time", data=np.arange(8192) / 4096 * 1000)
            group.create_dataset("value", data=np.sin(2 * np.pi * 512 * np.arange(8192) / 4096))
        self.widgets = []

    def wait_for(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(predicate())

    def tearDown(self):
        for widget in self.widgets:
            self.wait_for(lambda: not any(t.isRunning() for t in widget.findChildren(SelectionStatisticsLoader)))
            if hasattr(widget, "dispose"):
                widget.dispose()
            widget.close()
            widget.deleteLater()
        self.wait_for(lambda: not _LIVE_WORKERS)
        self.app.processEvents()

    def mouse(self, canvas, kind, point, button=Qt.MouseButton.RightButton):
        buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else button
        event = QMouseEvent(kind, point, point, button, buttons, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(canvas.viewport(), event)

    def test_duration_units_and_summary_survive_marker_updates(self):
        popup = MarkerInfoBox()
        self.widgets.append(popup)
        for seconds, expected in ((2.5, "2.5 s"), (0.02, "20 ms"), (0.000025, "25 µs"),
                                  (0.000000025, "25 ns"), (0, "0 s"), (-0.02, "20 ms")):
            popup.set_selection_duration(seconds)
            popup.set_entries([MarkerInfoEntry("Signal", "#123456", "42 mV", ("Extra detail",))])
            self.assertIn("Selection Δt: " + expected, popup.toPlainText())
            popup._toggle_entry(SimpleNamespace(toString=lambda: "toggle:0"))
            self.assertIn("Selection Δt: " + expected, popup.toPlainText())
        popup.set_selection_duration(None)
        self.assertNotIn("Δt", popup.toPlainText())
        self.assertIn("42 mV", popup.toPlainText())

    def test_duration_uses_native_units_or_canonical_seconds_independently_of_axis_display(self):
        canvas = InteractivePlotWidget()
        self.widgets.append(canvas)
        marker = MarkerInfoBox(canvas)
        metadata = SimpleNamespace(is_unix_time=False, log_start_unix_seconds=None, seconds_per_unit=0.001)
        info = SelectionDurationInfo(canvas, marker, lambda: metadata)
        # The workspace can assign a different shared axis display conversion.
        canvas.time_axis.seconds_per_unit = 123
        for scale, is_unix, start, left, right in (
            (0.001, False, None, 10, 30),
            (0.000001, False, None, 10000, 30000),
            (1, False, None, 0.03, 0.01),
            (0.001, False, 1800000000, 1800000000, 1800000000.02),
            (0.000001, True, None, 1800000000, 1800000000.02),
        ):
            metadata.seconds_per_unit, metadata.is_unix_time, metadata.log_start_unix_seconds = scale, is_unix, start
            info.show(left, right)
            self.assertAlmostEqual(info.duration, 0.02, places=6)
            self.assertAlmostEqual(marker._selection_duration, 0.02, places=6)
        info.clear()
        self.assertIsNone(marker._selection_duration)
        self.assertIsNone(info.duration)

    def test_two_markers_select_the_area_between_on_all_time_card_types(self):
        for card_type in (LinePlotWidget, MultiPlotWidget, EventStatePlotWidget, FrequencyPlotWidget):
            with self.subTest(card=card_type.__name__):
                card = card_type(self.path, "signal")
                self.widgets.append(card)
                card.resize(1000, 500)
                card.show()
                owner = getattr(card, "series_plot", card)
                canvas = owner.plot_widget
                if isinstance(card, FrequencyPlotWidget):
                    self.wait_for(lambda: card.data is not None and card._worker is None)
                self.app.processEvents()
                view = canvas.getViewBox()
                middle_y = sum(view.viewRange()[1]) / 2
                point = lambda x: QPointF(canvas.mapFromScene(view.mapViewToScene(QPointF(x, middle_y))))
                # Multi-series: click away from the trace so the click places a marker.
                if isinstance(card, MultiPlotWidget):
                    point = lambda x: QPointF(canvas.mapFromScene(view.mapViewToScene(QPointF(x, 0.98))))
                second = []
                card.second_marker_placed.connect(second.append)
                self.click(canvas, point(1100), Qt.MouseButton.LeftButton)
                self.assertIsNone(canvas.selection_region)
                self.click(canvas, point(500), Qt.MouseButton.RightButton)
                self.assertAlmostEqual(second[-1], 500, delta=5)
                self.assertEqual(card.marker_values()[1], second[-1])
                self.assertIsNotNone(canvas.selection_region)
                # There is no separate Selection box; Δt is in the values boxes.
                self.assertFalse([box for box in canvas.findChildren(MarkerInfoBox) if box.isVisible()])
                self.assertAlmostEqual(owner.value_boxes.selection_duration, 0.6, delta=0.01)
                # Dragging a marker moves that edge of the selection.
                self.mouse(canvas, QEvent.Type.MouseButtonPress, point(1100), Qt.MouseButton.LeftButton)
                self.mouse(canvas, QEvent.Type.MouseMove, point(1000), Qt.MouseButton.LeftButton)
                self.mouse(canvas, QEvent.Type.MouseMove, point(800), Qt.MouseButton.LeftButton)
                self.mouse(canvas, QEvent.Type.MouseButtonRelease, point(800), Qt.MouseButton.LeftButton)
                self.assertAlmostEqual(canvas.marker_value(0), 800, delta=5)
                self.assertAlmostEqual(owner.value_boxes.selection_duration, 0.3, delta=0.01)
                if isinstance(card, LinePlotWidget):
                    self.wait_for(lambda: owner.value_boxes.statistics)
                card.show_marker_data(750)
                self.wait_for(lambda: owner.value_boxes.visible_boxes(0))
                box = owner.value_boxes.visible_boxes(0)[0]
                self.assertIn("Selection Δt:", box.details_text())
                if isinstance(card, LinePlotWidget):
                    # The former selection statistics are in the values box details.
                    self.assertIn("Median:", box.details_text())
                # A right double-click removes the second marker and so the selection.
                self.double_click(canvas, point(500), Qt.MouseButton.RightButton)
                self.assertIsNone(canvas.second_marker)
                self.assertIsNone(canvas.selection_region)
                self.assertIsNone(owner.value_boxes.selection_duration)
                self.assertNotIn("Δt", box.details_text())
                self.double_click(canvas, point(800), Qt.MouseButton.LeftButton)
                self.assertIsNone(canvas.vertical_marker)

    def test_frequency_settings_menu_is_on_the_header_button(self):
        card = FrequencyPlotWidget(self.path, "signal")
        self.widgets.append(card)
        card.resize(1000, 500)
        card.show()
        self.app.processEvents()
        self.assertIn(card.fft_settings_button, card.header_controls)
        self.assertLess(card.fft_settings_button.x(), card.close_button.x())
        positions = []
        card.show_fft_menu = positions.append
        card.fft_settings_button.click()
        self.assertEqual(len(positions), 1)

    def click(self, canvas, point, button):
        self.mouse(canvas, QEvent.Type.MouseButtonPress, point, button)
        self.mouse(canvas, QEvent.Type.MouseButtonRelease, point, button)

    def double_click(self, canvas, point, button):
        self.click(canvas, point, button)
        self.mouse(canvas, QEvent.Type.MouseButtonDblClick, point, button)
        self.mouse(canvas, QEvent.Type.MouseButtonRelease, point, button)

    def test_empty_selection_and_timing_delta_keep_duration_without_waiting_for_statistics(self):
        card = LinePlotWidget(self.path, "signal")
        self.widgets.append(card)
        card.resize(1000, 500)
        card.show()
        owner = card.series_plot
        owner.show_timing_delta(self.path, "signal")
        owner.show_selection_statistics(2500, 3000)
        self.assertAlmostEqual(owner.value_boxes.selection_duration, 0.5)
        self.wait_for(lambda: not owner.findChildren(SelectionStatisticsLoader))
        self.assertAlmostEqual(owner.value_boxes.selection_duration, 0.5)
        owner.hide_selection_statistics()
        # A delayed result from the cleared selection must not reopen it.
        owner._show_loaded_selection_statistics(owner._selection_request_id - 1, 2500, 3000, [])
        self.assertIsNone(owner.value_boxes.selection_duration)


if __name__ == "__main__":
    unittest.main()
