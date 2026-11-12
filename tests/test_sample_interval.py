"""Sample interval cards: loading, card choices, menus and drops."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest

import h5py
import numpy as np
from PySide6.QtCore import QEvent, QThreadPool
from tests.qt_wait import qt_wait
from PySide6.QtWidgets import QApplication

from src.app.lodgazilla import PlotWorkspace
from src.data.plot_policy import CARD_TITLES, PlotSource, compatible_cards, plan_cards
from src.gui.plots.sample_interval import SampleIntervalPlotWidget
from src.gui.plots.series import load_sample_interval_series, load_selection_statistics
from src.jobs.plot_drop import inspect_sources


class SampleIntervalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "data.h5"
        with h5py.File(self.path, "w") as cache:
            # 10 ms steps with one 50 ms gap, stored in milliseconds.
            self.write(cache, "fast", "time_ms", "ms", [0, 10, 20, 70, 80], [1., 2., 3., 4., 5.])
            self.write(cache, "slow", "time_s", "s", [0., 1., 2.], [0., 1., 0.])
            self.write(cache, "state", "time_s", "s", [0., 0.5, 2.], [0, 1, 0], unit="bool")

    def tearDown(self):
        QThreadPool.globalInstance().waitForDone(10_000)
        self.temp.cleanup()

    @staticmethod
    def write(cache, name, time_name, time_unit, times, values, unit="V"):
        group = cache.create_group("series/" + name)
        group.attrs.update(time_dataset=time_name, time_unit=time_unit, unit=unit, navigator_name=name)
        group.create_dataset(time_name, data=times)
        group.create_dataset("value", data=values)

    def test_intervals_are_converted_to_seconds(self):
        data = load_sample_interval_series(self.path, "fast")
        np.testing.assert_allclose(data.y, [0.01, 0.01, 0.05, 0.01])
        self.assertEqual((data.unit, data.title), ("s", "fast interval"))
        # Without a log start, the selection range uses the native time unit (ms).
        statistics = load_selection_statistics(self.path, "fast", 0.0, 80.0, quantity="interval")
        self.assertEqual(statistics.unit, "s")
        self.assertAlmostEqual(statistics.maximum, 0.05)
        self.assertEqual(statistics.sample_count, 4)

    def test_offered_for_numeric_and_state_series_and_packs_into_one_card(self):
        self.assertEqual(CARD_TITLES["interval"], "Sample interval")
        items = inspect_sources([PlotSource("series", self.path, name) for name in ("fast", "slow", "state")])
        for item in items:
            self.assertIn("interval", item.supported)
        self.assertIn("interval", compatible_cards(items))
        # Not a default: the default card rules are unchanged.
        self.assertNotIn("interval", [plan.kind for plan in plan_cards(items)])
        plans = plan_cards(items, "interval")
        self.assertEqual([(plan.kind, len(plan.items)) for plan in plans], [("interval", 3)])

    def test_workspace_opens_card_and_accepts_more_series(self):
        workspace = PlotWorkspace(settings_path=Path(self.temp.name) / "settings.json")
        self.addCleanup(lambda: (workspace.close_all_plots(), workspace.deleteLater(),
                                 self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)))
        workspace.resize(900, 700)
        workspace.show()
        items = inspect_sources([PlotSource("series", self.path, "fast")])
        card, = workspace.open_plot_inputs(items, "interval")
        self.app.processEvents()
        self.assertIsInstance(card, SampleIntervalPlotWidget)
        self.assertEqual(card.drop_card_type, "interval")
        self.assertEqual(workspace._plot_navigator_type(card), "interval")
        axis = card.series_plot.plot_item.getAxis("left")
        self.assertEqual(axis.labelText, "Sample interval")
        np.testing.assert_allclose(card.series_plot._current_data.y, [0.01, 0.01, 0.05, 0.01])

        # The active Sample interval card accepts further series as extra lines.
        more = inspect_sources([PlotSource("series", self.path, "slow")])
        workspace.activate_plot(card)
        self.assertTrue(workspace.active_card_accepts(more))
        workspace.plot_in_active_card(more)
        qt_wait(50)
        self.assertEqual(card.series, [(self.path, "fast"), (self.path, "slow")])
        extra = card.series_plot.additional_series[0].data
        np.testing.assert_allclose(extra.y, [1.0, 1.0])

        # Range-selection statistics describe intervals, not sample values.
        loader = card.series_plot._selection_loads._create_loader(
            (1, 0.0, 80.0, list(card.series)), card.series_plot)
        self.assertEqual(loader.quantity, "interval")
        loader.deleteLater()

        # Marker values describe sample values, so the card does not show them.
        card.series_plot.show_marker_values(0.02)
        self.assertFalse(card.series_plot.value_boxes.visible_boxes())


if __name__ == "__main__":
    unittest.main()
