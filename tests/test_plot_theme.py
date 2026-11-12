"""Canvas colors stay consistent across renderers and theme changes."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from unittest import TestCase, mock

import pyqtgraph as pg
import gc

from PySide6.QtCore import QEvent
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from src.gui.theme import _apply_palette_to_application, dark_palette, light_palette
from src.gui.plots.theme import apply_plot_palette
from src.gui.plots.xyz import XYZPlotWidget
from src.gui.plots.video.viewport import Viewport
from src.gui.plots.gnss import map_html
from src.gui.plots.gnss_3d import _three_d_map_html
from tests.test_xyz_plot import FakeView


class PlotThemeTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_palette_broadcast_runs_without_garbage_collection(self):
        # The collector could otherwise free a widget tree while Qt visits it.
        seen = []

        class Recorder(QWidget):
            def changeEvent(self, event):  # noqa: N802
                if event.type() in {QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange}:
                    seen.append(gc.isenabled())
                super().changeEvent(event)

        widget = Recorder()
        self.addCleanup(widget.deleteLater)
        original = QPalette(self.app.palette())
        self.addCleanup(lambda: _apply_palette_to_application(self.app, original))
        self.assertTrue(gc.isenabled())
        _apply_palette_to_application(self.app, dark_palette())
        self.assertTrue(seen)
        self.assertFalse(any(seen))
        self.assertTrue(gc.isenabled())

    def test_2d_and_3d_canvases_match_when_switching_themes(self):
        plot = pg.PlotWidget()
        self.addCleanup(plot.close)
        with mock.patch.object(XYZPlotWidget, "view_class", FakeView):
            # An incomplete trace needs no input file and still has a canvas.
            card = XYZPlotWidget((Path("unused.h5"), "x"))
        self.addCleanup(card.close)
        self.addCleanup(card.dispose)
        for palette, expected in ((dark_palette(), "#000000"), (light_palette(), "#ffffff"),
                                  (dark_palette(), "#000000")):
            plot.setPalette(palette)
            card.setPalette(palette)
            apply_plot_palette(plot)
            card.apply_theme()
            self.assertEqual(plot.backgroundBrush().color(), QColor(expected))
            self.assertEqual(card.view.background, QColor(expected))

    def test_video_clear_color_follows_canvas_theme(self):
        viewport = Viewport()
        self.addCleanup(viewport.close)
        for palette, expected in ((dark_palette(), (0., 0., 0., 1.)),
                                  (light_palette(), (1., 1., 1., 1.))):
            viewport.setPalette(palette)
            with mock.patch("src.gui.plots.video.viewport.gl.glClearColor") as clear_color, \
                    mock.patch("src.gui.plots.video.viewport.gl.glClear"):
                viewport.paintGL()
                clear_color.assert_called_once_with(*expected)

    def test_map_pages_start_with_the_requested_canvas_color(self):
        for background in ("#000000", "#ffffff"):
            for build in (map_html, _three_d_map_html):
                self.assertIn(f"background:{background}", build(background=background))


class ThemeApplicationTests(TestCase):
    def test_applying_the_current_theme_again_does_nothing(self):
        from PySide6.QtWidgets import QApplication
        from src.gui.theme import apply_theme

        QApplication.instance() or QApplication([])
        apply_theme("dark")
        self.assertFalse(apply_theme("dark"))
        self.assertTrue(apply_theme("dark", force=True))
        self.assertTrue(apply_theme("os"))
