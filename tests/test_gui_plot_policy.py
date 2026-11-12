"""Shared defaults, multi-selection menus and native Qt drag routing."""

import json
import unittest
from types import SimpleNamespace
from unittest import mock

import h5py
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, qInstallMessageHandler
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtTest import QTest
from tests.qt_wait import qt_wait
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu, QTreeWidgetItem, QVBoxLayout, QWidget

from src.data.plot_policy import PlotSource, SELECTION_MIME_TYPE, activation_uses_card
from src.jobs.plot_drop import inspect_menu_sources, inspect_sources
from src.gui.plots.open_menu import populate_plot_menu
from src.gui.navigator.widget import CopyableTreeWidget, DataNavigatorWidget, ITEM_TYPE_ROLE, ROW_TYPE_GROUP
from src.app.lodgazilla import LODGaZillaWindow
from tests.test_gui_grouped_legends import GroupedLegendTests
from tests.plot_menu_helpers import wait_for_plot_menu


class PlotPolicyGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    tearDown = GroupedLegendTests.tearDown
    wait_loaded = GroupedLegendTests.wait_loaded
    prime = GroupedLegendTests.prime

    def setUp(self):
        GroupedLegendTests.setUp(self)
        with h5py.File(self.path, "a") as cache:
            for name in ("a", "b", "c", "d", "e", "f", "state"):
                cache.copy("series/GNSS/latitude", "series/" + name)
                cache["series/" + name].attrs["navigator_name"] = name
            cache["series/state"].attrs["unit"] = "bool"
        self.sources = [PlotSource("series", self.path, name) for name in "abcdef"]

    def mime(self, sources):
        mime = QMimeData()
        mime.setData(SELECTION_MIME_TYPE, json.dumps([source.payload() for source in sources]).encode())
        return mime

    def send(self, kind, point, mime, target=None):
        target = target or self.workspace.viewport()
        event_type = {"enter": QDragEnterEvent, "move": QDragMoveEvent, "drop": QDropEvent}[kind]
        event = event_type(QPointF(point) if kind == "drop" else point, Qt.DropAction.CopyAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(target, event)
        return event

    def test_shared_default_opening_and_coordinate_order(self):
        for count, kind in ((1, "multi"), (2, "xy"), (3, "xyz"), (4, "multi")):
            with self.subTest(count=count):
                cards = self.workspace.open_plot_inputs(inspect_sources(self.sources[:count]))
                self.assertEqual([card.drop_card_type for card in cards], [kind])
                self.assertEqual(cards[0].series, [source.entry for source in self.sources[:count]])
                self.workspace.remove_plot(cards[0])
        for names, kind in ((self.names[:2][::-1], "gnss"), (self.names[::-1], "gnss_3d")):
            card = self.workspace.open_plot_inputs(inspect_sources(
                [PlotSource("series", self.path, name) for name in names]))[0]
            self.wait_loaded(card)
            self.assertEqual(card.drop_card_type, kind)
            self.assertEqual([axis.name for axis in card.grouped_series.groups[0].axes], self.names[:len(names)])
            self.workspace.remove_plot(card)

    def test_explicit_xy_keeps_three_series_in_one_card_with_empty_axis(self):
        cards = self.workspace.open_plot_inputs(inspect_sources(self.sources[:3]), "xy")
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.wait_loaded(card)
        self.assertEqual(len(card.grouped_series.groups), 2)
        self.assertEqual(card.grouped_series.groups[1].axes[0].name, "c")
        self.assertIsNone(card.grouped_series.groups[1].axes[1])
        self.assertIsNotNone(card.results[0])
        self.assertIsNone(card.results[1])

    def test_single_activation_uses_multi_for_numeric_and_state(self):
        for name in ("a", "state"):
            card = self.workspace.add_new_plot(self.path, name)
            self.assertEqual(card.drop_card_type, "multi")
            self.assertEqual(card.series, [(self.path, name)])
            self.workspace.remove_plot(card)

    def test_frequency_keeps_all_selection_with_media_and_state_fallbacks(self):
        sources = self.sources[:2] + [PlotSource("series", self.path, "state"),
                                     PlotSource("image", self.path.with_suffix(".png"), folder=self.path.parent)]
        cards = self.workspace.open_plot_inputs(inspect_sources(sources), "frequency")
        self.assertEqual([card.drop_card_type for card in cards], ["frequency", "frequency", "multi", "image"])
        self.assertEqual([card.series for card in cards[:3]], [[source.entry] for source in sources[:3]])

    def test_menu_active_then_default_and_partial_choice_opens_everything(self):
        menu = QMenu()
        sources = self.sources[:2] + [PlotSource("series", self.path, "state")]
        populate_plot_menu(menu, self.workspace, sources)
        wait_for_plot_menu(menu)
        self.assertEqual(menu.actions()[0].text(), "Active card")
        self.assertFalse(menu.actions()[0].isEnabled())
        self.assertEqual(menu.actions()[1].text(), "Multi-series (default)")
        frequency = next(action for action in menu.actions() if action.data() == "frequency")
        self.assertIn("2 of 3", frequency.toolTip())
        frequency.trigger()
        self.assertEqual([card.drop_card_type for card in self.workspace.plots()], ["frequency", "frequency", "multi"])
        menu.deleteLater()

    def test_multi_selection_drag_leave_and_default_drop_open_both_series(self):
        card = self.workspace.add_line_plot(*self.sources[2].entry)
        self.app.processEvents()
        mime = self.mime(self.sources[:2])
        self.prime(mime)
        target = card.series_plot.plot_widget.viewport()
        messages = []
        previous = qInstallMessageHandler(lambda _kind, _context, message: messages.append(message))
        try:
            self.assertTrue(self.send("enter", target.rect().center(), mime, target).isAccepted())
            self.assertTrue(self.send("move", target.rect().center(), mime, target).isAccepted())
            QApplication.sendEvent(target, QDragLeaveEvent())
            # Qt can deliver the matching leave to the parent graphics view too.
            QApplication.sendEvent(card.series_plot.plot_widget, QDragLeaveEvent())
            self.app.processEvents()
            self.prime(mime)
            point = card.mapTo(self.workspace.viewport(), QPoint(100, card.height()))
            self.send("enter", point, mime)
            self.assertTrue(self.send("drop", point, mime).isAccepted())
        finally:
            qInstallMessageHandler(previous)
        self.assertFalse([message for message in messages if "dragLeaveEvent" in message], messages)
        new_card = next(plot for plot in self.workspace.plots() if plot is not card)
        self.wait_loaded(new_card)
        self.assertEqual(new_card.drop_card_type, "xy")
        self.assertEqual(new_card.series, [source.entry for source in self.sources[:2]])

    def test_early_multi_drop_before_metadata_is_ready(self):
        mime = self.mime(self.sources[:3])
        point = QPoint(100, 100)
        self.send("enter", point, mime)
        self.assertTrue(self.send("drop", point, mime).isAccepted())
        for _ in range(300):
            if self.workspace.plots():
                break
            qt_wait(10)
        self.assertEqual(len(self.workspace.plots()), 1)
        card = self.workspace.plots()[0]
        self.wait_loaded(card)
        self.assertEqual(card.drop_card_type, "xyz")
        self.assertEqual(card.series, [source.entry for source in self.sources[:3]])

    def test_multi_selection_can_drop_into_existing_card(self):
        card = self.workspace.add_xy_plot(self.sources[0].entry)
        self.wait_loaded(card)
        self.app.processEvents()
        mime = self.mime(self.sources[1:])
        self.prime(mime)
        point = card.mapTo(self.workspace.viewport(), QPoint(20, card.height() // 2))
        self.send("enter", point, mime)
        self.assertTrue(self.send("drop", point, mime).isAccepted())
        self.wait_loaded(card)
        self.assertEqual(self.workspace.plots(), [card])
        self.assertEqual(len(card.grouped_series.groups), 4)
        self.assertIsNone(card.grouped_series.groups[-1].axes[-1])
        self.assertEqual(card.series, [source.entry for source in self.sources])

    def test_navigator_drag_contains_every_selected_source(self):
        tree = CopyableTreeWidget()
        tree.navigator = SimpleNamespace(selected_plot_sources=lambda: self.sources[:2])
        def inspect_drag(drag, actions):
            self.assertEqual(actions, Qt.DropAction.CopyAction)
            self.assertTrue(self.controller._read_payload(drag.mimeData()))
            self.assertEqual(self.controller.payload["items"], tuple(self.sources[:2]))
            return Qt.DropAction.IgnoreAction
        with mock.patch("src.gui.navigator.widget.exec_data_drag", side_effect=inspect_drag):
            tree.startDrag(Qt.DropAction.CopyAction)
        tree.deleteLater()

    def wait_until(self, condition):
        for _ in range(300):
            if condition():
                return
            qt_wait(10)
        self.fail("Plot action did not complete")

    def test_double_click_fills_active_group_and_falls_back_for_incompatible_card(self):
        card = self.workspace.add_xy_plot(self.sources[0].entry)
        self.wait_loaded(card)
        self.workspace.activate_series(*self.sources[1].entry)
        self.wait_until(lambda: card.grouped_series.axis(1) is not None)
        self.wait_loaded(card)
        self.assertEqual(self.workspace.plots(), [card])
        self.assertEqual(card.series, [source.entry for source in self.sources[:2]])
        self.assertIsNotNone(card.results[0])
        image = self.workspace.add_image_plot(self.path.parent, self.path.with_suffix('.png'))
        self.workspace.activate_series(*self.sources[2].entry)
        self.wait_until(lambda: len(self.workspace.plots()) == 3)
        self.assertEqual(self.workspace.active_plot.drop_card_type, 'multi')
        self.assertEqual(self.workspace.active_plot.series, [self.sources[2].entry])
        self.assertIn(image, self.workspace.plots())

    def test_double_click_uses_active_card_only_when_it_is_the_default_card(self):
        items = inspect_sources(self.sources[:1])
        self.assertTrue(activation_uses_card(items, "multi"))
        self.assertFalse(activation_uses_card(items, "line"))
        self.assertFalse(activation_uses_card(items, "frequency"))
        self.assertFalse(activation_uses_card(items, "xy"))
        self.assertTrue(activation_uses_card(items, "xy", has_empty_axis=True))
        self.assertFalse(activation_uses_card(items, "line", has_empty_axis=True))
        # An unreadable series fits no card, so it never goes into the active card.
        self.assertFalse(activation_uses_card(inspect_sources([PlotSource("series", self.path, "missing")]), "multi"))

        multi = self.workspace.add_new_plot(*self.sources[0].entry)
        self.workspace.activate_series(*self.sources[1].entry)
        self.wait_until(lambda: len(multi.series) == 2)
        self.assertEqual(self.workspace.plots(), [multi])
        for add_card in (lambda: self.workspace.add_line_plot(*self.sources[2].entry),
                         lambda: self.workspace.add_xy_plot(self.sources[2].entry, self.sources[3].entry)):
            card = add_card()
            count = len(self.workspace.plots())
            self.workspace.activate_series(*self.sources[4].entry)
            self.wait_until(lambda: len(self.workspace.plots()) == count + 1)
            self.assertEqual(self.workspace.active_plot.drop_card_type, "multi")
            self.assertEqual(self.workspace.active_plot.series, [self.sources[4].entry])
            self.assertEqual(len(card.series), 2 if card.accepts_axis_drop else 1)

    def test_active_menu_is_first_and_enabled_only_for_compatible_card(self):
        items = inspect_sources(self.sources[:2])
        card = self.workspace.add_line_plot(*self.sources[2].entry)
        menu = QMenu()
        populate_plot_menu(menu, self.workspace, self.sources[:2])
        wait_for_plot_menu(menu)
        self.assertEqual(menu.actions()[0].text(), 'Active card')
        self.assertTrue(menu.actions()[0].isEnabled())
        menu.actions()[0].trigger()
        self.assertEqual(self.workspace.plots(), [card])
        self.assertEqual(card.series, [self.sources[index].entry for index in (2, 0, 1)])
        menu.deleteLater()
        image = self.workspace.add_image_plot(self.path.parent, self.path.with_suffix('.png'))
        self.assertFalse(self.workspace.active_card_accepts(items))
        menu = QMenu()
        populate_plot_menu(menu, self.workspace, self.sources[:2])
        wait_for_plot_menu(menu)
        self.assertFalse(menu.actions()[0].isEnabled())
        menu.deleteLater()

    def test_series_group_activation_does_not_plot_and_menu_has_only_plot(self):
        group = QTreeWidgetItem(['Series group'])
        group.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_GROUP)
        navigator = mock.Mock()
        DataNavigatorWidget.on_item_activated(navigator, group, 0)
        self.assertEqual(navigator.mock_calls, [])
        navigator.tree.viewport.return_value.mapToGlobal.return_value = QPoint()
        window = SimpleNamespace(navigator=navigator, plot_workspace=self.workspace)
        menu = QMenu()
        def choose(*_args):
            wait_for_plot_menu(menu)
            self.assertEqual([action.text() for action in menu.actions()], ['Multi-series (default)'])
            menu.actions()[0].trigger()
        with mock.patch('src.app.lodgazilla.QMenu', return_value=menu), mock.patch.object(menu, 'exec', side_effect=choose):
            LODGaZillaWindow.show_series_group_context_menu(window, [source.entry for source in self.sources], QPoint())
        self.wait_until(lambda: len(self.workspace.plots()) == 1)
        card = self.workspace.plots()[0]
        self.assertEqual(card.drop_card_type, 'multi')
        self.assertEqual(card.series, [source.entry for source in self.sources])
        menu.deleteLater()

    def test_close_all_cancels_pending_activation(self):
        from src.jobs.plot_drop import SelectionInspection
        with mock.patch('src.jobs.plot_drop.QThreadPool.globalInstance'):
            self.workspace.activate_series(*self.sources[0].entry)
        inspection = self.workspace.findChild(SelectionInspection)
        self.assertIsNotNone(inspection)
        self.workspace.close_all_plots()
        inspection._finished(inspect_sources(self.sources[:1]))
        self.assertEqual(self.workspace.plots(), [])

    def test_empty_workspace_click_clears_active_card_and_next_activation_opens_new(self):
        card = self.workspace.add_new_plot(*self.sources[0].entry)
        self.app.processEvents()
        viewport = self.workspace.viewport()
        point = QPoint(100, viewport.height() - 10)
        self.assertIsNone(viewport.childAt(point))
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=point)
        self.assertIsNone(self.workspace.active_plot)
        self.assertFalse(card.property('activePlot'))
        menu = QMenu()
        populate_plot_menu(menu, self.workspace, self.sources[1:2])
        wait_for_plot_menu(menu)
        self.assertFalse(menu.actions()[0].isEnabled())
        menu.deleteLater()
        self.workspace.activate_series(*self.sources[1].entry)
        self.wait_until(lambda: len(self.workspace.plots()) == 2)
        self.assertEqual(card.series, [self.sources[0].entry])
        self.assertEqual(self.workspace.active_plot.series, [self.sources[1].entry])

    def test_card_clicks_preserve_activation_and_closing_other_card_preserves_selection(self):
        first = self.workspace.add_new_plot(*self.sources[0].entry)
        second = self.workspace.add_new_plot(*self.sources[1].entry)
        self.app.processEvents()
        self.workspace.activate_plot(None)
        # The frame's press propagates to the stack/viewport after activation.
        QTest.mouseClick(first, Qt.MouseButton.LeftButton, pos=QPoint(1, first.height() // 2))
        self.assertIs(self.workspace.active_plot, first)
        self.assertTrue(first.property('activePlot'))
        self.workspace.remove_plot(second)
        self.assertIs(self.workspace.active_plot, first)
        second = self.workspace.add_new_plot(*self.sources[1].entry)
        self.workspace.activate_plot(None)
        self.workspace.remove_plot(second)
        self.assertIsNone(self.workspace.active_plot)
        self.assertFalse(first.property('activePlot'))

    def test_escape_clears_active_card_from_workspace_and_card_focus(self):
        card = self.workspace.add_new_plot(*self.sources[0].entry)
        self.workspace.activateWindow()
        for target in (self.workspace, card.series_plot.plot_widget):
            with self.subTest(target=type(target).__name__):
                self.workspace.activate_plot(card)
                target.setFocus()
                self.app.processEvents()
                self.assertTrue(target.hasFocus())
                QTest.keyClick(target, Qt.Key.Key_Escape)
                self.assertIsNone(self.workspace.active_plot)
                self.assertFalse(card.property('activePlot'))
                self.assertEqual(self.workspace.plots(), [card])

    def test_escape_outside_workspace_or_in_popup_keeps_active_card(self):
        card = self.workspace.add_new_plot(*self.sources[0].entry)
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.addWidget(self.workspace)
        outside = QLineEdit()
        layout.addWidget(outside)
        menu = QMenu(self.workspace)
        menu.addAction('Example')
        try:
            host.show()
            host.activateWindow()
            outside.setFocus()
            self.app.processEvents()
            self.assertTrue(outside.hasFocus())
            QTest.keyClick(outside, Qt.Key.Key_Escape)
            self.assertIs(self.workspace.active_plot, card)
            self.workspace.setFocus()
            menu.popup(self.workspace.mapToGlobal(QPoint(20, 20)))
            self.app.processEvents()
            self.assertTrue(menu.isVisible())
            QTest.keyClick(menu, Qt.Key.Key_Escape)
            self.assertFalse(menu.isVisible())
            self.assertIs(self.workspace.active_plot, card)
        finally:
            menu.deleteLater()
            self.workspace.setParent(None)
            host.close()
            host.deleteLater()

    def test_group_gnss_actions_open_only_coordinates_and_plot_keeps_all_series(self):
        sources = [PlotSource("series", self.path, name) for name in self.names] + self.sources
        menu = QMenu()
        populate_plot_menu(menu, self.workspace, sources, group=True)
        wait_for_plot_menu(menu)
        self.assertEqual([action.text() for action in menu.actions()],
                         ['Multi-series (default)', 'Map', '3D Map'])
        for index, kind, count in ((1, 'gnss', 2), (2, 'gnss_3d', 3)):
            menu.actions()[index].trigger()
            card = self.workspace.active_plot
            self.wait_loaded(card)
            self.assertEqual(card.drop_card_type, kind)
            self.assertEqual(card.series, [source.entry for source in sources[:count]])
            self.assertEqual(len(card.grouped_series.groups), 1)
            self.workspace.remove_plot(card)
        menu.actions()[0].trigger()
        self.wait_until(lambda: len(self.workspace.plots()) == 1)
        self.assertEqual(self.workspace.active_plot.drop_card_type, 'multi')
        self.assertEqual(self.workspace.active_plot.series, [source.entry for source in sources])
        menu.deleteLater()

    def test_individual_series_menu_finds_coordinate_siblings(self):
        source = PlotSource('series', self.path, self.names[1])
        menu = QMenu()
        populate_plot_menu(menu, self.workspace, [source])
        wait_for_plot_menu(menu)
        self.assertEqual(menu.actions()[0].data(), 'active')
        self.assertEqual(menu.actions()[1].text(), 'Multi-series (default)')
        action = next(action for action in menu.actions() if action.data() == 'detected_gnss_3d')
        action.trigger()
        card = self.workspace.active_plot
        self.wait_loaded(card)
        self.assertEqual(card.series, self.entries)
        menu.deleteLater()

    def test_gnss_menu_requires_complete_numeric_coordinates(self):
        with h5py.File(self.path, 'a') as cache:
            cache['series/' + self.names[2]].attrs['unit'] = 'bool'
        menu = QMenu()
        populate_plot_menu(menu, self.workspace, [PlotSource('series', self.path, self.names[0])])
        wait_for_plot_menu(menu)
        actions = [action.data() for action in menu.actions()]
        self.assertIn('detected_gnss', actions)
        self.assertNotIn('detected_gnss_3d', actions)
        menu.deleteLater()
        with h5py.File(self.path, 'a') as cache:
            del cache['series/' + self.names[1]]
        _, coordinates = inspect_menu_sources([PlotSource('series', self.path, self.names[0])])
        self.assertEqual(coordinates, {})

    def test_gnss_sibling_detection_respects_separate_group_metadata(self):
        def add_series(cache, storage, group_name, name):
            cache.copy('series/' + self.names[0], 'series/' + storage)
            cache['series/' + storage].attrs.update(navigator_group=group_name, navigator_name=name)
        with h5py.File(self.path, 'a') as cache:
            add_series(cache, 'lat_a', 'Position/A', 'LAT_deg')
            add_series(cache, 'lon_b', 'Position/B', 'LON_deg')
            add_series(cache, 'alt_b', 'Position/B', 'Elevation_m')
            add_series(cache, 'speed_a', 'Position/A', 'speed')
        sources = [PlotSource('series', self.path, 'Position/A/speed')]
        _, coordinates = inspect_menu_sources(sources)
        self.assertEqual(coordinates, {})
        with h5py.File(self.path, 'a') as cache:
            add_series(cache, 'lon_a', 'Position/A', 'LON_deg')
        _, coordinates = inspect_menu_sources(sources)
        self.assertEqual(list(coordinates), ['gnss'])
        self.assertEqual([item.source.name for item in coordinates['gnss']],
                         ['Position/A/LAT_deg', 'Position/A/LON_deg'])
        self.assertEqual({item.coordinate_group for item in coordinates['gnss']}, {'position/a'})


if __name__ == "__main__":
    unittest.main()
