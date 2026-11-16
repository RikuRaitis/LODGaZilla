"""Legend width and shortened names, series names on hover, and the Values box's full name."""
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

from src.gui.plots import legend as legend_module  # noqa: E402
from src.gui.plots import series_hover  # noqa: E402
from src.gui.plots.legend import (LegendEntry, PlotLegendWidget, legend_width, set_legend_width_percent,  # noqa: E402
                                  set_legends_shown)
from src.gui.plots.marker_info import MarkerInfoEntry  # noqa: E402
from src.gui.plots.series_hover import SeriesNameHover, set_series_name_hover_delay  # noqa: E402
from src.gui.plots.value_boxes import ValueBoxItem, full_series_name  # noqa: E402
from tests.qt_wait import qt_wait_until  # noqa: E402
from tests.test_review_plots import _QtTestCase  # noqa: E402


def move(widget, x, y):
    """Send a pointer move without buttons to a widget."""
    from PySide6.QtWidgets import QApplication

    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(x, y), QPointF(widget.mapToGlobal(QPoint(x, y))),
                        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


class LegendWidthTests(_QtTestCase):
    def setUp(self):
        super().setUp()
        # The Plot navigator's Legends button is module-wide; another test may have left it off.
        set_legends_shown(True)

    def tearDown(self):
        set_legend_width_percent(25)
        super().tearDown()

    def test_every_legend_is_a_share_of_its_card_width_and_names_are_shortened(self):
        from src.gui.plots.line import LinePlotWidget

        long_name = "a_very_long_series_name_that_does_not_fit_in_a_quarter_of_the_card"
        path = self.make_cache(names=("a", long_name))
        card = LinePlotWidget(path, "a", settings_path=self.directory / "settings.json")
        self.widgets.append(card)
        card.add_series(path, long_name)
        card.resize(800, 300)
        card.show()
        qt_wait_until(lambda: card.legend.isVisible())
        self.app.processEvents()
        self.assertEqual(card.legend.width(), legend_width(card.width()))
        self.assertEqual(card.legend.width(), 200)
        label = card.legend.rows[1].findChildren(legend_module._LegendLabel)[-1]
        self.assertTrue(label.is_elided())
        self.assertIn("...", label.text())
        self.assertEqual(label.full_text(), f"{path.parent.name}/{long_name}")
        # The width follows the card and the setting.
        card.resize(1000, 300)
        self.app.processEvents()
        self.assertEqual(card.legend.width(), 250)
        shortened = len(label.text())
        set_legend_width_percent(40)
        card.legend.reposition()
        self.app.processEvents()
        self.assertEqual(card.legend.width(), 400)
        self.assertGreater(len(label.text()), shortened)
        # It sits at the card's right edge, with a small margin.
        right = card.legend.mapTo(card, QPoint(card.legend.width(), 0)).x()
        self.assertGreaterEqual(card.width() - right, legend_module.LEGEND_RIGHT_MARGIN)
        self.assertLessEqual(card.width() - right, 6)

    def test_legend_width_has_a_minimum_and_the_setting_is_bounded(self):
        set_legend_width_percent(500)
        self.assertEqual(legend_module.LEGEND_WIDTH_PERCENT[0], 60)
        set_legend_width_percent(25)
        self.assertEqual(legend_width(100), legend_module.LEGEND_MINIMUM_WIDTH)


