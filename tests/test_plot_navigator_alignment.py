"""The Plot navigator's full range spans exactly the time cards' plot area, at both edges."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.app.lodgazilla import LODGaZillaWindow  # noqa: E402
from src.plugins.manager import PluginManager  # noqa: E402
from tests.qt_wait import qt_wait, qt_wait_until  # noqa: E402
from tests.test_frequency_plot import write_signal  # noqa: E402
from tests.test_workspace_file import write_cache  # noqa: E402


def plot_area_edges(window, card):  # type: ignore[no-untyped-def]
    """A card's plot area edges in the Plot navigator canvas' coordinates."""
    view_box = card.plot_view_box()
    widget = view_box.getViewWidget()
    bounds = view_box.sceneBoundingRect()
    canvas = window.plot_navigator.canvas
    left = canvas.mapFromGlobal(widget.mapToGlobal(QPoint(widget.mapFromScene(bounds.topLeft()).x(), 0))).x()
    right = canvas.mapFromGlobal(widget.mapToGlobal(QPoint(widget.mapFromScene(bounds.topRight()).x(), 0))).x()
    return left, right


class PlotNavigatorAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self):  # type: ignore[no-untyped-def]
        folder = Path(self.enterContext(tempfile.TemporaryDirectory(ignore_cleanup_errors=True)))
        settings = folder / "settings.json"
        settings.write_text(json.dumps({"navigator_controls": {"recent_folders": []}}))
        with mock.patch("src.app.lodgazilla.PluginManager",
                        side_effect=lambda window, **kwargs: PluginManager(window, plugin_root=folder / "empty", **kwargs)):
            window = LODGaZillaWindow(folder / "data", settings_path=settings)
        self.addCleanup(lambda: (window.close(), self.app.processEvents()))
        window.resize(1400, 700)
        window.show()
        return window, folder

    def assert_aligned(self, window, card):  # type: ignore[no-untyped-def]
        navigator = window.plot_navigator
        def aligned():
            left, right = plot_area_edges(window, card)
            margins = navigator.canvas.margins()
            return margins == (left, navigator.canvas.width() - right)
        self.assertTrue(qt_wait_until(aligned, 3000), (navigator.canvas.margins(), plot_area_edges(window, card)))

    def test_right_edge_follows_the_cards_when_a_scroll_bar_narrows_them(self):
        window, folder = self.window()
        cache = folder / "data" / "rec.h5"
        write_cache(cache)
        card = window.plot_workspace.add_line_plot(cache, "x")
        self.assert_aligned(window, card)
        # Enough cards for the workspace's scroll bar, which narrows every card on the right only.
        for _ in range(6):
            window.plot_workspace.add_line_plot(cache, "y")
        qt_wait(200)
        self.assertTrue(window.plot_workspace.verticalScrollBar().isVisible())
        self.assert_aligned(window, card)

    def test_full_range_puts_the_first_and_last_samples_on_the_plot_area_edges(self):
        window, folder = self.window()
        cache = folder / "data" / "rec.h5"
        write_cache(cache)
        card = window.plot_workspace.add_line_plot(cache, "x")
        self.assert_aligned(window, card)
        times = card.series_plot.curve.getData()[0]
        # No auto-range padding: the card shows exactly the Plot navigator's full range.
        self.assertEqual(card.plot_view_box().viewRange()[0], [float(times[0]), float(times[-1])])
        self.assertEqual(tuple(window.plot_navigator.global_range()), (float(times[0]), float(times[-1])))

    def test_frequency_card_alone_sets_the_edges(self):
        window, folder = self.window()
        signal = folder / "data" / "signal.h5"
        signal.parent.mkdir(parents=True, exist_ok=True)
        times = np.arange(8192) / 4096
        write_signal(signal, times, np.sin(2 * np.pi * 512 * times))
        card = window.plot_workspace.add_frequency_plot(signal, "Scope/Channel A")
        self.assert_aligned(window, card)


if __name__ == "__main__":
    unittest.main()
