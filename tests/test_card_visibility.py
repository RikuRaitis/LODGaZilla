"""The Plot navigator's Legends and Card controls buttons."""
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

    def test_card_controls_hide_on_every_card_and_restore_their_states(self):
        workspace = self.workspace()
        path = self.make_cache()
        card = workspace.add_line_plot(path, "a")
        self.app.processEvents()
        controls = [card.close_button, card.help_button, card.offset_button]
        self.assertTrue(all(control.isVisible() for control in controls))
        card.offset_button.click()  # An open Offset editor closes with the controls.
        workspace.set_card_controls_shown(False)
        self.assertFalse(any(control.isVisible() for control in (card.offset_button, card.offset_editor)))
        # Close and Help stay, with Help moved next to Close.
        self.assertTrue(card.close_button.isVisible() and card.help_button.isVisible())
        self.assertEqual(card.help_button.geometry().right() + 2, card.close_button.x() - 1)
        self.assertTrue(card.drag_handle.isVisible())
        # Controls a card shows while hidden stay hidden, and cards opened later start hidden.
        card.offset_button.show()
        card._layout_header_controls()
        self.assertFalse(card.offset_button.isVisible())
        later = workspace.add_line_plot(path, "b")
        self.app.processEvents()
        self.assertFalse(later.offset_button.isVisible())
        self.assertTrue(later.close_button.isVisible() and later.help_button.isVisible())
        workspace.set_card_controls_shown(True)
        for plot in (card, later):
            self.assertTrue(all(control.isVisible()
                                for control in (plot.close_button, plot.help_button, plot.offset_button)))
            self.assertFalse(plot.offset_editor.isVisible())
        self.assertEqual(card.offset_button.geometry().right() + 2, card.close_button.x() - 1)

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
        legends, controls = [], []
        navigator.legends_visibility_changed.connect(legends.append)
        navigator.card_controls_visibility_changed.connect(controls.append)
        navigator.legends_button.click()
        navigator.card_controls_button.click()
        self.assertEqual((legends, controls), ([False], [False]))
        self.assertEqual(navigator.legends_button.toolTip(), "Legends hidden")
        navigator.set_legends_shown(True)
        navigator.set_card_controls_shown(True)
        self.assertEqual((legends, controls), ([False], [False]))
        self.assertEqual(navigator.card_controls_button.toolTip(), "Card controls shown")

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