class SeriesNameHoverTests(_QtTestCase):
    def setUp(self):
        super().setUp()
        set_series_name_hover_delay(100)
        self.addCleanup(set_series_name_hover_delay, 700)

    def test_name_shows_after_the_pointer_rests_and_hides_when_it_moves(self):
        widget = QWidget()
        self.widgets.append(widget)
        widget.resize(200, 100)
        widget.show()
        asked = []
        hover = SeriesNameHover(widget, lambda position, done: (asked.append(position), done("run1/Speed")))
        move(widget, 40, 30)
        self.assertEqual(asked, [])
        self.assertTrue(qt_wait_until(lambda: hover._tip is not None and hover._tip.isVisible(), 2000))
        self.assertEqual(asked, [QPoint(40, 30)])
        self.assertEqual(hover._tip.text(), "run1/Speed")
        # Moving again hides it at once.
        move(widget, 45, 30)
        self.assertFalse(hover._tip.isVisible())
        hover.hide_name()

    def test_a_late_answer_after_a_move_is_ignored(self):
        widget = QWidget()
        self.widgets.append(widget)
        widget.show()
        answers = []
        hover = SeriesNameHover(widget, lambda position, done: answers.append(done))
        move(widget, 10, 10)
        self.assertTrue(qt_wait_until(lambda: answers, 2000))
        move(widget, 12, 10)
        answers[0]("late")
        self.assertTrue(hover._tip is None or not hover._tip.isVisible())

    def test_line_card_names_the_series_under_the_pointer(self):
        from src.gui.plots.line import LinePlotWidget

        path = self.make_cache()
        card = LinePlotWidget(path, "a", settings_path=self.directory / "settings.json")
        self.widgets.append(card)
        card.add_series(path, "b")
        card.resize(800, 300)
        card.show()
        plot = card.series_plot
        qt_wait_until(lambda: plot.curve.getData()[0] is not None and len(plot.curve.getData()[0]), 5000)
        x, y = plot.curve.getData()
        index = len(x) // 2
        scene = plot.curve.mapToScene(QPointF(float(x[index]), float(y[index])))
        position = plot.plot_widget.mapFromScene(scene)
        self.assertEqual(plot.series_name_at(position), f"{path.parent.name}/a")
        self.assertIsNone(plot.series_name_at(QPoint(position.x(), position.y() - 200)))

    def test_canvas_lookup_measures_lines_and_bars_in_pixels(self):
        import pyqtgraph as pg

        canvas = pg.PlotWidget()
        self.widgets.append(canvas)
        canvas.resize(400, 300)
        canvas.show()
        curve = canvas.plot([0, 10], [0, 10])
        bars = pg.BarGraphItem(x0=[20], x1=[30], y0=[0], y1=[1])
        canvas.addItem(bars)
        canvas.setRange(xRange=(0, 30), yRange=(0, 10), padding=0)
        self.app.processEvents()
        on_line = canvas.mapFromScene(curve.mapToScene(QPointF(5, 5)))
        in_bar = canvas.mapFromScene(bars.mapToScene(QPointF(25, 0.5)))
        items = [(curve, "line"), (bars, "bars")]
        self.assertEqual(series_hover.name_at_canvas_point(canvas, on_line, items), "line")
        self.assertEqual(series_hover.name_at_canvas_point(canvas, in_bar, items), "bars")
        self.assertIsNone(series_hover.name_at_canvas_point(canvas, on_line + QPoint(0, 40), items))

    def test_legend_rows_show_their_whole_name(self):
        parent = QWidget()
        self.widgets.append(parent)
        legend = PlotLegendWidget(parent)
        legend.set_entries([LegendEntry(0, "run1/Speed", "#ff0000", (Path("run1/data.h5"), "Speed"))])
        label = legend.rows[0].findChildren(legend_module._LegendLabel)[-1]
        self.assertEqual(label.hover_name(), "run1/Speed")
        empty = LegendEntry(1, "empty", "#ff0000", None, replaceable=True)
        legend.set_entries([empty])
        self.assertIsNone(legend.rows[0].findChildren(legend_module._LegendLabel)[-1].hover_name())


class FullNameTests(unittest.TestCase):
    def test_values_box_details_name_the_whole_series(self):
        entry = MarkerInfoEntry("Speed", "#ff0000", "3 m/s", ("Date/time: now",))
        item = ValueBoxItem((Path("run1/data.h5"), "Speed"), entry, 1.0)
        self.assertEqual(full_series_name(item), "run1/Speed")
        named = ValueBoxItem("peak", MarkerInfoEntry("Strongest frequency", "#fff", "4 Hz", (), full_name="run1/Acc"), 1.0)
        self.assertEqual(full_series_name(named), "run1/Acc")
        self.assertEqual(full_series_name(ValueBoxItem("lane", entry, 1.0)), "")


class FrequencyLegendTests(_QtTestCase):
    def setUp(self):
        super().setUp()
        set_legends_shown(True)

    def test_frequency_card_names_its_series_in_a_legend_not_a_title(self):
        from src.gui.plots.frequency import FrequencyPlotWidget

        path = self.make_cache()
        card = FrequencyPlotWidget(path, "a")
        self.widgets.append(card)
        card.resize(800, 300)
        card.show()
        self.app.processEvents()
        self.assertEqual(card.plot_item.titleLabel.text, "")
        self.assertTrue(card.legend.isVisible())
        self.assertEqual([entry.text for entry in card.legend.entries], [f"{path.parent.name}/a"])
        self.assertEqual(card.legend.entries[0].color, card.peak_line_color)
        # A drop on the Legend replaces the one series.
        card.insert_series(path, "b", 0)
        self.assertEqual(card.series, [(path, "b")])
        self.assertEqual([entry.text for entry in card.legend.entries], [f"{path.parent.name}/b"])


class XYZAxesTests(unittest.TestCase):
    def test_axes_meet_at_the_origin_held_inside_the_data_box(self):
        from src.gui.plots.xyz import AXES_CORNER, AXES_ORIGIN, axes_origin

        np.testing.assert_allclose(axes_origin([-2, 1, -1], [2, 3, 3], AXES_ORIGIN), [0, -1, -0.5])
        np.testing.assert_allclose(axes_origin([-2, 1, -1], [2, 3, 3], AXES_CORNER), [-1, -1, -1])


if __name__ == "__main__":
    unittest.main()
