"""Card time offsets: display-only shifts of what a card shows on the shared time axis."""
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from src.gui.plots.image import ImagePlotWidget
from src.gui.plots.interactive import TimeAxisItem
from src.gui.plots.navigator import PlotNavigatorEntry, PlotNavigatorWidget
from tests.test_review_plots import LOG_START, _QtTestCase


class TimeOffsetTests(_QtTestCase):
    def workspace(self):
        from PySide6.QtCore import QThreadPool
        from src.app.lodgazilla import PlotWorkspace

        workspace = PlotWorkspace(settings_path=self.directory / "settings.json")
        workspace.resize(900, 600)
        workspace.show()

        def cleanup():
            workspace.close_all_plots()
            workspace.close()
            workspace.deleteLater()
            QThreadPool.globalInstance().waitForDone(5000)
            self.app.processEvents()

        self.addCleanup(cleanup)
        return workspace

    def test_offset_button_opens_an_editor_in_its_place_until_focus_leaves(self):
        workspace = self.workspace()
        card = workspace.add_line_plot(self.make_cache(), "a")
        self.app.processEvents()
        self.assertEqual(card.offset_button.geometry().right() + 2, card.close_button.x() - 1)
        self.assertEqual(card.help_button.geometry().right() + 2, card.offset_button.x() - 1)
        card.offset_button.click()
        self.assertTrue(card.offset_button.isHidden())
        self.assertFalse(card.offset_editor.isHidden())
        changes = []
        card.time_offset_changed.connect(lambda source, previous: changes.append((source, previous)))
        card.offset_editor.setValue(2.5)
        self.assertEqual(card.time_offset, 2.5)
        self.assertEqual(changes, [(card, 0.0)])
        card.offset_editor.editingFinished.emit()  # Also emitted when the field loses focus.
        self.assertTrue(card.offset_editor.isHidden())
        self.assertFalse(card.offset_button.isHidden())
        self.assertIn("+2.500 s", card.offset_button.toolTip())
        self.assertEqual(card.workspace_state()["time_offset"], 2.5)

    def test_image_cards_have_no_time_offset(self):
        card = ImagePlotWidget(self.directory, self.directory / "missing.png")
        self.widgets.append(card)
        self.assertFalse(card.supports_time_offset)
        self.assertTrue(card.offset_button.isHidden())

    def test_axis_labels_show_shared_time(self):
        axis = TimeAxisItem()
        axis.set_representation(TimeAxisItem.LOG_ELAPSED)
        axis.set_display_offset(2.0)
        self.assertEqual(axis.tickStrings([10.0], 1.0, 1.0), [axis._elapsed_text(12.0, 1.0)])
        # Ticks fall on round shared times, which are offset in the card's own time.
        for _spacing, values in axis.tickValues(0.0, 10.0, 500):
            for value in values:
                steps = (value + 2.0) / _spacing
                self.assertAlmostEqual(steps, round(steps), places=6)

    def test_offset_moves_card_view_markers_and_navigator_bar(self):
        workspace = self.workspace()
        path = self.make_cache()
        first = workspace.add_line_plot(path, "a")
        second = workspace.add_line_plot(path, "b")
        self.app.processEvents()
        entries = []
        workspace.plot_navigator_changed.connect(entries.append)
        workspace.set_linked_view_range(LOG_START + 10, LOG_START + 30)

        second.set_time_offset(5.0)
        # The linked card keeps the shared range on screen; its own data moves under it.
        lower, upper = second.plot_view_box().viewRange()[0]
        self.assertAlmostEqual(lower, LOG_START + 5, places=3)
        self.assertAlmostEqual(upper, LOG_START + 25, places=3)
        lower, upper = first.plot_view_box().viewRange()[0]
        self.assertAlmostEqual(lower, LOG_START + 10, places=3)
        bar = next(entry for entry in entries[-1] if entry.plot is second)
        self.assertAlmostEqual(bar.full_start, LOG_START + 5, places=3)
        self.assertEqual(bar.offset, 5.0)

        # Markers convert between shared time and each card's own time.
        self.assertAlmostEqual(workspace._marker_value_for_plot(second, LOG_START + 20), LOG_START + 15)
        self.assertAlmostEqual(workspace._marker_timestamp(second, LOG_START + 15), LOG_START + 20)
        workspace.set_markers_linked(True)
        first.set_vertical_marker(LOG_START + 20)
        self.assertAlmostEqual(second.marker_values()[0], LOG_START + 15)
        second.set_time_offset(6.0)
        self.assertAlmostEqual(second.marker_values()[0], LOG_START + 14)

        # A trimmed range moves with the card and filters the same data.
        workspace.set_plot_navigator_range(second, LOG_START + 20, LOG_START + 40)
        self.assertEqual(second.series_plot.data_time_range, (LOG_START + 14, LOG_START + 34))
        workspace.shift_plot_time_offset(second, -1.0)
        self.assertEqual(workspace._plot_navigator_ranges[second], (LOG_START + 19, LOG_START + 39))
        self.assertEqual(second.series_plot.data_time_range, (LOG_START + 14, LOG_START + 34))
        workspace.reset_plot_navigator_range(second)
        self.assertIsNone(second.series_plot.data_time_range)
        workspace.reset_plot_time_offset(second)
        self.assertEqual(second.time_offset, 0.0)

    def test_restored_card_keeps_its_offset(self):
        workspace = self.workspace()
        card = workspace.add_line_plot(self.make_cache(), "a")
        card.set_time_offset(-3.0)
        state = workspace.workspace_state()
        restored = self.workspace()
        restored.restore_workspace_state(state)
        self.assertEqual(restored.plots()[0].time_offset, -3.0)


    def test_multi_series_offsets_only_the_selected_series(self):
        from src.gui.plots.series import MarkerDataLoader

        workspace = self.workspace()
        path = self.make_cache()
        card = workspace.add_multi_plot([(path, "a"), (path, "b")])
        self.app.processEvents()
        plot = card.series_plot
        first, second = (Path(path), "a"), (Path(path), "b")
        entries = []
        workspace.plot_navigator_changed.connect(entries.append)

        # With a series selected, the Offset controls and a navigator drag move only that series.
        plot.select_series(second)
        self.assertIn("/b", card.offset_button.toolTip())
        card.offset_button.click()
        card.offset_editor.setValue(3.0)
        self.assertEqual(card.time_offset, 0.0)
        self.assertEqual(plot.series_offsets, {second: 3.0})
        workspace.shift_plot_time_offset(card, 1.0)
        self.assertEqual(plot.series_offsets, {second: 4.0})
        self.wait_for(lambda: plot.additional_series[0].data is not None
                      and abs(float(plot.additional_series[0].data.x[0]) - (LOG_START + 4)) < 1e-6)
        self.assertAlmostEqual(float(plot._current_data.x[0]), LOG_START)
        self.assertTrue(card.has_time_offset())
        bar = entries[-1][-1]
        self.assertTrue(bar.offset_set)
        self.assertAlmostEqual(bar.full_stop, LOG_START + 99 + 4)

        # Each series is read at the marker minus its own offset.
        self.assertEqual(plot.entry_time_offsets(), [0.0, 4.0])
        samples = []
        loader = MarkerDataLoader(1, LOG_START + 10, [first, second], plot, x_offsets=[0.0, 4.0])
        loader.loaded.connect(lambda _id, _x, values: samples.extend(values))
        loader.run()
        # Series b holds index + 1 at each second: at its own time 6 s the value is 7.
        self.assertEqual([sample.converted_value for sample in samples], [10.0, 7.0])

        # With nothing selected, the controls move the whole card again.
        plot.select_series(None)
        workspace.shift_plot_time_offset(card, 2.0)
        self.assertEqual(card.time_offset, 2.0)
        self.assertEqual(plot.series_offsets, {second: 4.0})

        state = workspace.workspace_state()
        restored = self.workspace()
        restored.restore_workspace_state(state)
        copy = restored.plots()[0]
        self.assertEqual(copy.time_offset, 2.0)
        self.assertEqual(copy.series_plot.series_offsets, {second: 4.0})

        workspace.reset_plot_time_offset(card)
        self.assertEqual(card.time_offset, 0.0)
        self.assertEqual(plot.series_offsets, {})
        self.assertFalse(card.has_time_offset())

