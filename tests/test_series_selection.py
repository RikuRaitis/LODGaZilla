"""Selecting a series with a click, the shared series widths, and the Legend highlight."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF  # noqa: E402

from src.app.settings import sanitize_plot_settings  # noqa: E402
from src.gui.plots.event_state import EventStatePlotWidget  # noqa: E402
from src.gui.plots.line import LinePlotWidget  # noqa: E402
from src.gui.plots.series_style import series_line_width, set_series_line_widths  # noqa: E402
from tests.test_review_plots import _QtTestCase  # noqa: E402


def widget_point(plot_widget, view_box, x, y):
    """The plot widget position of a data point."""
    return QPointF(plot_widget.mapFromScene(view_box.mapViewToScene(QPointF(x, y))))


class SettingsTests(unittest.TestCase):
    def test_widths_default_and_the_old_multi_series_setting_carries_over(self):
        self.assertEqual((sanitize_plot_settings({})["series_line_width"],
                          sanitize_plot_settings({})["selected_series_line_width"]), (2, 5))
        self.assertEqual(sanitize_plot_settings({"multi_selected_line_width": 7})["selected_series_line_width"], 7)
        self.assertEqual(sanitize_plot_settings({"multi_selected_line_width": 7, "selected_series_line_width": 4})
                         ["selected_series_line_width"], 4)
        self.assertNotIn("multi_selected_line_width", sanitize_plot_settings({}))


class LineSelectionTests(_QtTestCase):
    def make_line(self):
        path = self.make_cache()
        card = LinePlotWidget(path, "a")
        self.widgets.append(card)
        card.add_series(path, "b")
        card.resize(700, 400)
        card.show()
        self.wait_for(lambda: card.series_plot.additional_series and card.series_plot.additional_series[0].data)
        return path, card

    def test_a_click_selects_the_line_under_it_and_elsewhere_none(self):
        path, card = self.make_line()
        plot = card.series_plot
        data = plot.additional_series[0].data
        on_b = widget_point(plot.plot_widget, plot.plot_item.vb, data.x[50], data.y[50])
        plot.plot_widget.plot_left_clicked.emit(on_b)
        self.assertEqual(card.selected_series, (path, "b"))
        # Drawn wider and in front; the other at the series width.
        self.assertEqual(plot.additional_series[0].curve.opts["pen"].width(), series_line_width(selected=True))
        self.assertEqual(plot.curve.opts["pen"].width(), series_line_width())
        # Its Legend row is highlighted.
        self.assertEqual([card.legend.rows.index(row) for row in card.legend.selected_rows()], [1])
        # A click far from every line selects nothing.
        corner = QPointF(5, 5)
        plot.plot_widget.plot_left_clicked.emit(corner)
        self.assertIsNone(card.selected_series)
        self.assertEqual(plot.additional_series[0].curve.opts["pen"].width(), series_line_width())
        self.assertEqual(card.legend.selected_rows(), [])

    def test_new_widths_apply_to_open_cards_and_a_removed_series_is_deselected(self):
        path, card = self.make_line()
        card.select_series((path, "a"))
        set_series_line_widths(3, 8)
        self.addCleanup(set_series_line_widths, 2, 5)
        card.apply_series_widths()
        self.assertEqual((card.series_plot.curve.opts["pen"].width(),
                          card.series_plot.additional_series[0].curve.opts["pen"].width()), (8, 3))
        card.remove_series_at(0)
        self.assertIsNone(card.selected_series)


class EventStateSelectionTests(_QtTestCase):
    def test_a_click_on_a_lane_selects_it(self):
        path = self.make_cache(unix=False)
        card = EventStatePlotWidget(path, "a")
        self.widgets.append(card)
        card.add_series(path, "b")
        card.resize(700, 400)
        card.show()
        self.wait_for(lambda: len({source for _bar, source in card._lane_bars}) == 2)
        bar, source = next((bar, source) for bar, source in card._lane_bars if source == (path, "b"))
        options = bar.opts
        x = float((options["x0"][0] + options["x1"][0]) / 2)
        y = float((options["y0"][0] + options["y1"][0]) / 2)
        card.plot_widget.plot_left_clicked.emit(widget_point(card.plot_widget, card.plot_item.vb, x, y))
        self.assertEqual(card.selected_series, (path, "b"))
        widths = {source: bar.opts["pen"].width() for bar, source in card._lane_bars}
        self.assertEqual(widths, {(path, "a"): series_line_width(), (path, "b"): series_line_width(selected=True)})
        self.assertEqual([card.legend.rows.index(row) for row in card.legend.selected_rows()], [1])
        card.plot_widget.plot_left_clicked.emit(QPointF(2, 2))
        self.assertIsNone(card.selected_series)


if __name__ == "__main__":
    unittest.main()
