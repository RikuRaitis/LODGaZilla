from tests.plugin_fixtures import with_sample_importer
import os
import sys
import tempfile
import unittest
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import h5py
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QEvent, QThreadPool  # noqa: E402
from tests.qt_wait import qt_wait  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.app.lodgazilla import (  # noqa: E402
    ActiveImport,
    PluginJobRequest,
    ImportRequest,
    LODGaZillaWindow,
    PlotWorkspace,
    combined_process_progress,
)
from src.gui.plots.line import LinePlotWidget  # noqa: E402
from src.jobs.import_coordinator import ImportJobCoordinator, request_conflicts_with_active  # noqa: E402


class ImportPluginSelectionTests(unittest.TestCase):
    @with_sample_importer
    def test_folder_import_uses_current_gui_plugins_when_disk_settings_disagree(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                (folder / "capture.csv").write_text(
                    "time,value\n-0.001,2.5\n0,3.5\n",
                    encoding="utf-8",
                )
                # Empty explicitly disables all plugins; it must not fall back to disk.
                selection = {"sample": True} if enabled else {}
                coordinator = ImportJobCoordinator(1)
                window = SimpleNamespace(
                    import_coordinator=coordinator,
                    plugin_manager=SimpleNamespace(enabled=selection),
                    clear_imported_children=mock.Mock(),
                    event_timer=mock.Mock(),
                    max_background_processes=1,
                    available_import_slots=coordinator.available_slots,
                    active_imports={},
                    internal_logger=mock.Mock(),
                    settings_path=None,
                )
                with mock.patch("src.app.lodgazilla.multiprocessing.get_context") as get_context, \
                     mock.patch("src.app.lodgazilla.start_process_tree"):
                    LODGaZillaWindow.start_import_process(window, ImportRequest(folder, False, 1))
                context = get_context.return_value
                context.Event.return_value.is_set.return_value = False
                invocation = context.Process.call_args.kwargs
                # Later GUI changes cannot mutate an already-created import request.
                selection.clear()
                with mock.patch("src.jobs.import_data.load_settings_section", return_value={
                    "enabled": {"sample": not enabled},
                }), mock.patch("src.app.lodgazilla.h5_cache_logger", return_value=logging.getLogger("test")):
                    invocation["target"](*invocation["args"])
                finished = [
                    call.args[0] for call in context.Queue.return_value.put.call_args_list
                    if call.args[0][0] == "finished"
                ]
                self.assertEqual(len(finished), 1)
                self.assertEqual(finished[0][1:3], (int(enabled), 0))
                self.assertEqual((folder / "capture.h5").exists(), enabled)
                if enabled:
                    with h5py.File(folder / "capture.h5", "r") as cache:
                        np.testing.assert_allclose(cache["series/Test/signal/value"][:], [2.5, 3.5])


class CombinedProcessProgressTests(unittest.TestCase):
    def test_parallel_progress_uses_the_longest_remaining_process(self):
        self.assertEqual(
            combined_process_progress([
                ("first.bin", 25, 100, "0:12"),
                ("second.bin", 50, 100, "0:06"),
            ]),
            (25, 100, "0:12"),
        )


class ImportSchedulingTests(unittest.TestCase):
    def test_finishing_a_job_releases_slots_for_the_next_queued_job(self):
        coordinator = ImportJobCoordinator(2)
        active = ActiveImport(Path("first"), False, 2, None, None, "H5 cache")  # type: ignore[arg-type]
        import_id = coordinator.reserve_id()
        coordinator.register_active(import_id, active)
        queued = ImportRequest(Path("second"), False, 2)
        coordinator.enqueue(queued)

        self.assertIsNone(coordinator.pop_next_runnable())
        self.assertIs(coordinator.finish(import_id), active)
        self.assertEqual(coordinator.pop_next_runnable(), queued)

    def test_recursive_scope_conflict_and_mixed_operations_follow_policy(self):
        coordinator = ImportJobCoordinator(4)
        active = ActiveImport(Path("root"), True, 1, None, None, "H5 cache")  # type: ignore[arg-type]
        coordinator.register_active(coordinator.reserve_id(), active)

        self.assertFalse(coordinator.can_start(ImportRequest(Path("root/child"), False, 1)))
        self.assertTrue(coordinator.can_start(PluginJobRequest(Path("root/child"), False, 1)))
    def test_coordinator_starts_independent_queued_work_behind_a_conflict(self):
        coordinator = ImportJobCoordinator(4)
        active = ActiveImport(Path("active"), True, 2, None, None, "H5 cache")  # type: ignore[arg-type]
        coordinator.register_active(coordinator.reserve_id(), active)
        conflicting = ImportRequest(Path("active/child"), False, 1)
        independent = PluginJobRequest(Path("active/child"), False, 1)
        coordinator.enqueue(conflicting)
        coordinator.enqueue(independent)

        self.assertEqual(coordinator.pop_next_runnable(), independent)
        self.assertEqual(list(coordinator.queue), [conflicting])

    def test_h5_and_csv_work_can_share_an_overlapping_folder(self):
        folder = Path("shared-folder")
        active_h5 = ActiveImport(folder, False, 2, None, None, "H5 cache")  # type: ignore[arg-type]

        self.assertFalse(request_conflicts_with_active(PluginJobRequest(folder, False, 1), active_h5))

    def test_two_h5_requests_for_an_overlapping_folder_still_conflict(self):
        folder = Path("shared-folder")
        active_h5 = ActiveImport(folder, False, 2, None, None, "H5 cache")  # type: ignore[arg-type]

        self.assertTrue(request_conflicts_with_active(ImportRequest(folder, False, 1), active_h5))


class PlotWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_plot_cards_can_collect_series_reorder_and_close(self):
        workspace = PlotWorkspace(default_plot_heights={"line": 200})
        first = workspace.add_line_plot(Path("first.h5"), "acc_x")
        second = workspace.add_line_plot(Path("second.h5"), "gyro_x")

        workspace.add_series_to_plot(first, Path("first.h5"), "acc_y")
        self.assertEqual(first.series, [(Path("first.h5"), "acc_x"), (Path("first.h5"), "acc_y")])
        self.assertIs(workspace.active_plot, first)

        workspace.reorder_plot(second, first, after=False)
        self.assertIs(workspace.splitter.widget(0), second)

        workspace.set_markers_linked(True)
        first.set_vertical_marker(123.0)
        second_marker = second.series_plot.plot_widget.vertical_marker
        self.assertIsNotNone(second_marker)
        first.set_vertical_marker(234.0)
        self.assertIs(second.series_plot.plot_widget.vertical_marker, second_marker)
        self.assertEqual(second_marker.value(), 234.0)
        first.clear_vertical_marker()
        self.assertIsNone(second.series_plot.plot_widget.vertical_marker)

        with mock.patch.object(second, "dispose", wraps=second.dispose) as dispose:
            workspace.remove_plot(second)
        dispose.assert_called_once_with()
        self.assertEqual(workspace.splitter.count(), 1)
        self.assertIs(workspace.active_plot, first)

    def test_second_marker_and_selection_follow_the_marker_link(self):
        workspace = PlotWorkspace(default_plot_heights={"line": 200})
        first = workspace.add_line_plot(Path("first.h5"), "acc_x")
        second = workspace.add_line_plot(Path("second.h5"), "gyro_x")
        navigator_markers = []
        workspace.global_second_marker_changed.connect(navigator_markers.append)
        canvas = second.series_plot.plot_widget

        workspace.set_markers_linked(False)
        first.set_vertical_marker(100.0)
        first.set_vertical_marker(200.0, 1)
        self.assertIsNone(canvas.second_marker)
        self.assertEqual(navigator_markers[-1], 200.0)
        # Unlinked, the Plot navigator shows the active card's markers.
        workspace.activate_plot(second)
        self.assertIsNone(navigator_markers[-1])
        workspace.activate_plot(first)
        self.assertEqual(navigator_markers[-1], 200.0)

        workspace.set_markers_linked(True)
        first.set_vertical_marker(100.0)
        first.set_vertical_marker(300.0, 1)
        self.assertEqual(second.marker_values(), (100.0, 300.0))
        self.assertEqual(canvas.selection_range(), (100.0, 300.0))
        first.clear_vertical_marker(1)
        self.assertIsNone(canvas.second_marker)
        self.assertIsNone(canvas.selection_range())
        self.assertIsNone(navigator_markers[-1])

        # Plot navigator right click and right double-click place and remove the second marker.
        workspace.place_second_marker_from_plot_navigator(250.0)
        self.assertEqual(first.marker_values()[1], 250.0)
        self.assertEqual(second.marker_values()[1], 250.0)
        workspace.clear_second_marker_from_plot_navigator()
        self.assertEqual(first.marker_values()[1], None)
        self.assertEqual(second.marker_values()[1], None)

    def test_marker_settings_defaults_and_style_apply_to_open_cards(self):
        from src.app.settings import SERIES_COLOR_DEFAULTS, load_lodgazilla_settings, sanitize_plot_settings
        from src.gui.plots.base import set_series_colors
        from src.gui.plots import markers
        from src.gui.settings_widget import SettingsEditorWidget
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            defaults = load_lodgazilla_settings(path)
            self.assertEqual((defaults["link_markers"], defaults["snap_vertical_marker_to_nearest_point"],
                              defaults["marker_info_display"]), (False, False, "active"))
            plot = sanitize_plot_settings({"first_marker_color": "red", "second_marker_color": "#00FF00",
                                           "marker_width": 40})
            self.assertEqual((plot["first_marker_color"], plot["second_marker_color"], plot["marker_width"]),
                             ("#ff3b30", "#00ff00", 10))
            editor = SettingsEditorWidget(path)
            self.addCleanup(editor.deleteLater)
            editor.first_marker_color_button.set_color("#123456")
            editor.marker_width_spin.setValue(3)
            editor.marker_opacity_spin.setValue(50)
            editor.selection_color_button.set_color("#ff00ff")
            editor.selection_opacity_spin.setValue(40)
            self.assertEqual(editor.series_color_buttons[0].color(), SERIES_COLOR_DEFAULTS[0])
            editor.series_color_buttons[0].set_color("#112233")
            editor.series_color_buttons[1].set_color("not a color")  # Ignored.
            editor.save()
            saved = sanitize_plot_settings(json.loads(path.read_text(encoding="utf-8"))["plot"])
            self.assertEqual((saved["first_marker_color"], saved["marker_width"]), ("#123456", 3))
            self.assertEqual(saved["series_colors"], ["#112233", *SERIES_COLOR_DEFAULTS[1:]])
            # Invalid entries are skipped; an empty list falls back to the defaults.
            self.assertEqual(sanitize_plot_settings({"series_colors": ["#112233", "not a color"]})["series_colors"],
                             ["#112233"])
            self.assertEqual(sanitize_plot_settings({"series_colors": []})["series_colors"],
                             list(SERIES_COLOR_DEFAULTS))
        self.addCleanup(markers.set_marker_style, "#ff3b30", "#00c853", 1)
        self.addCleanup(markers.set_selection_style, "#2196f3", 22)
        self.addCleanup(set_series_colors, SERIES_COLOR_DEFAULTS)
        workspace = PlotWorkspace(default_plot_heights={"line": 200})
        card = workspace.add_line_plot(Path("first.h5"), "acc_x")
        card.set_vertical_marker(1.0)
        card.set_vertical_marker(2.0, 1)
        workspace.apply_marker_settings(saved)
        pen = card.series_plot.plot_widget.vertical_marker.pen
        self.assertEqual((pen.color().name(), pen.width(), pen.color().alpha()), ("#123456", 3, 128))
        brush = card.series_plot.plot_widget.selection_region.brush.color()
        self.assertEqual((brush.name(), brush.alpha()), ("#ff00ff", 102))
        # Bands drawn over a line use twice the selection opacity.
        self.assertEqual(markers.selection_rgba(2.0), (255, 0, 255, 204))
        # Series added from now on use the new default colors.
        self.assertEqual(workspace.add_line_plot(Path("second.h5"), "acc_y").series_plot.series_colors(), ["#112233"])

    def test_time_axis_on_all_cards_the_bottom_card_or_none(self):
        from src.app.settings import sanitize_lodgazilla_settings
        from src.gui.plots.navigator import PlotNavigatorWidget
        self.assertEqual(sanitize_lodgazilla_settings({})["time_axis_display"], "none")
        self.assertEqual(sanitize_lodgazilla_settings({"time_axis_display": "x"})["time_axis_display"], "none")
        workspace = PlotWorkspace(default_plot_heights={"line": 200})
        workspace.set_time_axis_display("all")
        cards = [workspace.add_line_plot(Path(f"{index}.h5"), "acc_x") for index in range(3)]
        shown = lambda: [card.series_plot.plot_item.getAxis("bottom").isVisible() for card in cards]
        self.assertEqual(shown(), [True, True, True])
        workspace.set_time_axis_display("bottom")
        self.assertEqual(shown(), [False, False, True])
        # The axis follows the card that is lowest now.
        workspace.reorder_plot(cards[2], cards[0], after=False)
        self.assertEqual(shown(), [False, True, False])
        workspace.set_time_axis_display("none")
        self.assertEqual(shown(), [False, False, False])
        navigator = PlotNavigatorWidget()
        self.addCleanup(navigator.deleteLater)
        modes = []
        navigator.time_axis_display_changed.connect(modes.append)
        navigator._time_axis_buttons["bottom"].click()
        navigator.set_time_axis_display("none")
        self.assertEqual(modes, ["bottom"])
        self.assertTrue(navigator._time_axis_buttons["none"].isChecked())
        self.assertFalse(navigator._time_axis_buttons["all"].icon().isNull())

    def test_plot_navigator_places_and_removes_both_markers(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from src.gui.plots.navigator import PlotNavigatorEntry, PlotNavigatorWidget
        navigator = PlotNavigatorWidget()
        self.addCleanup(navigator.deleteLater)
        navigator.resize(400, 80)
        navigator.set_entries([PlotNavigatorEntry(object(), 0.0, 100.0, "card", "line")])
        navigator.show()
        self.app.processEvents()
        events = []
        navigator.marker_placed.connect(lambda value: events.append(("first", round(value))))
        navigator.marker_cleared.connect(lambda: events.append(("first", None)))
        navigator.second_marker_placed.connect(lambda value: events.append(("second", round(value))))
        navigator.second_marker_cleared.connect(lambda: events.append(("second", None)))
        # Above the bars is empty canvas.
        point = QPointF(navigator.x_for_time(50.0), 2)

        def send(kind, button):
            buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else button
            QApplication.sendEvent(navigator.canvas, QMouseEvent(kind, point, point, button, buttons,
                                                                 Qt.KeyboardModifier.NoModifier))

        for button in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            send(QEvent.Type.MouseButtonPress, button)
            send(QEvent.Type.MouseButtonRelease, button)
            send(QEvent.Type.MouseButtonDblClick, button)
        self.assertEqual(events, [("first", 50), ("first", None), ("second", 50), ("second", None)])
        navigator.set_marker(20.0)
        navigator.set_second_marker(60.0)
        image = navigator.canvas.grab().toImage()
        selection = image.pixelColor(int(navigator.x_for_time(40.0)), 2)
        outside = image.pixelColor(int(navigator.x_for_time(80.0)), 2)
        self.assertNotEqual(selection, outside)

    def test_new_plot_keeps_configured_height(self):
        workspace = PlotWorkspace(default_plot_heights={"line": 200})
        workspace.resize(500, 400)
        workspace.show()
        self.app.processEvents()
        plot = workspace.add_line_plot(Path("data.h5"), "acc_x")
        self.app.processEvents()

        self.assertEqual(workspace.splitter.height(), 200)
        self.assertGreater(workspace.splitter.width(), 0)
        self.assertTrue(plot.isVisible())
        workspace.resize_plot(plot, 60)
        self.assertEqual(workspace.splitter.height(), 260)

    def test_card_types_use_independent_heights_without_resizing_existing_cards(self):
        temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(temp_dir) / "data.h5"
        with h5py.File(path, "w") as h5_file:
            for name in ("acc_x", "acc_y"):
                group = h5_file.create_group(f"series/{name}")
                group.attrs["time_dataset"] = "time_unix"
                group.attrs["navigator_name"] = name
                group.create_dataset("time_unix", data=np.asarray([1.0, 2.0, 3.0]))
                group.create_dataset("value", data=np.asarray([0.0, 1.0, 0.0]))
        workspace = PlotWorkspace(default_plot_heights={
            "line": 240, "multi": 300, "event": 350, "xy": 400, "image": 450,
        })
        def close_workspace():
            # Grouped cards load asynchronously; drain reads and queued paint
            # updates before deleting native views and the HDF5 fixture.
            QThreadPool.globalInstance().waitForDone(3000)
            qt_wait(100)
            workspace.close_all_plots()
            workspace.close()
            workspace.deleteLater()
            # Finish deferred Qt deletion before removing the HDF5 fixture.
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

        self.addCleanup(close_workspace)
        workspace.resize(600, 500)
        workspace.show()
        entry = (path, "acc_x")
        first = workspace.add_line_plot(*entry)
        workspace.resize_plot(first, 25)
        workspace.add_multi_plot([entry])
        workspace.add_event_state_plot(*entry)
        workspace.add_xy_plot(entry, (entry[0], "acc_y"))
        workspace.add_image_plot(Path("."), Path("missing.png"))
        self.app.processEvents()
        self.assertEqual(workspace.splitter.sizes(), [265, 300, 350, 400, 450])

        # Applying settings affects subsequent cards, including timelines taller
        # than the old hard-coded 130-pixel cap, without resetting resized cards.
        workspace.default_plot_heights = {**workspace.default_plot_heights, "line": 330, "event": 420}
        workspace.add_line_plot(*entry)
        workspace.add_event_state_plot(*entry)
        self.app.processEvents()
        self.assertEqual(workspace.splitter.sizes(), [265, 300, 350, 400, 450, 330, 420])

    def test_time_axis_representation_is_linked_by_default(self):
        workspace = PlotWorkspace(default_plot_heights={"line": 200})
        first = workspace.add_line_plot(Path("first.h5"), "acc_x")
        second = workspace.add_line_plot(Path("second.h5"), "gyro_x")

        first.set_time_axis_representation("log_elapsed")

        self.assertEqual(second.time_axis_representation(), "log_elapsed")
        workspace.set_time_axis_format_linked(False)
        first.set_time_axis_representation("real_datetime")
        self.assertEqual(second.time_axis_representation(), "log_elapsed")

    def test_line_plot_renders_h5_series_instead_of_placeholder_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                group = h5_file.create_group("series/acc_x")
                group.attrs["time_dataset"] = "time_unix"
                group.attrs["navigator_name"] = "acc_x"
                group.create_dataset("time_unix", data=np.asarray([1.0, 2.0, 3.0]))
                group.create_dataset("value", data=np.asarray([4.0, 5.0, 6.0]))
            plot = LinePlotWidget(h5_path, "acc_x")

        self.assertFalse(plot.error_label.isVisible())
        self.assertEqual(plot.series_plot.series_name, "acc_x")

    def test_line_plot_overlays_multiple_series_and_allows_color_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "data.h5"
            with h5py.File(h5_path, "w") as h5_file:
                for name, values in (("acc_x", [4.0, 5.0, 6.0]), ("acc_y", [7.0, 8.0, 9.0])):
                    group = h5_file.create_group(f"series/{name}")
                    group.attrs["time_dataset"] = "time_unix"
                    group.attrs["navigator_name"] = name
                    group.create_dataset("time_unix", data=np.asarray([1.0, 2.0, 3.0]))
                    group.create_dataset("value", data=np.asarray(values))
            plot = LinePlotWidget(h5_path, "acc_x")
            plot.add_series(h5_path, "acc_y")

        self.assertEqual(len(plot.series_plot.additional_series), 1)
        self.assertEqual(len(plot.series_plot.additional_series[0].data.x), 3)
        plot.series_plot.set_series_color(1, "#123456")
        self.assertEqual(plot.series_plot.series_colors()[1], "#123456")
        self.assertEqual(len(plot.legend.rows), 2)
