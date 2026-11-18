"""The Plot navigator's Markers button: hidden by default, no card places markers, and hiding
clears the markers and selection everywhere."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF  # noqa: E402

from src.gui.plots.markers import FIRST_MARKER, SECOND_MARKER, markers_shown, set_markers_shown  # noqa: E402
from tests.test_review_plots import _QtTestCase  # noqa: E402


class MarkersButtonTests(_QtTestCase):
    def workspace(self):
        from PySide6.QtCore import QThreadPool
        from src.app.lodgazilla import PlotWorkspace

        workspace = PlotWorkspace(settings_path=self.directory / "settings.json")
        workspace.resize(900, 600)
        workspace.show()

        def cleanup():
            set_markers_shown(False)
            workspace.close_all_plots()
            workspace.close()
            workspace.deleteLater()
            QThreadPool.globalInstance().waitForDone(5000)
            self.app.processEvents()

        self.addCleanup(cleanup)
        return workspace

    def test_hidden_markers_are_not_placed_and_hiding_clears_them(self):
        self.assertFalse(markers_shown())
        workspace = self.workspace()
        path = self.make_cache()
        card = workspace.add_line_plot(path, "a")
        other = workspace.add_line_plot(path, "b")
        self.app.processEvents()
        plot = card.series_plot.plot_widget
        center = plot.interactive_view_box.sceneBoundingRect().center()
        # Hidden: a click places nothing, nor does the Plot navigator.
        plot._place_marker_at(center, FIRST_MARKER)
        workspace.place_marker_from_plot_navigator(10.0)
        self.assertEqual(card.marker_values(), (None, None))
        # Shown: markers and a selection are placed.
        workspace.set_markers_shown(True)
        plot._place_marker_at(center, FIRST_MARKER)
        plot._place_marker_at(QPointF(center.x() + 50, center.y()), SECOND_MARKER)
        self.assertTrue(all(value is not None for value in card.marker_values()))
        self.assertIsNotNone(plot.selection_range())
        # Hiding clears both markers and the selection on every card.
        workspace.set_markers_shown(False)
        for each in (card, other):
            self.assertEqual(each.marker_values(), (None, None))
        self.assertIsNone(plot.selection_range())
        self.assertIsNone(workspace._global_marker_timestamp)

    def test_point_marker_cards_place_nothing_while_hidden(self):
        workspace = self.workspace()
        path = self.make_cache()
        card = workspace.add_xy_plot((path, "a"), (path, "b"))
        self.wait_for(lambda: not card._loading)
        card._place_marker_from_view(FIRST_MARKER, 5.0)
        self.assertEqual(card.marker_values(), (None, None))
        workspace.set_markers_shown(True)
        card._place_marker_from_view(FIRST_MARKER, 5.0)
        self.assertEqual(card.marker_values()[0], 5.0)

    def test_the_button_is_off_by_default_and_saves_its_state(self):
        import json
        from unittest import mock
        from src.app.lodgazilla import LODGaZillaWindow
        from src.plugins.manager import PluginManager
        settings = self.directory / "settings.json"
        settings.write_text(json.dumps({"navigator_controls": {"recent_folders": []}}))
        with mock.patch("src.app.lodgazilla.PluginManager",
                        side_effect=lambda window, **kwargs: PluginManager(window, plugin_root=self.directory / "none",
                                                                           **kwargs)), \
                mock.patch("src.plugins.updates.PluginUpdates.check", lambda self, sources: None):
            window = LODGaZillaWindow(self.directory, settings_path=settings)
        self.widgets.append(window)
        button = window.plot_navigator.markers_button
        self.assertFalse(button.isChecked())
        # First in the marker group, left of Markers linked.
        layout = button.parentWidget().layout()
        self.assertEqual(layout.indexOf(button) + 1, layout.indexOf(window.plot_navigator.marker_link_button))
        self.assertFalse(markers_shown())
        button.click()
        self.assertTrue(markers_shown())
        from src.app.settings import load_lodgazilla_settings
        self.assertTrue(load_lodgazilla_settings(settings)["show_markers"])
        button.click()
        self.assertFalse(markers_shown())
