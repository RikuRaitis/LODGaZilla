"""Sample limit selector for cards that draw every Nth sample."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest import mock

import h5py
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.data.sampling import (
    SAMPLE_LIMIT_ALL, coverage_percent_text, decimation_step, sample_counts, sample_limit_label,
)
from src.data.xyz import XYZ_MAXIMUM_POINTS, AxisSource, XYZData, load_xyz_data
from src.gui.plots.gnss import MAP_MAXIMUM_POINTS, load_gnss_track
from src.gui.plots.xy import XY_MAXIMUM_POINTS, XYPlotWidget, load_xy_data
from src.gui.plots.xyz import XYZPlotWidget
from src.jobs.plot_groups import _Load

SAMPLES = 1_000


class SampleLimitDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "data.h5"
        with h5py.File(self.path, "w") as cache:
            for name, values in (("x", np.arange(SAMPLES)), ("y", np.arange(SAMPLES) * 2.),
                                 ("z", np.arange(SAMPLES) * 3.), ("GNSS/latitude", np.linspace(60, 61, SAMPLES)),
                                 ("GNSS/longitude", np.linspace(24, 25, SAMPLES))):
                group = cache.create_group("series/" + name)
                group.attrs.update(time_dataset="time", time_unit="s", navigator_name=name)
                group.create_dataset("time", data=np.arange(SAMPLES, dtype=float))
                group.create_dataset("value", data=values)

    def test_step_and_labels(self):
        self.assertEqual(decimation_step(1_000, 100), 10)
        self.assertEqual(decimation_step(1_000, SAMPLE_LIMIT_ALL), 1)
        self.assertEqual([sample_limit_label(limit) for limit in (20_000, 1_000_000, SAMPLE_LIMIT_ALL)],
                         ["20k points", "1M points", "All points"])

    def test_loaders_honour_the_limit_and_all_keeps_every_sample(self):
        xy = lambda limit: load_xy_data(self.path, "x", self.path, "y", maximum_points=limit)[0]
        xyz = lambda limit: load_xyz_data(tuple(AxisSource(self.path, name) for name in "xyz"),
                                          maximum_points=limit).time
        track = lambda limit: load_gnss_track(self.path, "GNSS/latitude", self.path, "GNSS/longitude",
                                              maximum_points=limit)["lat"]
        for name, load in (("xy", xy), ("xyz", xyz), ("map", track)):
            with self.subTest(card=name):
                self.assertLessEqual(len(load(100)), 100)
                self.assertEqual(len(load(SAMPLE_LIMIT_ALL)), SAMPLES)

    def test_loaders_report_drawn_and_available_samples(self):
        xy = load_xy_data(self.path, "x", self.path, "y", maximum_points=100)
        xyz = load_xyz_data(tuple(AxisSource(self.path, name) for name in "xyz"), maximum_points=100)
        track = load_gnss_track(self.path, "GNSS/latitude", self.path, "GNSS/longitude", maximum_points=100)
        for result in (xy, xyz, track):
            with self.subTest(result=type(result).__name__):
                shown, available = sample_counts(result)
                self.assertEqual(available, SAMPLES)
                self.assertLessEqual(shown, 100)
        # X/Y results still unpack as five values.
        self.assertEqual(len(xy), 5)

    def test_percent_text_never_rounds_a_partial_view_to_100(self):
        self.assertEqual([coverage_percent_text(*counts) for counts in
                          ((100, 100), (999, 1_000), (114, 1_000), (1, 1_000))],
                         ["100%", "99%", "11%", "<1%"])

    def test_grouped_loader_passes_a_card_limit_only_when_set(self):
        calls = []
        load_data = lambda axes, **kwargs: calls.append(kwargs)
        _Load(1, ((1,),), None, load_data).run()
        _Load(2, ((1,),), None, load_data, maximum_points=500).run()
        self.assertNotIn("maximum_points", calls[0])
        self.assertEqual(calls[1]["maximum_points"], 500)


class SampleLimitSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selector_defaults_sits_left_of_close_and_reloads(self):
        entry = (Path("missing.h5"), "x")
        for card_type, default in ((XYPlotWidget, XY_MAXIMUM_POINTS), (XYZPlotWidget, XYZ_MAXIMUM_POINTS)):
            with self.subTest(card=card_type.__name__):
                with mock.patch("src.jobs.plot_groups.GroupedPlotLoadCoordinator.request") as request:
                    card = card_type(entry, entry) if card_type is XYPlotWidget else card_type(entry, entry, entry)
                    self.addCleanup(card.deleteLater)
                    card.resize(700, 300)
                    card.show()
                    card.set_header_controls_shown(True)
                    self.app.processEvents()
                    combo = card.sample_limit_combo
                    self.assertEqual(card.sample_limit, default)
                    self.assertEqual(combo.currentData(), default)
                    # The Offset and Help buttons sit next to Close; the selector comes next.
                    self.assertEqual(card.offset_button.geometry().right() + 2, card.controls_button.x() - 1)
                    self.assertEqual(card.help_button.geometry().right() + 2, card.offset_button.x() - 1)
                    self.assertEqual(combo.geometry().right() + 2, card.help_button.x() - 1)
                    if card_type is XYZPlotWidget:
                        # The Reset camera button follows to the left of the selector.
                        self.assertLess(card.reset_button.x(), combo.x())
                    request.reset_mock()
                    combo.setCurrentIndex(combo.findData(SAMPLE_LIMIT_ALL))
                    self.assertEqual(card.sample_limit, SAMPLE_LIMIT_ALL)
                    self.assertEqual(request.call_args.args[2], SAMPLE_LIMIT_ALL)

    def test_selector_shows_drawn_share_and_bold_when_partial(self):
        entry = (Path("missing.h5"), "x")
        with mock.patch("src.jobs.plot_groups.GroupedPlotLoadCoordinator.request"):
            card = XYPlotWidget(entry, entry)
        self.addCleanup(card.deleteLater)
        combo = card.sample_limit_combo
        self.assertEqual(combo.currentText(), "100k max")
        partial = mock.Mock(shown_points=100_000, available_points=400_000)
        card.update_sample_coverage([partial])
        self.assertEqual(combo.currentText(), "25% · 100k max")
        self.assertEqual(combo.itemText(combo.findData(200_000)), "50% · 200k max")
        # Only the first limit that draws everything is offered; higher ones add nothing.
        self.assertEqual(combo.itemText(combo.count() - 1), "100% · 500k max")
        self.assertEqual((combo.findData(1_000_000), combo.findData(SAMPLE_LIMIT_ALL)), (-1, -1))
        self.assertTrue(combo.font().bold())
        self.assertIn("Drawing 100,000 of 400,000 samples", combo.toolTip())
        card.update_sample_coverage([mock.Mock(shown_points=8_000, available_points=8_000)])
        self.assertEqual(combo.currentText(), "100% · 100k max")
        # Every limit draws all 8,000 samples: the smallest one and the current one remain.
        self.assertEqual([combo.itemData(index) for index in range(combo.count())], [10_000, 100_000])
        self.assertFalse(combo.font().bold())
        self.assertIn("Drawing all 8,000 samples", combo.toolTip())
        # Choosing a remaining limit still works, and before a load every limit is listed.
        combo.setCurrentIndex(0)
        self.assertEqual(card.sample_limit, 10_000)
        card.update_sample_coverage([])
        self.assertEqual(combo.findData(SAMPLE_LIMIT_ALL), combo.count() - 1)

    def test_changing_the_limit_keeps_the_xy_zoom_and_position(self):
        entry = (Path("missing.h5"), "x")
        values = np.arange(100.)
        with mock.patch("src.jobs.plot_groups.GroupedPlotLoadCoordinator.request"):
            card = XYPlotWidget(entry, entry)
            self.addCleanup(card.deleteLater)
            card._loaded([(values, values, values * 2, "x", "y")])
            card.plot_item.vb.setRange(xRange=(10, 20), yRange=(30, 40), padding=0)
            card.sample_limit_combo.setCurrentIndex(card.sample_limit_combo.findData(SAMPLE_LIMIT_ALL))
            card._loaded([(values, values, values * 2, "x", "y")])
        np.testing.assert_allclose(card.plot_item.vb.viewRange(), [(10, 20), (30, 40)])

    def test_changing_the_limit_keeps_the_3d_axis_scaling(self):
        entry = (Path("missing.h5"), "x")
        full = np.column_stack([np.arange(10.)] * 3)
        with mock.patch("src.jobs.plot_groups.GroupedPlotLoadCoordinator.request"):
            card = XYZPlotWidget(entry, entry, entry)
            self.addCleanup(card.deleteLater)
            card._loaded([XYZData(full[:, 0], full, ("x", "y", "z"), False)])
            extent = card._extent
            card.view.setCameraPosition(distance=3, elevation=10, azimuth=80)
            camera = card.view.cameraParams()
            card.sample_limit_combo.setCurrentIndex(card.sample_limit_combo.findData(SAMPLE_LIMIT_ALL))
            # Every-Nth sampling can drop the last sample, which would otherwise rescale the axes.
            card._loaded([XYZData(full[:-1, 0], full[:-1], ("x", "y", "z"), True)])
        np.testing.assert_allclose(card._extent, extent)
        self.assertEqual(card.view.cameraParams(), camera)
        # Later loads that are not limit changes scale to their own data again.
        card.on_series_changed()
        card._loaded([XYZData(full[:-1, 0], full[:-1], ("x", "y", "z"), True)])
        self.assertLess(card._extent[1][0], extent[1][0])

    def test_header_row_places_left_and_right_controls_beside_the_drag_strip(self):
        from PySide6.QtWidgets import QLabel
        from src.gui.plots.base import PlotBaseWidget
        card = PlotBaseWidget(Path("missing.h5"), "x")
        self.addCleanup(card.deleteLater)
        left, right = QLabel("Backend", card), QLabel("FPS", card)
        card.header_left_controls.append(left)
        card.header_controls.append(right)
        card.resize(600, 200)
        card.show()
        card.set_header_controls_shown(True)
        self.app.processEvents()
        center = card.close_button.geometry().center().y()
        self.assertEqual(left.x(), 8)
        self.assertEqual(right.geometry().right() + 2, card.help_button.x() - 1)
        for label in (left, right):
            self.assertLessEqual(abs(label.geometry().center().y() - center), 1)

    def test_help_button_lists_the_cards_mouse_actions(self):
        from PySide6.QtWidgets import QLabel
        from src.gui.plots.card_help import MOUSE_HELP
        entry = (Path("missing.h5"), "x")
        with mock.patch("src.jobs.plot_groups.GroupedPlotLoadCoordinator.request"):
            card = XYPlotWidget(entry, entry)
        self.addCleanup(card.deleteLater)
        self.assertFalse(card.help_button.icon().isNull())
        card.help_button.click()
        popup = card._help_popup
        self.assertIsInstance(popup, QLabel)
        self.assertTrue(popup.isVisible())
        text = popup.text()
        self.assertIn("X/Y: mouse actions", text)
        for action, _effect in MOUSE_HELP["xy"]:
            self.assertIn(action, text)
        self.assertIn("Top strip: drag", text)
        # Any mouse press outside the help closes it.
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent

        def press(widget):
            point = QPointF(2, 2)
            QApplication.sendEvent(widget, QMouseEvent(QEvent.Type.MouseButtonPress, point, point,
                                                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                                       Qt.KeyboardModifier.NoModifier))

        press(popup)
        self.assertTrue(popup.isVisible())
        press(card.plot_widget.viewport())
        self.assertFalse(popup.isVisible())
        # A press on the help button while it is open closes it without reopening it.
        card.help_button.click()
        press(card.help_button)
        card.help_button.click()
        self.assertIsNone(card._help_popup)
        # A press that closed it but was released off the button does not swallow the next click.
        card.help_button.click()
        press(card.help_button)
        card.help_button.released.emit()
        self.app.processEvents()
        card.help_button.click()
        self.assertIsNotNone(card._help_popup)
        card._help_popup.close()
        # A saved Sample limit the choices lack is shown as itself, not as the first choice.
        card.restore_workspace_state({"groups": [], "sample_limit": 123_456})
        self.assertEqual(card.sample_limit_combo.currentData(), 123_456)
        self.assertEqual(card.sample_limit, 123_456)
        # Every card type has its own help topic.
        from src.data.plot_policy import CARD_TITLES
        self.assertEqual(set(MOUSE_HELP), set(CARD_TITLES))

    def test_map_cards_default_to_twenty_thousand_points(self):
        from src.gui.plots.gnss import GnssPlotWidget
        from src.gui.plots.gnss_3d import Gnss3DPlotWidget
        self.assertEqual(GnssPlotWidget.default_sample_limit, MAP_MAXIMUM_POINTS)
        self.assertEqual(Gnss3DPlotWidget.default_sample_limit, MAP_MAXIMUM_POINTS)


if __name__ == "__main__":
    unittest.main()