class NavigatorOffsetDragTests(_QtTestCase):
    def mouse(self, navigator, kind, x, y, button=Qt.MouseButton.LeftButton):
        point = QPointF(x, y)
        buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else button
        event = QMouseEvent(kind, point, point, button if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton,
                            buttons, Qt.KeyboardModifier.NoModifier)
        {QEvent.Type.MouseButtonPress: navigator.canvas.mousePressEvent,
         QEvent.Type.MouseMove: navigator.canvas.mouseMoveEvent,
         QEvent.Type.MouseButtonRelease: navigator.canvas.mouseReleaseEvent}[kind](event)

    def navigator(self):
        navigator = PlotNavigatorWidget()
        self.widgets.append(navigator)
        navigator.resize(1052, 120)
        navigator.show()
        self.app.processEvents()
        navigator.set_plot_area_margins(26, navigator.canvas.width() - 1026)  # 1000 px for 0..100 s
        navigator.set_entries([PlotNavigatorEntry("p0", 0.0, 100.0, "p0", "line"),
                               PlotNavigatorEntry("p1", 0.0, 50.0, "p1", "line", 0.0, 50.0)])
        return navigator

    def test_sideways_drag_sets_offset_and_vertical_drag_reorders(self):
        navigator = self.navigator()
        offsets, reorders = [], []
        navigator.offset_dragged.connect(lambda plot, delta: offsets.append((plot, delta)))
        navigator.reorder_requested.connect(lambda *args: reorders.append(args))
        y = 9 + navigator.ROW_HEIGHT + 3  # Second bar.
        self.mouse(navigator, QEvent.Type.MouseButtonPress, 276, y)
        self.mouse(navigator, QEvent.Type.MouseMove, 326, y + 1)
        # The change so far is shown beside the pointer while dragging.
        self.assertEqual(navigator.canvas.offset_delta_text(), "Δ +5.000 s")
        self.assertEqual(navigator._offset_cursor, QPointF(326, y + 1))
        navigator.canvas.grab().save(str(self.directory / "drag.png"))
        self.mouse(navigator, QEvent.Type.MouseButtonRelease, 326, y + 1)
        self.assertIsNone(navigator._offset_cursor)
        self.assertEqual(len(offsets), 1)
        self.assertEqual(offsets[0][0], "p1")
        self.assertAlmostEqual(offsets[0][1], 5.0, places=6)
        self.assertEqual(reorders, [])
        self.assertIsNone(navigator._frozen_range)

        self.mouse(navigator, QEvent.Type.MouseButtonPress, 276, y)
        self.mouse(navigator, QEvent.Type.MouseMove, 278, 2)
        self.mouse(navigator, QEvent.Type.MouseButtonRelease, 278, 2)
        self.assertEqual(len(offsets), 1)
        self.assertEqual(reorders, [("p1", "p0", False)])

    def test_trimmed_entries_offer_reset_selection(self):
        self.assertFalse(PlotNavigatorEntry("p", 0.0, 10.0, "p", "line", 0.0, 10.0).trimmed)
        self.assertTrue(PlotNavigatorEntry("p", 2.0, 10.0, "p", "line", 0.0, 10.0).trimmed)


if __name__ == "__main__":
    unittest.main()
