"""The Plot navigator's Legends button and each card's Legend and Card controls buttons."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.gui.plots.legend import legends_shown, set_legends_shown
from src.gui.plots.navigator import PlotNavigatorWidget
from tests.test_review_plots import _QtTestCase


class CardVisibilityTests(_QtTestCase):
    def workspace(self):
        from PySide6.QtCore import QThreadPool
        from src.app.lodgazilla import PlotWorkspace

        workspace = PlotWorkspace(settings_path=self.directory / "settings.json")
        workspace.resize(900, 600)
        workspace.show()

        def cleanup():
            set_legends_shown(True)
            workspace.close_all_plots()
            workspace.close()
            workspace.deleteLater()
            QThreadPool.globalInstance().waitForDone(5000)
            self.app.processEvents()

        self.addCleanup(cleanup)
        return workspace

    def test_legends_hide_on_every_card_and_return_as_each_card_wants(self):
        workspace = self.workspace()
        path = self.make_cache()
        card = workspace.add_multi_plot([(path, "a"), (path, "b")])
        self.app.processEvents()
        self.assertTrue(card.legend.isVisible())
        workspace.set_legends_shown(False)
        self.assertFalse(legends_shown())
        self.assertFalse(card.legend.isVisible())
        # A card opened while legends are hidden starts without one; its series still load.
        later = workspace.add_multi_plot([(path, "c"), (path, "a")])
        self.app.processEvents()
        self.assertFalse(later.legend.isVisible())
        workspace.set_legends_shown(True)
        self.assertTrue(card.legend.isVisible())
        self.assertTrue(later.legend.isVisible())
        # A legend its card hides, such as an empty one, stays hidden.
        card.legend.setVisible(False)
        workspace.set_legends_shown(False)
        workspace.set_legends_shown(True)
        self.assertFalse(card.legend.isVisible())

    def test_each_card_shows_or_hides_its_own_legend(self):
        workspace = self.workspace()
        states = []
        workspace.legends_state_changed.connect(states.append)
        path = self.make_cache()
        card = workspace.add_multi_plot([(path, "a"), (path, "b")])
        other = workspace.add_multi_plot([(path, "a"), (path, "b")])
        self.app.processEvents()
        self.assertTrue(card.legend_button.isChecked() and card.legend.isVisible())
        # The card's button hides only its own legend; the Legends button then reads off.
        card.legend_button.click()
        self.assertFalse(card.legend.isVisible() or card.legend_button.isChecked())
        self.assertEqual(card.legend_button.toolTip(), "Show legend")
        self.assertTrue(other.legend.isVisible())
        self.assertEqual(states, [False])
        self.assertTrue(legends_shown())
        # It is saved with the card and restored.
        self.assertFalse(card.workspace_state()["legend_shown"])
        other.restore_workspace_state({"legend_shown": False})
        self.assertFalse(other.legend.isVisible())
        card.legend_button.click()
        other.legend_button.click()
        self.assertEqual(states[-1], True)
        # The Legends button shows or hides every card's legend at once, and new cards follow it.
        card.legend_button.click()
        workspace.set_legends_shown(True)
        self.assertTrue(card.legend.isVisible() and other.legend.isVisible() and card.legend_button.isChecked())
        workspace.set_legends_shown(False)
        self.assertFalse(card.legend.isVisible() or other.legend.isVisible())
        later = workspace.add_multi_plot([(path, "a")])
        self.app.processEvents()
        self.assertFalse(later.legend_shown)
        # A card without a legend has no Legend button.
        image = self.directory / "image.png"
        from PySide6.QtGui import QColor, QImage
        picture = QImage(20, 10, QImage.Format.Format_ARGB32)
        picture.fill(QColor("red"))
        picture.save(str(image))
        image_card = workspace.add_image_plot(self.directory, image)
        self.app.processEvents()
        self.assertFalse(image_card.legend_button.isVisible())
        self.assertNotIn("legend_shown", image_card.workspace_state())

    def test_each_card_opens_its_own_controls_and_closes_them_when_inactive(self):
        workspace = self.workspace()
        path = self.make_cache()
        card = workspace.add_line_plot(path, "a")
        other = workspace.add_line_plot(path, "b")
        self.app.processEvents()
        # Only Close, Legend and the Card controls button show by default; Help is a card control.
        for plot in (card, other):
            self.assertTrue(all(button.isVisible()
                                for button in (plot.close_button, plot.legend_button, plot.controls_button)))
            self.assertFalse(any(control.isVisible() for control in (plot.help_button, plot.offset_button)))
            self.assertTrue(plot.drag_handle.isVisible())
        # Close is the rightmost button, then the Legend and Card controls buttons.
        self.assertEqual(card.legend_button.geometry().right() + 2, card.close_button.x() - 1)
        self.assertEqual(card.controls_button.geometry().right() + 2, card.legend_button.x() - 1)
        # Its click makes the card active and shows its controls, Help included.
        card.controls_button.click()
        self.assertIs(workspace.active_plot, card)
        self.assertTrue(card.controls_button.isChecked())
        self.assertTrue(all(control.isVisible() for control in (card.help_button, card.offset_button)))
        self.assertEqual(card.offset_button.geometry().right() + 2, card.controls_button.x() - 1)
        self.assertEqual(card.help_button.geometry().right() + 2, card.offset_button.x() - 1)
        self.assertFalse(other.offset_button.isVisible())
        # Controls a card shows while closed stay hidden.
        other.offset_button.show()
        other._layout_header_controls()
        self.assertFalse(other.offset_button.isVisible())
        # When another card becomes active, the controls close; an open Offset editor too.
        card.offset_button.click()
        workspace.activate_plot(other)
        self.assertFalse(card.controls_button.isChecked())
        self.assertFalse(any(control.isVisible() for control in (card.offset_button, card.offset_editor,
                                                                 card.help_button)))
        # A second click on an open card's button closes its controls; the card stays active.
        other.controls_button.click()
        self.assertTrue(other.offset_button.isVisible())
        other.controls_button.click()
        self.assertFalse(other.offset_button.isVisible())
        self.assertIs(workspace.active_plot, other)

    def test_drag_strip_is_tinted_while_the_pointer_is_over_it(self):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QEnterEvent

        workspace = self.workspace()
        card = workspace.add_line_plot(self.make_cache(), "a")
        self.app.processEvents()
        strip = card.drag_handle
        # The test platform's pointer may start over the strip.
        self.app.sendEvent(strip, QEvent(QEvent.Type.Leave))
        plain = strip.grab().toImage()
        self.app.sendEvent(strip, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
        self.assertTrue(strip.hovered)
        self.assertNotEqual(strip.grab().toImage(), plain)
        self.app.sendEvent(strip, QEvent(QEvent.Type.Leave))
        self.assertFalse(strip.hovered)
        self.assertEqual(strip.grab().toImage(), plain)

    def test_plot_navigator_button_hides_the_overview_and_keeps_the_toolbar(self):
        navigator = PlotNavigatorWidget()
        self.widgets.append(navigator)
        navigator.show()
        self.app.processEvents()
        changes = []
        navigator.expanded_changed.connect(changes.append)
        button = navigator.navigator_button
        self.assertTrue(button.isChecked() and navigator.canvas.isVisible())
        self.assertEqual(button.toolTip(), "Plot navigator shown")
        button.click()
        self.assertFalse(navigator.canvas.isVisible())
        self.assertTrue(navigator.header_widget.isVisible())
        self.assertEqual(navigator.height(), navigator.HEADER_HEIGHT)
        self.assertEqual(button.toolTip(), "Plot navigator hidden")
        # A saved state is shown on the button without a second change.
        navigator.set_expanded(True)
        self.assertTrue(button.isChecked() and navigator.canvas.isVisible())
        self.assertEqual(changes, [False, True])

    def test_navigator_buttons_emit_and_show_saved_states_quietly(self):
        navigator = PlotNavigatorWidget()
        self.widgets.append(navigator)
        legends = []
        navigator.legends_visibility_changed.connect(legends.append)
        navigator.legends_button.click()
        self.assertEqual(legends, [False])
        self.assertEqual(navigator.legends_button.toolTip(), "Legends hidden")
        navigator.set_legends_shown(True)
        self.assertEqual(legends, [False])
        self.assertEqual(navigator.legends_button.toolTip(), "Legends shown")
        # Card controls are per card now; the Plot navigator has no button for them.
        self.assertFalse(hasattr(navigator, "card_controls_button"))

    def test_plugins_group_shows_one_icon_per_launcher_and_emits_its_plugin(self):
        navigator = PlotNavigatorWidget()
        self.widgets.append(navigator)
        balance = navigator._header_balance.width()
        icon = self.directory / "icon.svg"
        icon.write_text("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 4 4'>"
                        "<rect width='4' height='4' fill='currentColor'/></svg>", encoding="utf-8")
        launched = []
        navigator.plugin_launch_requested.connect(launched.append)
        navigator.set_plugin_launchers([("device", "Device window", icon), ("plain", "Plain window", None)])
        self.assertFalse(navigator.plugin_bar.isHidden())
        self.assertEqual(navigator.plugin_buttons["device"].toolTip(), "Device window")
        self.assertFalse(navigator.plugin_buttons["device"].icon().isNull())
        self.assertFalse(navigator.plugin_buttons["plain"].icon().isNull())
        # The balance on the left grows with the group, so the toolbar stays centered.
        self.assertGreater(navigator._header_balance.width(), balance)
        navigator.plugin_buttons["plain"].click()
        self.assertEqual(launched, ["plain"])
        navigator.set_plugin_launchers([])
        self.assertTrue(navigator.plugin_bar.isHidden())
        self.assertEqual(navigator._header_balance.width(), balance)


if __name__ == "__main__":
    unittest.main()


class CenterViewTests(_QtTestCase):
    def test_cards_with_a_zoomable_view_have_center_view(self):
        from src.gui.plots.event_state import EventStatePlotWidget
        from src.gui.plots.frequency import FrequencyPlotWidget
        from src.gui.plots.gnss import GnssPlotWidget
        from src.gui.plots.gnss_3d import Gnss3DPlotWidget
        from src.gui.plots.image import ImagePlotWidget
        from src.gui.plots.line import LinePlotWidget
        from src.gui.plots.multi import MultiPlotWidget
        from src.gui.plots.sample_interval import SampleIntervalPlotWidget
        from src.gui.plots.xy import XYPlotWidget
        from src.gui.plots.xyz import XYZPlotWidget
        for card in (LinePlotWidget, MultiPlotWidget, SampleIntervalPlotWidget, EventStatePlotWidget,
                     FrequencyPlotWidget, XYPlotWidget, XYZPlotWidget, GnssPlotWidget):
            self.assertTrue(card.has_center_view, card.__name__)
        # The 3D Map has its own Center view among its map controls; an image does not zoom.
        for card in (Gnss3DPlotWidget, ImagePlotWidget):
            self.assertFalse(card.has_center_view, card.__name__)
        self.assertFalse(EventStatePlotWidget.center_view_fits_y)

    def test_a_time_card_shows_its_whole_time_range_again(self):
        from PySide6.QtCore import QThreadPool
        from src.app.lodgazilla import PlotWorkspace

        workspace = PlotWorkspace(settings_path=self.directory / "settings.json")
        workspace.resize(900, 600)
        workspace.show()
        self.addCleanup(lambda: (workspace.close_all_plots(), workspace.close(), workspace.deleteLater(),
                                 QThreadPool.globalInstance().waitForDone(5000)))
        card = workspace.add_line_plot(self.make_cache(), "a")
        self.app.processEvents()
        self.assertIn(card.center_view_button, card.header_controls)
        full = workspace._plot_navigator_full_ranges[card]
        view = card.plot_view_box()
        view.setXRange(full[0] + 10, full[0] + 20, padding=0)
        card.center_view_button.click()
        low, high = view.viewRange()[0]
        self.assertAlmostEqual(low, full[0] - card.time_offset, places=3)
        self.assertAlmostEqual(high, full[1] - card.time_offset, places=3)
