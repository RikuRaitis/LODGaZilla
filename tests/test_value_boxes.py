"""Per-series values boxes at both markers, with selection statistics when expanded."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import time
import unittest
from pathlib import Path

import h5py
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.gui.plots.line import LinePlotWidget
from src.gui.plots.sample_interval import SampleIntervalPlotWidget
from src.gui.plots.value_boxes import VALUE_BOX_OPACITY, set_value_box_opacity


class ValueBoxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "data.h5"
        with h5py.File(self.path, "w") as cache:
            for name, values in (("low", np.zeros(100)), ("high", np.full(100, 10.0))):
                group = cache.create_group("series/" + name)
                group.attrs.update(time_dataset="time", time_unit="s", navigator_name=name, unit="V")
                group.create_dataset("time", data=np.arange(100, dtype=float))
                group.create_dataset("value", data=values)

    def card(self, card_type=LinePlotWidget):
        card = card_type(self.path, "low")
        card.add_series(self.path, "high")
        self.addCleanup(lambda: (card.dispose(), card.close(), card.deleteLater(), self.app.processEvents()))
        card.resize(900, 400)
        card.show()
        self.app.processEvents()
        return card

    def wait_for(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(predicate())

    def test_one_box_per_series_at_each_marker_beside_its_line(self):
        card = self.card()
        plot = card.series_plot
        plot.plot_item.getViewBox().setRange(xRange=(0, 99), yRange=(-5, 15), padding=0)
        layer = plot.value_boxes
        card.show_marker_data(30.0, 0)
        card.show_marker_data(60.0, 1)
        self.wait_for(lambda: len(layer.visible_boxes(0)) == 2 and len(layer.visible_boxes(1)) == 2)
        canvas = plot.plot_widget
        view = plot.plot_item.getViewBox()
        colors = dict(zip(plot._series_entries, plot.series_colors()))
        for marker, x_value in ((0, 30.0), (1, 60.0)):
            boxes = layer.visible_boxes(marker)
            marker_x = canvas.mapFromScene(view.mapViewToScene(pg.Point(x_value, 0))).x()
            for box in boxes:
                # One line, in the series' color, right of the marker.
                self.assertLess(box.height(), 30)
                self.assertIn(colors[box.item.key], box.toHtml())
                self.assertGreater(box.x(), marker_x)
                line_y = canvas.mapFromScene(view.mapViewToScene(pg.Point(0, box.item.y_value))).y()
                self.assertLessEqual(box.y(), line_y + 1)
                self.assertGreaterEqual(box.y() + box.height(), line_y - 1)
            first, second = sorted(boxes, key=lambda box: box.y())
            self.assertLessEqual(first.y() + first.height(), second.y())
        # One line with only the value and unit, without a marker-colored edge.
        box = next(box for box in layer.visible_boxes(0) if box.item.key == (self.path, "high"))
        self.assertEqual(box.toPlainText(), "10 V")
        self.assertNotIn("border-left", box.styleSheet())

    def test_hover_shows_details_and_a_click_pins_them_until_a_click_outside(self):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QEnterEvent, QMouseEvent
        card = self.card()
        layer = card.series_plot.value_boxes
        card.show_marker_data(30.0, 0)
        self.wait_for(lambda: len(layer.visible_boxes(0)) == 2)
        box = next(box for box in layer.visible_boxes(0) if box.item.key == (self.path, "high"))
        details = layer.details
        point = QPointF(4, box.height() / 2)
        QApplication.sendEvent(box, QEnterEvent(point, point, box.mapToGlobal(point)))
        self.assertFalse(details.isHidden())
        self.assertIn("high: 10 V", details.toPlainText())
        self.assertIn("Device time", details.toPlainText())
        self.assertFalse(details.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse)
        QApplication.sendEvent(box, QEvent(QEvent.Type.Leave))
        self.assertTrue(details.isHidden())

        def press(widget, button):
            QApplication.sendEvent(widget, QMouseEvent(QEvent.Type.MouseButtonPress, point, point, button, button,
                                                       Qt.KeyboardModifier.NoModifier))

        # A right click does nothing; a left click pins the details with selectable
        # text. Neither places a marker.
        press(box.viewport(), Qt.MouseButton.RightButton)
        self.assertTrue(details.isHidden())
        press(box.viewport(), Qt.MouseButton.LeftButton)
        self.assertFalse(details.isHidden())
        self.assertTrue(details.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse)
        self.assertEqual(card.marker_values(), (None, None))
        QApplication.sendEvent(box, QEvent(QEvent.Type.Leave))
        press(details.viewport(), Qt.MouseButton.LeftButton)
        self.assertFalse(details.isHidden())
        # Any click outside closes it.
        press(card.series_plot.plot_widget.viewport(), Qt.MouseButton.LeftButton)
        self.assertTrue(details.isHidden())

    def test_details_follow_marker_moves_and_show_selection_statistics(self):
        card = self.card()
        plot = card.series_plot
        layer = plot.value_boxes
        card.show_marker_data(30.0, 0)
        self.wait_for(lambda: len(layer.visible_boxes(0)) == 2)
        key = (self.path, "high")
        box = next(box for box in layer.visible_boxes(0) if box.item.key == key)
        layer.details.show_for(box, pinned=True)
        self.addCleanup(layer.details.close_details)
        card.set_vertical_marker(30.0, 0)
        card.set_vertical_marker(50.0, 1)
        self.wait_for(lambda: key in layer.statistics)
        for line in ("Selection Δt: 20 s", "Min: 10 V", "Max: 10 V", "Average: 10 V", "Median: 10 V"):
            self.assertIn(line, box.details_text())
            self.assertIn(line, layer.details.toPlainText())
        card.clear_vertical_marker(1)
        self.assertNotIn("Min:", box.details_text())

    def test_background_opacity_follows_the_setting(self):
        self.addCleanup(set_value_box_opacity, VALUE_BOX_OPACITY[0])
        card = self.card()
        layer = card.series_plot.value_boxes
        card.show_marker_data(30.0, 0)
        self.wait_for(lambda: layer.visible_boxes(0))
        set_value_box_opacity(50)
        card.apply_marker_style()
        self.assertIn(", 128);", layer.visible_boxes(0)[0].styleSheet())
        set_value_box_opacity(100)
        card.apply_marker_style()
        self.assertIn(", 255);", layer.visible_boxes(0)[0].styleSheet())

    def test_sample_interval_boxes_show_only_selection_statistics(self):
        card = self.card(SampleIntervalPlotWidget)
        layer = card.series_plot.value_boxes
        card.show_marker_data(30.0, 0)
        self.app.processEvents()
        self.assertFalse(layer.visible_boxes())
        card.set_vertical_marker(30.0, 0)
        card.set_vertical_marker(50.0, 1)
        card.show_marker_data(50.0, 1)
        self.wait_for(lambda: len(layer.visible_boxes(0)) == 2 and len(layer.visible_boxes(1)) == 2)
        self.assertIn("avg 1 s", layer.visible_boxes(0)[0].toPlainText())


class SeriesPaletteTests(unittest.TestCase):
    def test_default_colors_stay_clear_of_the_selection_and_marker_hues(self):
        from PySide6.QtGui import QColor
        from src.gui.plots.base import SERIES_COLORS
        from src.gui.plots.markers import MARKER_COLORS, SELECTION_BRUSH
        reserved = [QColor(*SELECTION_BRUSH[:3]).hue()] + [QColor(color).hue() for color in MARKER_COLORS]
        for color in SERIES_COLORS:
            hue = QColor(color).hue()
            if hue < 0:
                continue  # Grey has no hue to confuse.
            with self.subTest(color=color):
                self.assertTrue(all(min(abs(hue - other), 360 - abs(hue - other)) >= 20 for other in reserved))


if __name__ == "__main__":
    unittest.main()
