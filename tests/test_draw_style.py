"""Draw style selector: series drawn as Auto, Lines or Points."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest import mock

import h5py
import numpy as np
from PySide6.QtWidgets import QApplication

from src.data.xyz import XYZData
from src.gui.plots.base import DRAW_AUTO, DRAW_LINES, DRAW_POINTS
from src.gui.plots.event_state import EventStatePlotWidget
from src.gui.plots.gnss import GnssPlotWidget
from src.gui.plots.gnss_3d import Gnss3DPlotWidget
from src.gui.plots.line import LinePlotWidget
from src.gui.plots.multi import MultiPlotWidget
from src.gui.plots.sample_interval import SampleIntervalPlotWidget
from src.gui.plots.series import MAX_MARKER_POINTS
from src.gui.plots.xy import XYPlotWidget
from src.gui.plots.xyz import XYZPlotWidget


class DrawStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "data.h5"
        with h5py.File(self.path, "w") as cache:
            for name, count in (("few", 50), ("many", MAX_MARKER_POINTS + 500)):
                group = cache.create_group(f"series/{name}")
                group.attrs.update(time_dataset="time", time_unit="s", navigator_name=name)
                group.create_dataset("time", data=np.arange(count, dtype=float))
                group.create_dataset("value", data=np.sin(np.arange(count) / 10))

    def card(self, card):
        self.addCleanup(card.deleteLater)
        card.resize(700, 300)
        card.show()
        self.app.processEvents()
        return card

    def test_series_cards_have_the_selector_and_others_do_not(self):
        self.assertTrue(all(card_type.supports_draw_style for card_type in (
            LinePlotWidget, MultiPlotWidget, SampleIntervalPlotWidget, XYPlotWidget, XYZPlotWidget,
            GnssPlotWidget, Gnss3DPlotWidget)))
        self.assertFalse(EventStatePlotWidget.supports_draw_style)

    def test_selector_sits_in_the_header_row_left_of_the_sample_limit(self):
        entry = (Path("missing.h5"), "x")
        with mock.patch("src.jobs.plot_groups.GroupedPlotLoadCoordinator.request"):
            card = self.card(XYPlotWidget(entry, entry))
        selector = card.draw_style_selector
        # It is a card control: hidden until the card's Card controls button opens them.
        self.assertTrue(selector.isHidden())
        card.set_header_controls_shown(True)
        self.assertEqual(list(card.draw_style_buttons), [DRAW_AUTO, DRAW_LINES, DRAW_POINTS])
        self.assertTrue(card.draw_style_buttons[DRAW_AUTO].isChecked())
        self.assertEqual(selector.geometry().right() + 2, card.sample_limit_combo.x() - 1)
        self.assertLessEqual(abs(selector.geometry().center().y() - card.close_button.geometry().center().y()), 1)
        for style, button in card.draw_style_buttons.items():
            self.assertFalse(button.icon().isNull(), style)
        # Closing the card controls hides it with the other header controls.
        card.set_header_controls_shown(False)
        self.assertTrue(selector.isHidden())
        card.set_header_controls_shown(True)
        self.assertFalse(selector.isHidden())

    def test_line_card_auto_adds_points_only_when_few_raw_samples_are_in_view(self):
        card = self.card(LinePlotWidget(self.path, "many"))
        plot = card.series_plot
        card.series_plot.show_series_entries([(self.path, "many"), (self.path, "few")])
        many, few = plot.curve, plot.additional_series[0]
        self.assertTrue(many.isVisible())
        self.assertEqual((len(plot.markers.data), len(few.markers.data)), (0, 50))
        card.draw_style_buttons[DRAW_POINTS].click()
        self.assertEqual(card.draw_style, DRAW_POINTS)
        self.assertFalse(many.isVisible() or few.curve.isVisible())
        self.assertEqual(len(plot.markers.data), len(plot._current_data.x))
        card.draw_style_buttons[DRAW_LINES].click()
        self.assertTrue(many.isVisible() and few.curve.isVisible())
        self.assertEqual((len(plot.markers.data), len(few.markers.data)), (0, 0))
        # A reload keeps the style.
        plot.show_series_entries([(self.path, "few")])
        self.assertEqual(len(plot.markers.data), 0)

    def test_xy_and_3d_plot_draw_lines_points_or_both(self):
        entry = (Path("missing.h5"), "x")
        values = np.arange(20.)
        with mock.patch("src.jobs.plot_groups.GroupedPlotLoadCoordinator.request"):
            xy = self.card(XYPlotWidget(entry, entry))
            xy._loaded([(values, values, values * 2, "x", "y")])
            xyz = self.card(XYZPlotWidget(entry, entry, entry))
            xyz._loaded([XYZData(values, np.column_stack([values] * 3), ("x", "y", "z"), False)])
        curve, points = xy._graph_items[0]

        def drawn_3d():
            names = {type(item).__name__ for item in xyz._items}
            return "GLLinePlotItem" in names and any(len(item.pos) > 2 for item in xyz._items
                                                       if type(item).__name__ == "GLLinePlotItem"), \
                "GLScatterPlotItem" in names

        self.assertEqual((curve.isVisible(), points.isVisible()), (True, True))
        self.assertEqual(drawn_3d(), (True, True))
        for style, expected in ((DRAW_POINTS, (False, True)), (DRAW_LINES, (True, False)), (DRAW_AUTO, (True, True))):
            with self.subTest(style=style):
                xy.set_draw_style(style)
                xyz.set_draw_style(style)
                self.assertEqual((curve.isVisible(), points.isVisible()), expected)
                self.assertEqual(drawn_3d(), expected)

    def test_style_is_saved_in_workspaces_only_when_not_auto(self):
        entry = (Path("missing.h5"), "x")
        with mock.patch("src.jobs.plot_groups.GroupedPlotLoadCoordinator.request"):
            card = self.card(XYPlotWidget(entry, entry))
            self.assertNotIn("draw_style", card.workspace_state())
            card.set_draw_style(DRAW_POINTS)
            state = card.workspace_state()
            self.assertEqual(state["draw_style"], DRAW_POINTS)
            restored = self.card(XYPlotWidget(entry, entry))
            restored.restore_workspace_state(state)
        self.assertEqual(restored.draw_style, DRAW_POINTS)
        self.assertTrue(restored.draw_style_buttons[DRAW_POINTS].isChecked())
        # An unknown style keeps Auto.
        restored.restore_workspace_state({**state, "draw_style": "bogus"})
        self.assertEqual(restored.draw_style, DRAW_AUTO)

    def test_map_sends_the_style_with_its_tracks(self):
        card = GnssPlotWidget.__new__(GnssPlotWidget)
        card.draw_style = DRAW_POINTS
        card._track_data = [{"lat": [60.0], "lon": [24.0], "time": [0.0]}]
        card._pending_map_view = None
        card.map_view = mock.Mock()
        GnssPlotWidget._push_tracks(card, False)
        script = card.map_view.page().runJavaScript.call_args.args[0]
        self.assertTrue(script.startswith('drawStyle="points";'))
        self.assertIn("setTracks(", script)


if __name__ == "__main__":
    unittest.main()
