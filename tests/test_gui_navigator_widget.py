import sys
import tempfile
import unittest
import json
import os
from pathlib import Path
from unittest import mock

import h5py
import numpy as np
from PySide6.QtCore import QItemSelectionModel, QRect, Qt, QTimer
from PySide6.QtTest import QTest
from tests.qt_wait import qt_wait
from PySide6.QtWidgets import QApplication, QAbstractItemView, QLineEdit, QTreeWidget, QTreeWidgetItem


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from src.app.settings import (  # noqa: E402
    NavigatorControlSettings,
    load_navigator_control_settings,
    save_navigator_control_settings,
)
from src.data.folder_info import read_info_file  # noqa: E402
from src.gui.navigator.widget import (  # noqa: E402
    DataNavigatorWidget,
    DIAGNOSTIC_CLIPPING,
    DIAGNOSTIC_SAMPLE_LOSS,
    ITEM_DIAGNOSTIC_FLAGS_ROLE,
    ITEM_INFO_EMPTY_ROLE,
    ITEM_INFO_KEY_ROLE,
    ITEM_PATH_ROLE,
    ITEM_TYPE_ROLE,
    ROW_TYPE_DETAILED_INFO,
    ROW_TYPE_FOLDER,
    ROW_TYPE_GROUP,
    ROW_TYPE_INFO,
    ROW_TYPE_SERIES,
    ROW_TYPE_VIRTUAL,
    PHYSICAL_DATA_COLUMN,
    PHYSICAL_VALUE_COLUMN,
    columns_for_category,
    column_category_for_item,
    info_column_id,
    tree_item_sort_key,
)
from src.data.navigator_data import (  # noqa: E402
    H5Series,
    SeriesMetadata,
    filter_folders_by_metadata,
    folder_info_group_path,
    format_sample_rate,
    format_log_length,
    h5_time_range_seconds_from_file,
    metadata_values_for_key,
    read_h5_navigator_data,
    scan_folder_info_files,
)
from src.gui.navigator.settings import (  # noqa: E402
    COLUMN_CATEGORY_FOLDER,
    COLUMN_CATEGORY_INFO,
    COLUMN_CATEGORY_SERIES,
    COLUMN_DATA,
    COLUMN_INFO_PAIR_VALUE,
    COLUMN_LOG_LENGTH,
    COLUMN_SAMPLE_COUNT,
    COLUMN_SAMPLE_RATE,
    COLUMN_UNIT,
    load_column_settings,
    save_column_settings,
)


class NavigatorFolderTargetsTests(unittest.TestCase):
    def test_virtual_groups_cover_their_visible_folders_once(self):
        QApplication.instance() or QApplication([])
        # Hiding a row needs a tree.
        tree = QTreeWidget()

        def item(item_type, path="", parent=None):
            row = QTreeWidgetItem(parent if parent is not None else tree)
            row.setData(0, ITEM_TYPE_ROLE, item_type)
            row.setData(0, ITEM_PATH_ROLE, path)
            return row

        year = item(ROW_TYPE_VIRTUAL)
        month = item(ROW_TYPE_VIRTUAL, parent=year)
        first = item(ROW_TYPE_FOLDER, "data/a", month)
        item(ROW_TYPE_SERIES, "data/a/x.h5", first)
        item(ROW_TYPE_FOLDER, "data/b", month)
        item(ROW_TYPE_FOLDER, "data/hidden", month).setHidden(True)
        folders = DataNavigatorWidget.folders_for_items([year, first])
        self.assertEqual(folders, [Path("data/a"), Path("data/b")])


class NavigatorSeriesDetailsTests(unittest.TestCase):
    def test_read_h5_series_calculates_sample_rate_from_first_and_last_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "sample.h5"
            with h5py.File(h5_path, "w") as h5_file:
                group = h5_file.create_group("series").create_group("acc_x")
                group.attrs["display_name"] = "acc_x"
                group.attrs["navigator_name"] = "acc_x"
                group.attrs["navigator_group"] = "IMU"
                group.attrs["unit"] = "m/s2"
                group.attrs["visible"] = True
                group.attrs["sample_count"] = 4
                group.attrs["time_dataset"] = "time_ms"
                group.attrs["time_unit"] = "ms"
                group.create_dataset("time_ms", data=np.asarray([1000.0, 1010.0, 1020.0, 1030.0]))
                group.create_dataset("value", data=np.asarray([1.0, 2.0, 3.0, 4.0]))

            data = read_h5_navigator_data(h5_path)

        self.assertIsNone(data.error)
        self.assertEqual(len(data.series), 1)
        self.assertAlmostEqual(data.series[0].sample_rate_hz, 100.0)

    def test_audio_files_do_not_count_towards_a_folders_log_length(self):
        # Audio times count from its video's start (0 s), not from 1970: a span from
        # there to a real recording would be decades long.
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            for name, start, audio in (("watch.h5", 1_800_000_000.0, False), ("run.mp4.audio.h5", 0.0, True)):
                with h5py.File(folder / name, "w") as h5_file:
                    if audio:
                        h5_file.attrs["media_kind"] = "audio"
                    group = h5_file.create_group("series").create_group("x")
                    group.attrs["time_dataset"] = "time_unix"
                    group.create_dataset("time_unix", data=np.asarray([start, start + 60.0]))
                    group.create_dataset("value", data=np.asarray([1.0, 2.0]))
            data = {path: read_h5_navigator_data(path) for path in (folder / "watch.h5", folder / "run.mp4.audio.h5")}
            lengths = DataNavigatorWidget._folder_log_lengths_from_h5_data(data)

        self.assertIsNone(data[folder / "run.mp4.audio.h5"].time_range)
        self.assertEqual(data[folder / "watch.h5"].time_range, (1_800_000_000.0, 1_800_000_060.0))
        self.assertEqual(lengths[folder.resolve()], format_log_length(60.0))

    def test_read_h5_series_uses_root_series_index_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            h5_path = Path(temp_dir) / "sample.h5"
            with h5py.File(h5_path, "w") as h5_file:
                h5_file.attrs["series_index_json"] = json.dumps([
                    {
                        "name": "IMU/acc_x",
                        "display_name": "acc_x",
                        "navigator_name": "acc_x",
                        "navigator_group": "IMU",
                        "unit": "m/s2",
                        "sample_count": 4,
                        "sample_rate_hz": 100.0,
                        "visible": True,
                    }
                ])

            data = read_h5_navigator_data(h5_path)

        self.assertIsNone(data.error)
        self.assertEqual(
            data.series,
            [H5Series("IMU/acc_x", "acc_x", "acc_x", "IMU", "m/s2", 4, 100.0, True)],
        )

    def test_folder_log_length_uses_root_time_range_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            h5_path = folder / "sample.h5"
            with h5py.File(h5_path, "w") as h5_file:
                h5_file.attrs["time_start_seconds"] = 10.0
                h5_file.attrs["time_end_seconds"] = 72.0

            with h5py.File(h5_path, "r") as h5_file:
                time_range = h5_time_range_seconds_from_file(h5_file)

        self.assertEqual(time_range, (10.0, 72.0))
        self.assertEqual(format_log_length(time_range[1] - time_range[0]), "1:02")

    def test_format_sample_rate_keeps_compact_units(self):
        self.assertEqual(format_sample_rate(750.0), "750Hz")
        self.assertEqual(format_sample_rate(12.5), "12.5Hz")


class NavigatorSettingsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_leaving_the_folder_field_unchanged_keeps_the_tree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = make_sample_navigator_folder(Path(temp_dir))
            other = Path(temp_dir) / "Other"
            other.mkdir()
            navigator = DataNavigatorWidget(folder, settings_path=Path(temp_dir) / "settings.json")
            try:
                with mock.patch.object(navigator, "refresh_tree") as refresh:
                    navigator.folder_edit.editingFinished.emit()
                    refresh.assert_not_called()
                    navigator.folder_edit.setText(str(other))
                    navigator.folder_edit.editingFinished.emit()
                    refresh.assert_called_once()
            finally:
                navigator.close()

    def test_double_click_series_group_only_toggles_expansion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = make_sample_navigator_folder(Path(temp_dir))
            navigator = DataNavigatorWidget(folder, settings_path=Path(temp_dir) / "settings.json")
            try:
                navigator.resize(1000, 700)
                navigator.show()
                navigator.tree.expandAll()
                group = first_item_of_type(navigator, ROW_TYPE_GROUP)
                group.setExpanded(False)
                navigator.tree.scrollToItem(group)
                self.app.processEvents()
                activated = []
                navigator.series_activated.connect(lambda *args: activated.append(args))
                navigator.gnss_group_activated.connect(lambda *args: activated.append(args))
                point = navigator.tree.visualRect(
                    navigator.tree.indexFromItem(group, PHYSICAL_DATA_COLUMN)
                ).intersected(navigator.tree.viewport().rect()).center()
                QTest.mouseClick(navigator.tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
                QTest.mouseDClick(navigator.tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
                self.app.processEvents()
                self.assertTrue(group.isExpanded())
                self.assertEqual(activated, [])
            finally:
                navigator.close()
                navigator.deleteLater()

    def test_series_group_right_click_exposes_child_series(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = make_sample_navigator_folder(Path(temp_dir))
            navigator = DataNavigatorWidget(folder, settings_path=None)
            try:
                navigator.resize(1000, 700)
                navigator.show()
                navigator.tree.expandAll()
                self.app.processEvents()
                group = first_item_of_type(navigator, ROW_TYPE_GROUP)
                navigator.tree.scrollToItem(group)
                self.app.processEvents()
                received = []
                navigator.series_group_context_menu_requested.connect(lambda entries, point: received.append(entries))
                navigator.on_context_menu_requested(navigator.tree.visualItemRect(group).center())
                self.assertEqual(len(received), 1)
                self.assertTrue(received[0])
                self.assertTrue(all(path.suffix == '.h5' and name for path, name in received[0]))
            finally:
                navigator.close()
                navigator.deleteLater()

    def test_navigator_controls_use_and_save_shared_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = Path(temp_dir) / "Data"
            data_folder.mkdir()
            settings_path = Path(temp_dir) / "app_settings.json"

            navigator = DataNavigatorWidget(data_folder, settings_path=settings_path)
            navigator.controls.hide_irrelevant_button.click()

            settings = load_navigator_control_settings(settings_path)

        self.assertEqual(
            settings,
            NavigatorControlSettings(
                hide_irrelevant_series=False,
                recent_folders=(str(data_folder.resolve()),),
            ),
        )

    def test_navigator_control_settings_can_be_reloaded_without_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = Path(temp_dir) / "Data"
            data_folder.mkdir()
            settings_path = Path(temp_dir) / "app_settings.json"
            navigator = DataNavigatorWidget(data_folder, settings_path=settings_path)

            save_navigator_control_settings(
                settings_path,
                NavigatorControlSettings(
                    align_columns_of_different_type=True,
                ),
            )
            navigator.reload_navigator_control_settings()

        self.assertEqual(
            navigator.navigator_control_settings,
            NavigatorControlSettings(
                align_columns_of_different_type=True,
            ),
        )

    def test_refresh_tree_updates_series_columns_from_recreated_h5_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            h5_path = data_folder / "First" / "sample.h5"
            navigator = DataNavigatorWidget(data_folder, settings_path=None)

            with h5py.File(h5_path, "r+") as h5_file:
                index = json.loads(h5_file.attrs["series_index_json"])
                index[0]["navigator_metadata"] = [
                    {"id": "new_value", "title": "New value", "format": "number", "value": 42},
                ]
                h5_file.attrs["series_index_json"] = json.dumps(index)

            navigator.refresh_tree()
            navigator.tree.setCurrentItem(
                first_item_of_type(navigator, ROW_TYPE_SERIES),
                PHYSICAL_VALUE_COLUMN,
            )

        self.assertEqual(
            [column.column_id for column in navigator.logical_columns_for_category(COLUMN_CATEGORY_SERIES)],
            [COLUMN_DATA, "new_value"],
        )

    def test_ui_only_refresh_reuses_h5_and_folder_info_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            navigator = DataNavigatorWidget(data_folder, settings_path=None)

            with mock.patch("src.jobs.navigator_snapshot.read_h5_navigator_data") as h5_reader, mock.patch(
                "src.jobs.navigator_snapshot.read_existing_info_file"
            ) as info_reader:
                navigator.refresh_tree()

        h5_reader.assert_not_called()
        info_reader.assert_not_called()

    def test_tree_uses_data_and_value_physical_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))

            navigator = DataNavigatorWidget(data_folder, settings_path=None)
            folder_item = first_item_of_type(navigator, ROW_TYPE_FOLDER)
            series_item = first_item_of_type(navigator, ROW_TYPE_SERIES)

        self.assertEqual(navigator.tree.columnCount(), 2)
        self.assertTrue(navigator.tree.isHeaderHidden())
        self.assertEqual(navigator.tree.headerItem().text(PHYSICAL_DATA_COLUMN), "Data")
        self.assertEqual(navigator.tree.headerItem().text(PHYSICAL_VALUE_COLUMN), "Value")
        self.assertEqual(folder_item.text(PHYSICAL_DATA_COLUMN), "First")
        self.assertEqual(folder_item.text(PHYSICAL_VALUE_COLUMN), "")
        self.assertEqual(series_item.text(PHYSICAL_DATA_COLUMN), "acc_x")
        self.assertEqual(series_item.text(PHYSICAL_VALUE_COLUMN), "")

    def test_copy_uses_selected_logical_value_cell(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            navigator = DataNavigatorWidget(data_folder, settings_path=None)
            series_item = first_item_of_type(navigator, ROW_TYPE_SERIES)

            navigator.tree.setCurrentItem(series_item, PHYSICAL_VALUE_COLUMN)
            navigator._selected_logical_column_id = COLUMN_SAMPLE_RATE

            copied = navigator.tree.selected_plain_text()

        self.assertEqual(copied, "100Hz")

    def test_uses_most_recent_folder_when_no_initial_folder_is_given(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            settings_path = Path(temp_dir) / "app_settings.json"
            save_navigator_control_settings(
                settings_path,
                NavigatorControlSettings(recent_folders=(str(data_folder),)),
            )

            navigator = DataNavigatorWidget(None, settings_path=settings_path)

            self.assertEqual(navigator.data_folder(), data_folder.resolve())

    def test_single_click_selects_and_copies_logical_value_cell(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            navigator = DataNavigatorWidget(data_folder, settings_path=None)
            series_item = first_item_of_type(navigator, ROW_TYPE_SERIES)
            parent = series_item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            navigator.resize(900, 500)
            navigator.show()
            QApplication.processEvents()

            value_rect = navigator.tree.visualRect(
                navigator.tree.indexFromItem(series_item, PHYSICAL_VALUE_COLUMN)
            )
            sample_rate_rect = next(
                rect
                for column, rect in navigator.value_column_sections(series_item, value_rect)
                if column.column_id == COLUMN_SAMPLE_RATE
            )
            self.assertIs(
                navigator.tree.itemFromIndex(navigator.tree.indexAt(sample_rate_rect.center())),
                series_item,
            )
            QTest.mouseClick(
                navigator.tree.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                sample_rate_rect.center(),
            )
            QApplication.processEvents()

            current_item = navigator.tree.currentItem()
            self.assertIsNotNone(current_item)
            self.assertEqual(current_item.data(0, ITEM_TYPE_ROLE), ROW_TYPE_SERIES)
            self.assertEqual(navigator.tree.currentColumn(), PHYSICAL_DATA_COLUMN)
            self.assertEqual(navigator._selected_logical_column_id, COLUMN_SAMPLE_RATE)
            self.assertEqual(navigator.tree.selected_plain_text(), "100Hz")
            navigator.close()

    def test_hover_tracks_only_the_logical_value_cell(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            navigator = DataNavigatorWidget(data_folder, settings_path=None)
            series_item = first_item_of_type(navigator, ROW_TYPE_SERIES)
            parent = series_item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            navigator.resize(900, 500)
            navigator.show()
            QApplication.processEvents()

            value_rect = navigator.tree.visualRect(
                navigator.tree.indexFromItem(series_item, PHYSICAL_VALUE_COLUMN)
            )
            sample_count_rect = next(
                rect
                for column, rect in navigator.value_column_sections(series_item, value_rect)
                if column.column_id == COLUMN_SAMPLE_COUNT
            )
            QTest.mouseMove(navigator.tree.viewport(), sample_count_rect.center())
            QApplication.processEvents()

            self.assertEqual(navigator._hovered_logical_column_id, COLUMN_SAMPLE_COUNT)
            navigator.close()

    def test_horizontal_scrollbar_uses_the_widest_row_type_configuration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            navigator = DataNavigatorWidget(data_folder, settings_path=None)
            navigator.column_settings.profiles[COLUMN_CATEGORY_SERIES].widths[COLUMN_SAMPLE_RATE] = 900
            navigator.resize(420, 360)
            navigator.show()
            QApplication.processEvents()
            navigator.update_physical_value_column_width()
            QApplication.processEvents()

            self.assertGreaterEqual(
                navigator.tree.header().sectionSize(PHYSICAL_VALUE_COLUMN),
                navigator.required_value_column_width(),
            )
            self.assertGreater(navigator.tree.horizontalScrollBar().maximum(), 0)
            navigator.close()

    def test_selecting_a_different_row_type_preserves_horizontal_scroll_position(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            navigator = DataNavigatorWidget(data_folder, settings_path=None)
            navigator.column_settings.profiles[COLUMN_CATEGORY_SERIES].widths[COLUMN_SAMPLE_RATE] = 900
            navigator.resize(420, 360)
            navigator.show()
            navigator.update_physical_value_column_width()
            QApplication.processEvents()

            scrollbar = navigator.tree.horizontalScrollBar()
            expected_position = min(100, scrollbar.maximum())
            self.assertGreater(expected_position, 0)
            scrollbar.setValue(expected_position)

            navigator.tree.setCurrentItem(
                first_item_of_type(navigator, ROW_TYPE_SERIES),
                PHYSICAL_VALUE_COLUMN,
            )
            navigator.restore_horizontal_scroll_position(expected_position)
            qt_wait(10)

            self.assertEqual(navigator.active_column_category, COLUMN_CATEGORY_SERIES)
            self.assertGreaterEqual(scrollbar.maximum(), expected_position)
            self.assertEqual(scrollbar.value(), expected_position)
            navigator.close()

    def test_clicking_logical_value_cell_preserves_horizontal_scroll_position(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            navigator = DataNavigatorWidget(data_folder, settings_path=None)
            series_item = first_item_of_type(navigator, ROW_TYPE_SERIES)
            parent = series_item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            navigator.column_settings.profiles[COLUMN_CATEGORY_SERIES].widths[COLUMN_SAMPLE_RATE] = 900
            navigator.resize(420, 360)
            navigator.show()
            navigator.update_physical_value_column_width()
            QApplication.processEvents()

            scrollbar = navigator.tree.horizontalScrollBar()
            expected_position = min(100, scrollbar.maximum())
            self.assertGreater(expected_position, 0)
            scrollbar.setValue(expected_position)
            QApplication.processEvents()

            value_rect = navigator.tree.visualRect(
                navigator.tree.indexFromItem(series_item, PHYSICAL_VALUE_COLUMN)
            )
            visible_sections = [
                (column, rect)
                for column, rect in navigator.value_column_sections(series_item, value_rect)
                if rect.intersects(navigator.tree.viewport().rect())
            ]
            self.assertTrue(visible_sections)
            clicked_column, clicked_rect = visible_sections[0]
            click_position = clicked_rect.intersected(navigator.tree.viewport().rect()).center()

            QTest.mouseClick(
                navigator.tree.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                click_position,
            )
            qt_wait(10)

            self.assertEqual(navigator._selected_logical_column_id, clicked_column.column_id)
            self.assertEqual(scrollbar.value(), expected_position)
            navigator.close()

    def test_clicking_sample_count_at_scroll_origin_does_not_hide_data_column(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            navigator = DataNavigatorWidget(data_folder, settings_path=None)
            series_item = first_item_of_type(navigator, ROW_TYPE_SERIES)
            parent = series_item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            navigator.column_settings.profiles[COLUMN_CATEGORY_SERIES].widths[COLUMN_SAMPLE_RATE] = 900
            navigator.resize(420, 360)
            navigator.show()
            navigator.update_physical_value_column_width()
            QApplication.processEvents()

            scrollbar = navigator.tree.horizontalScrollBar()
            self.assertGreater(scrollbar.maximum(), 0)
            scrollbar.setValue(0)
            value_rect = navigator.tree.visualRect(
                navigator.tree.indexFromItem(series_item, PHYSICAL_VALUE_COLUMN)
            )
            sample_count_rect = next(
                rect
                for column, rect in navigator.value_column_sections(series_item, value_rect)
                if column.column_id == COLUMN_SAMPLE_COUNT
            )
            navigator.tree.selectionModel().currentChanged.connect(
                lambda _current, _previous: QTimer.singleShot(
                    0,
                    lambda: scrollbar.setValue(scrollbar.maximum()),
                )
            )

            QTest.mouseClick(
                navigator.tree.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                sample_count_rect.center(),
            )
            qt_wait(10)

            self.assertEqual(navigator._selected_logical_column_id, COLUMN_SAMPLE_COUNT)
            self.assertEqual(scrollbar.value(), 0)
            navigator.close()

    def test_align_columns_setting_applies_active_widths_by_position(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            navigator = DataNavigatorWidget(data_folder, settings_path=None)
            series_item = first_item_of_type(navigator, ROW_TYPE_SERIES)
            navigator.column_settings.profiles[COLUMN_CATEGORY_SERIES].widths.update({
                COLUMN_SAMPLE_RATE: 91,
                COLUMN_SAMPLE_COUNT: 143,
            })
            navigator.navigator_control_settings = NavigatorControlSettings(
                align_columns_of_different_type=True,
            )

            sections = navigator.value_column_sections(series_item, QRect(0, 0, 500, 20))
            self.assertEqual(sections[0][1].width(), 78)
            self.assertEqual(sections[1][1].width(), 120)

            navigator.tree.setCurrentItem(series_item, PHYSICAL_VALUE_COLUMN)
            sections = navigator.value_column_sections(series_item, QRect(0, 0, 500, 20))
            self.assertEqual(sections[0][1].width(), 143)
            self.assertEqual(sections[1][1].width(), 91)

            navigator.resize_logical_column(COLUMN_SAMPLE_RATE, 111)
            folder_item = first_item_of_type(navigator, ROW_TYPE_FOLDER)
            sections = navigator.value_column_sections(folder_item, QRect(0, 0, 500, 20))
            self.assertEqual(sections[1][1].width(), 111)

    def test_logical_folder_value_edit_updates_info_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            navigator = DataNavigatorWidget(data_folder, settings_path=None)
            folder_item = first_item_of_type(navigator, ROW_TYPE_FOLDER)
            place_column = navigator.logical_column_for_id(info_column_id("Place"), COLUMN_CATEGORY_FOLDER)

            assert place_column is not None
            navigator.on_logical_cell_edited(folder_item, place_column, "Sea")
            folder = Path(str(folder_item.data(0, ITEM_PATH_ROLE)))
            info = read_info_file(folder)

        self.assertEqual(info.editable["place"], "Sea")

    def test_double_clicking_editable_logical_value_opens_inline_editor_without_scrolling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            navigator = DataNavigatorWidget(data_folder, settings_path=None)
            navigator.resize(900, 500)
            navigator.show()
            QApplication.processEvents()
            folder_item = first_item_of_type(navigator, ROW_TYPE_FOLDER)
            folder_item.setExpanded(True)
            value_rect = navigator.tree.visualRect(
                navigator.tree.indexFromItem(folder_item, PHYSICAL_VALUE_COLUMN)
            )
            place_rect = next(
                rect
                for column, rect in navigator.value_column_sections(folder_item, value_rect)
                if column.column_id == info_column_id("Place")
            )
            scrollbar = navigator.tree.horizontalScrollBar()
            original_scroll = scrollbar.value()

            QTest.mouseDClick(
                navigator.tree.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                place_rect.center(),
            )
            QApplication.processEvents()

            self.assertEqual(navigator.tree.state(), QAbstractItemView.State.EditingState)
            self.assertEqual(navigator._selected_logical_column_id, info_column_id("Place"))
            self.assertEqual(scrollbar.value(), original_scroll)
            QTest.keyClick(navigator.tree.viewport(), Qt.Key.Key_Escape)
            navigator.close()

    def test_missing_folder_metadata_can_be_added_with_inline_editor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            folder = data_folder / "Second"
            folder.mkdir()
            info_path = folder / "Second_info.json"
            original = '{"editable": {"session": "Dive"}, "readonly": {"serial number": "456"}}'
            info_path.write_text(original, encoding="utf-8")
            navigator = DataNavigatorWidget(data_folder, settings_path=None)
            self.addCleanup(navigator.close)
            navigator.resize(1000, 500)
            navigator.show()
            folder_item = next(
                item for item in navigator.tree_items()
                if item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_FOLDER
                and Path(str(item.data(0, ITEM_PATH_ROLE))) == folder.resolve()
            )
            parent = folder_item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            navigator.tree.setCurrentItem(folder_item, PHYSICAL_VALUE_COLUMN)
            navigator.tree.scrollToItem(folder_item)
            QApplication.processEvents()
            value_rect = navigator.tree.visualRect(
                navigator.tree.indexFromItem(folder_item, PHYSICAL_VALUE_COLUMN)
            )
            place_rect = next(
                rect for column, rect in navigator.value_column_sections(folder_item, value_rect)
                if column.column_id == info_column_id("place")
            )
            info_item = next(
                folder_item.child(index) for index in range(folder_item.childCount())
                if folder_item.child(index).data(0, ITEM_TYPE_ROLE) == ROW_TYPE_INFO
            )

            for commit in (False, True):
                QTest.mouseDClick(
                    navigator.tree.viewport(), Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier, place_rect.center(),
                )
                QApplication.processEvents()
                self.assertEqual(navigator.tree.state(), QAbstractItemView.State.EditingState)
                editor = QApplication.focusWidget()
                self.assertIsInstance(editor, QLineEdit)
                self.assertEqual(editor.text(), "")
                editor.setText("Melkutin")
                QTest.keyClick(editor, Qt.Key.Key_Return if commit else Qt.Key.Key_Escape)
                QApplication.processEvents()
                if not commit:
                    self.assertEqual(info_path.read_text(encoding="utf-8"), original)

            info = read_info_file(folder)
            self.assertEqual(info.editable, {"session": "Dive", "place": "Melkutin"})
            self.assertEqual(info.readonly["serial number"], "456")
            self.assertEqual(navigator.item_logical_value(folder_item, info_column_id("place")), "Melkutin")
            place_rows = [
                info_item.child(index) for index in range(info_item.childCount())
                if info_item.child(index).data(0, ITEM_INFO_KEY_ROLE) == "place"
            ]
            self.assertEqual(len(place_rows), 1)
            self.assertEqual(navigator.item_logical_value(place_rows[0], COLUMN_INFO_PAIR_VALUE), "Melkutin")
            self.assertEqual(read_info_file(data_folder / "First").editable["place"], "Lake")

    def test_folder_metadata_column_protects_readonly_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            navigator = DataNavigatorWidget(data_folder, settings_path=None)
            self.addCleanup(navigator.close)
            folder_item = first_item_of_type(navigator, ROW_TYPE_FOLDER)
            serial_column = navigator.logical_column_for_id(info_column_id("serial number"), COLUMN_CATEGORY_FOLDER)
            assert serial_column is not None
            self.assertFalse(navigator.is_folder_info_column_editable(folder_item, serial_column))
            navigator.on_logical_cell_edited(folder_item, serial_column, "changed")
            folder = data_folder / "First"
            self.assertEqual(read_info_file(folder).readonly["serial number"], "123")
            self.assertNotIn("serial number", read_info_file(folder).editable)

            # A column that was editable can become read-only before committing.
            place_column = navigator.logical_column_for_id(info_column_id("place"), COLUMN_CATEGORY_FOLDER)
            assert place_column is not None
            info_path = folder / "First_info.json"
            original = '{"editable": {}, "readonly": {"place": "Locked"}}'
            info_path.write_text(original, encoding="utf-8")
            navigator.on_logical_cell_edited(folder_item, place_column, "changed")
            self.assertEqual(info_path.read_text(encoding="utf-8"), original)

    def test_logical_detailed_info_value_edit_updates_info_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            navigator = DataNavigatorWidget(data_folder, settings_path=None)
            detail_item = detailed_info_item_for_key(navigator, "Place")
            value_column = navigator.logical_column_for_id(COLUMN_INFO_PAIR_VALUE, COLUMN_CATEGORY_INFO)

            assert value_column is not None
            navigator.on_logical_cell_edited(detail_item, value_column, "Pond")
            folder = Path(str(detail_item.data(0, ITEM_PATH_ROLE)))
            info = read_info_file(folder)

        self.assertEqual(info.editable["place"], "Pond")

    def test_logical_column_width_and_order_are_customisable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = make_sample_navigator_folder(Path(temp_dir))
            settings_path = Path(temp_dir) / "app_settings.json"
            navigator = DataNavigatorWidget(data_folder, settings_path=settings_path)
            navigator.tree.setCurrentItem(first_item_of_type(navigator, ROW_TYPE_SERIES), PHYSICAL_VALUE_COLUMN)

            navigator.resize_logical_column(COLUMN_DATA, 240)
            navigator.resize_logical_column(COLUMN_SAMPLE_RATE, 123)
            navigator.move_logical_column(COLUMN_SAMPLE_RATE, -1)
            navigator.save_column_settings()
            loaded = load_column_settings(settings_path)

        self.assertEqual(loaded.shared_widths[COLUMN_DATA], 240)
        self.assertEqual(loaded.profiles[COLUMN_CATEGORY_SERIES].widths[COLUMN_SAMPLE_RATE], 123)
        self.assertLess(
            loaded.profiles[COLUMN_CATEGORY_SERIES].order.index(COLUMN_SAMPLE_RATE),
            loaded.profiles[COLUMN_CATEGORY_SERIES].order.index(COLUMN_SAMPLE_COUNT),
        )


class NavigatorFolderSortingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data_folder = Path(temporary.name)
        for name, rank, duration in [("Alpha", "10", 62), ("Bravo", "2", 9), ("Charlie", "", 3600), ("Delta", "2", 10)]:
            folder = self.data_folder / name
            folder.mkdir()
            (folder / f"{name}_info.json").write_text(
                json.dumps({"editable": {"rank": rank}, "readonly": {"serial number": name}}),
                encoding="utf-8",
            )
            with h5py.File(folder / "sample.h5", "w") as h5_file:
                h5_file.attrs["time_start_seconds"] = 0.0
                h5_file.attrs["time_end_seconds"] = float(duration)
                h5_file.attrs["series_index_json"] = "[]"
        self.navigator = DataNavigatorWidget(self.data_folder, settings_path=None)
        self.addCleanup(self.navigator.close)
        self.navigator.resize(1000, 500)
        self.navigator.show()
        QApplication.processEvents()

    def root_names(self):
        tree = self.navigator.tree
        return [self.navigator.item_logical_value(tree.topLevelItem(index), COLUMN_DATA) for index in range(tree.topLevelItemCount())]

    def click_sort(self, column_id):
        navigator = self.navigator
        rect = next(rect for column, rect in navigator.logical_header_sections() if column.column_id == column_id)
        sort_rect = navigator.logical_header_sort_rect(rect)
        self.assertIsNotNone(sort_rect)
        QTest.mouseClick(navigator.logical_header, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, sort_rect.center())
        QApplication.processEvents()
        self.assertEqual(navigator.logical_header._dragged_column_id, "")
        self.assertEqual(navigator.logical_header._resizing_column_id, "")

    def test_header_cycles_sort_and_preserves_selection_and_subtrees(self):
        navigator = self.navigator
        tree = navigator.tree
        alpha = tree.topLevelItem(0)
        info = alpha.child(0)
        alpha.setExpanded(True)
        info.setExpanded(True)
        tree.setCurrentItem(alpha, PHYSICAL_DATA_COLUMN)
        tree.selectionModel().select(
            tree.indexFromItem(info.child(0), PHYSICAL_VALUE_COLUMN),
            QItemSelectionModel.SelectionFlag.Select,
        )
        selected = {(id(tree.itemFromIndex(index)), index.column()) for index in tree.selectedIndexes()}
        descendants = [id(item) for item in navigator.tree_items() if item.parent() is not None]
        original = self.root_names()
        column_order = navigator.current_column_order()
        self.click_sort(COLUMN_DATA)
        self.assertEqual(self.root_names(), original)
        self.click_sort(COLUMN_DATA)
        self.assertEqual(self.root_names(), list(reversed(original)))
        self.assertIs(tree.currentItem(), alpha)
        self.assertTrue(alpha.isExpanded())
        self.assertTrue(info.isExpanded())
        self.assertEqual(selected, {(id(tree.itemFromIndex(index)), index.column()) for index in tree.selectedIndexes()})
        self.assertEqual(sorted(descendants), sorted(id(item) for item in navigator.tree_items() if item.parent() is not None))
        self.assertEqual(navigator.current_column_order(), column_order)
        self.click_sort(COLUMN_DATA)
        self.assertEqual(self.root_names(), original)
        self.assertEqual(navigator.folder_sort_column_id, "")
        self.assertEqual(navigator.folder_sort_direction, 0)

    def test_numeric_metadata_duration_refresh_and_inline_edit(self):
        navigator = self.navigator
        rank_id = info_column_id("rank")
        self.click_sort(rank_id)
        self.assertEqual(self.root_names(), ["Bravo", "Delta", "Alpha", "Charlie"])
        self.click_sort(rank_id)
        self.assertEqual(self.root_names(), ["Alpha", "Bravo", "Delta", "Charlie"])
        self.click_sort(COLUMN_LOG_LENGTH)
        self.assertEqual(navigator.folder_sort_direction, 1)
        self.assertEqual(self.root_names(), ["Bravo", "Delta", "Alpha", "Charlie"])
        self.click_sort(COLUMN_LOG_LENGTH)
        self.assertEqual(self.root_names(), ["Charlie", "Alpha", "Delta", "Bravo"])
        navigator.refresh_tree()
        self.assertEqual(self.root_names(), ["Charlie", "Alpha", "Delta", "Bravo"])
        self.click_sort(rank_id)
        alpha = next(item for item in navigator.tree_items() if item.parent() is None and navigator.item_logical_value(item, COLUMN_DATA) == "Alpha")
        rank_column = navigator.logical_column_for_id(rank_id, COLUMN_CATEGORY_FOLDER)
        navigator.on_logical_cell_edited(alpha, rank_column, "1")
        QApplication.processEvents()
        self.assertEqual(self.root_names(), ["Alpha", "Bravo", "Delta", "Charlie"])
        self.assertEqual(read_info_file(self.data_folder / "Alpha").editable["rank"], "1")

    def test_sort_is_limited_to_folder_profile_and_root_folder_rows(self):
        navigator = self.navigator
        detail = detailed_info_item_for_key(navigator, "rank")
        navigator.tree.setCurrentItem(detail)
        self.assertEqual(navigator.active_column_category, COLUMN_CATEGORY_INFO)
        self.assertIsNone(navigator.logical_header_sort_rect(QRect(0, 0, 100, 24)))
        navigator.cycle_folder_sort(COLUMN_DATA)
        self.assertEqual(navigator.folder_sort_direction, 0)
        navigator.active_column_category = COLUMN_CATEGORY_SERIES
        self.assertIsNone(navigator.logical_header_sort_rect(QRect(0, 0, 100, 24)))
        navigator.active_column_category = COLUMN_CATEGORY_FOLDER

        # Grouped roots are virtual; neither they nor their nested folders move.
        navigator.group_by_combo.setCurrentText("rank")
        navigator.refresh_tree()
        before = [(id(item), id(item.parent()) if item.parent() is not None else None) for item in navigator.tree_items()]
        self.assertTrue(all(navigator.tree.topLevelItem(index).data(0, ITEM_TYPE_ROLE) == ROW_TYPE_VIRTUAL for index in range(navigator.tree.topLevelItemCount())))
        navigator.cycle_folder_sort(COLUMN_DATA)
        navigator.cycle_folder_sort(COLUMN_DATA)
        after = [(id(item), id(item.parent()) if item.parent() is not None else None) for item in navigator.tree_items()]
        self.assertEqual(before, after)


class NavigatorInfoGroupingTests(unittest.TestCase):
    def test_scan_folder_info_files_finds_editable_and_readonly_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = Path(temp_dir)
            first = data_folder / "First"
            second = data_folder / "Second"
            first.mkdir()
            second.mkdir()
            (first / "First_info.json").write_text(
                '{"editable": {"Session": "Dive 1"}, "readonly": {"serial number": "123"}}',
                encoding="utf-8",
            )
            (second / "Second_info.json").write_text(
                '{"editable": {"Session": "Dive 2", "Place": "Lake"}, "readonly": {"serial number": ""}}',
                encoding="utf-8",
            )

            info_index = scan_folder_info_files(data_folder)

        self.assertEqual(info_index.keys, {"session", "place", "serial number"})
        self.assertEqual(info_index.editable_keys, {"session", "place"})
        self.assertEqual(info_index.values_by_folder[first.resolve()]["session"], "Dive 1")
        self.assertEqual(info_index.values_by_folder[second.resolve()]["place"], "Lake")
        self.assertNotIn("serial number", info_index.editable_values_by_folder[first.resolve()])

    def test_folder_info_group_path_uses_value_empty_and_missing_groups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = Path(temp_dir)
            first = data_folder / "First"
            second = data_folder / "Second"
            first.mkdir()
            second.mkdir()
            values_by_folder = {
                first.resolve(): {"Session": "Dive 1", "serial number": ""},
            }

            self.assertEqual(folder_info_group_path(first, "Session", values_by_folder), ("Dive 1",))
            self.assertEqual(folder_info_group_path(first, "serial number", values_by_folder), ("(empty)",))
            self.assertEqual(folder_info_group_path(second, "Session", values_by_folder), ("(missing)",))

    def test_metadata_filter_supports_choices_free_text_and_empty_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = Path(temp_dir)
            first = data_folder / "First"
            second = data_folder / "Second"
            third = data_folder / "Third"
            first.mkdir()
            second.mkdir()
            third.mkdir()
            (first / "First_info.json").write_text(
                '{"editable": {"Session": "Deep Diving"}, "readonly": {"serial number": "123"}}',
                encoding="utf-8",
            )
            (second / "Second_info.json").write_text(
                '{"editable": {"Session": "Running"}, "readonly": {"serial number": ""}}',
                encoding="utf-8",
            )
            (third / "Third_info.json").write_text(
                '{"editable": {"Place": "Lake"}, "readonly": {"serial number": "456"}}',
                encoding="utf-8",
            )
            folders = [first, second, third]
            info_index = scan_folder_info_files(data_folder)

            self.assertEqual(metadata_values_for_key(info_index, "Session"), ["Deep Diving", "Running"])
            self.assertEqual(
                filter_folders_by_metadata(folders, info_index, "Session", "DIV"),
                [first],
            )
            self.assertEqual(
                filter_folders_by_metadata(folders, info_index, "serial number", "(empty)"),
                [second],
            )
            self.assertEqual(filter_folders_by_metadata(folders, info_index, "Session", ""), folders)

    def test_info_tree_item_sorts_before_other_children(self):
        info_item = QTreeWidgetItem(["Info"])
        info_item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_INFO)
        group_item = QTreeWidgetItem(["Accelerometer"])
        group_item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_GROUP)

        self.assertLess(tree_item_sort_key(info_item), tree_item_sort_key(group_item))

    def test_empty_detailed_info_tree_item_sorts_after_real_details(self):
        detail_item = QTreeWidgetItem(["Session"])
        detail_item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_DETAILED_INFO)
        empty_item = QTreeWidgetItem([""])
        empty_item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_DETAILED_INFO)
        empty_item.setData(0, ITEM_INFO_EMPTY_ROLE, True)

        self.assertLess(tree_item_sort_key(detail_item), tree_item_sort_key(empty_item))


class NavigatorColumnProfileTests(unittest.TestCase):
    def test_columns_are_scoped_by_category(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_folder = Path(temp_dir)
            folder = data_folder / "First"
            folder.mkdir()
            (folder / "First_info.json").write_text(
                '{"editable": {"Session": "Dive 1"}, "readonly": {"serial number": "123"}}',
                encoding="utf-8",
            )
            info_index = scan_folder_info_files(data_folder)

        self.assertEqual(
            [column.column_id for column in columns_for_category(COLUMN_CATEGORY_FOLDER, info_index)],
            [COLUMN_DATA, COLUMN_LOG_LENGTH, info_column_id("serial number"), info_column_id("Session")],
        )
        self.assertEqual(
            [column.column_id for column in columns_for_category(COLUMN_CATEGORY_INFO, info_index)],
            [COLUMN_DATA, COLUMN_INFO_PAIR_VALUE],
        )
        self.assertEqual(
            [
                column.column_id
                for column in columns_for_category(
                    COLUMN_CATEGORY_SERIES,
                    info_index,
                    [SeriesMetadata("rate", "Rate", "")],
                )
            ],
            [COLUMN_DATA, "rate"],
        )

    def test_row_types_are_mapped_to_their_column_categories(self):
        virtual_item = QTreeWidgetItem()
        virtual_item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_VIRTUAL)
        folder_item = QTreeWidgetItem()
        folder_item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_FOLDER)
        info_item = QTreeWidgetItem()
        info_item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_INFO)
        detail_item = QTreeWidgetItem()
        detail_item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_DETAILED_INFO)
        group_item = QTreeWidgetItem()
        group_item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_GROUP)
        series_item = QTreeWidgetItem()
        series_item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_SERIES)

        self.assertEqual(column_category_for_item(virtual_item), COLUMN_CATEGORY_FOLDER)
        self.assertEqual(column_category_for_item(folder_item), COLUMN_CATEGORY_FOLDER)
        self.assertEqual(column_category_for_item(info_item), COLUMN_CATEGORY_INFO)
        self.assertEqual(column_category_for_item(detail_item), COLUMN_CATEGORY_INFO)
        self.assertEqual(column_category_for_item(group_item), COLUMN_CATEGORY_SERIES)
        self.assertEqual(column_category_for_item(series_item), COLUMN_CATEGORY_SERIES)

    def test_column_settings_are_saved_per_category(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "app_settings.json"
            settings = load_column_settings(None)
            settings.profiles[COLUMN_CATEGORY_FOLDER].order = [COLUMN_DATA, COLUMN_LOG_LENGTH]
            settings.profiles[COLUMN_CATEGORY_FOLDER].hidden = {COLUMN_LOG_LENGTH}
            settings.shared_widths = {COLUMN_DATA: 240}
            settings.profiles[COLUMN_CATEGORY_FOLDER].widths = {COLUMN_LOG_LENGTH: 120}
            settings.profiles[COLUMN_CATEGORY_SERIES].order = [COLUMN_DATA, COLUMN_SAMPLE_RATE]
            settings.profiles[COLUMN_CATEGORY_SERIES].widths = {COLUMN_SAMPLE_RATE: 90}

            save_column_settings(path, settings)
            raw = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_column_settings(path)

        self.assertIn("profiles", raw["tree_columns"])
        self.assertEqual(raw["tree_columns"]["shared_widths"], {COLUMN_DATA: 240})
        self.assertEqual(loaded.shared_widths, {COLUMN_DATA: 240})
        self.assertEqual(loaded.profiles[COLUMN_CATEGORY_FOLDER].order, [COLUMN_DATA, COLUMN_LOG_LENGTH])
        self.assertEqual(loaded.profiles[COLUMN_CATEGORY_FOLDER].hidden, {COLUMN_LOG_LENGTH})
        self.assertEqual(loaded.profiles[COLUMN_CATEGORY_FOLDER].widths, {COLUMN_LOG_LENGTH: 120})
        self.assertEqual(loaded.profiles[COLUMN_CATEGORY_SERIES].order, [COLUMN_DATA, COLUMN_SAMPLE_RATE])
        self.assertEqual(loaded.profiles[COLUMN_CATEGORY_SERIES].widths, {COLUMN_SAMPLE_RATE: 90})
        self.assertEqual(set(loaded.profiles), {COLUMN_CATEGORY_FOLDER, COLUMN_CATEGORY_INFO, COLUMN_CATEGORY_SERIES})


class NavigatorDiagnosticRowStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_diagnostics_style_series_and_propagate_to_parents(self):
        navigator = DataNavigatorWidget(settings_path=None)
        self.addCleanup(navigator.close)
        folder = navigator.make_item({"data": "Folder"})
        folder.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_FOLDER)
        group = navigator.make_item({"data": "IMU"})
        group.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_GROUP)
        sample_loss = navigator.make_item({"data": "x", "warning": "Sampleloss detected"})
        sample_loss.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_SERIES)
        clipping = navigator.make_item({"data": "y", "warning": "Clipping detected"})
        clipping.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_SERIES)
        both = navigator.make_item({"data": "z", "warning": "Sampleloss detected; Clipping detected"})
        both.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_SERIES)
        group.addChild(sample_loss)
        group.addChild(clipping)
        group.addChild(both)
        folder.addChild(group)
        navigator.tree.addTopLevelItem(folder)

        navigator.apply_diagnostic_row_styles()

        self.assertEqual(sample_loss.data(0, ITEM_DIAGNOSTIC_FLAGS_ROLE), DIAGNOSTIC_SAMPLE_LOSS)
        self.assertEqual(clipping.data(0, ITEM_DIAGNOSTIC_FLAGS_ROLE), DIAGNOSTIC_CLIPPING)
        self.assertEqual(
            group.data(0, ITEM_DIAGNOSTIC_FLAGS_ROLE),
            DIAGNOSTIC_SAMPLE_LOSS | DIAGNOSTIC_CLIPPING,
        )
        self.assertEqual(folder.foreground(0).color().name(), "#806b00")
        self.assertTrue(sample_loss.font(0).bold())
        self.assertEqual(navigator.diagnostic_text_color(both).name(), "#806b00")

        navigator.diagnostic_row_settings["order"] = ["clipping", "sample_loss"]
        navigator.diagnostic_row_settings["clipping"].update({"bold": False, "italic": True})
        navigator.apply_diagnostic_row_styles()
        for item in (folder, group, both, clipping):
            self.assertEqual(navigator.diagnostic_text_color(item).name(), "#8a4d00")
            self.assertEqual(item.foreground(0).color().name(), "#8a4d00")
            self.assertFalse(item.font(0).bold())
            self.assertTrue(item.font(0).italic())
        self.assertEqual(navigator.diagnostic_text_color(sample_loss).name(), "#806b00")

        navigator.diagnostic_row_settings["clipping"]["enabled"] = False
        navigator.apply_diagnostic_row_styles()
        for item in (folder, group, both, sample_loss):
            self.assertEqual(navigator.diagnostic_text_color(item).name(), "#806b00")
            self.assertTrue(item.font(0).bold())
            self.assertFalse(item.font(0).italic())
        self.assertIsNone(navigator.diagnostic_text_color(clipping))
        self.assertFalse(clipping.font(0).italic())

        navigator.diagnostic_row_settings["sample_loss"]["enabled"] = False
        navigator.diagnostic_row_settings["clipping"]["enabled"] = True
        navigator.apply_diagnostic_row_styles()
        self.assertIsNone(navigator.diagnostic_text_color(sample_loss))
        self.assertFalse(sample_loss.font(0).bold())
        self.assertEqual(navigator.diagnostic_text_color(folder).name(), "#8a4d00")

        navigator.diagnostic_row_settings["clipping"]["enabled"] = False
        navigator.apply_diagnostic_row_styles()
        for item in (folder, group, both, sample_loss, clipping):
            self.assertIsNone(navigator.diagnostic_text_color(item))
            self.assertFalse(item.font(0).bold())
            self.assertFalse(item.font(0).italic())
            self.assertEqual(item.foreground(0).style(), Qt.BrushStyle.NoBrush)

    def test_sample_loss_and_clipping_buttons_switch_and_save_the_diagnostics(self):
        from src.app.settings import load_navigator_diagnostics_settings

        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Path(temp_dir) / "app_settings.json"
            navigator = DataNavigatorWidget(settings_path=settings)
            self.addCleanup(navigator.close)
            item = navigator.make_item({"data": "x", "warning": "Sampleloss detected"})
            item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_SERIES)
            navigator.tree.addTopLevelItem(item)
            navigator.apply_diagnostic_row_styles()
            controls = navigator.controls
            self.assertTrue(controls.sample_loss_button.isChecked() and controls.clipping_button.isChecked())
            self.assertEqual(controls.sample_loss_button.toolTip(), "Hide sample loss")
            self.assertEqual(navigator.diagnostic_text_color(item).name(), "#806b00")

            controls.sample_loss_button.click()
            self.assertIsNone(navigator.diagnostic_text_color(item))
            self.assertEqual(controls.sample_loss_button.toolTip(), "Show sample loss")
            saved = load_navigator_diagnostics_settings(settings)
            self.assertFalse(saved["sample_loss"]["enabled"])
            self.assertTrue(saved["clipping"]["enabled"])
            # A new Navigator starts with the saved state.
            later = DataNavigatorWidget(settings_path=settings)
            self.addCleanup(later.close)
            self.assertFalse(later.controls.sample_loss_button.isChecked())

    def test_column_settings_save_falls_back_when_atomic_replace_is_denied(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "app_settings.json"
            path.write_text("{}", encoding="utf-8")
            settings = load_column_settings(None)
            settings.shared_widths = {COLUMN_DATA: 240}

            with mock.patch("pathlib.Path.replace", side_effect=PermissionError("locked")):
                save_column_settings(path, settings)

            loaded = load_column_settings(path)

        self.assertEqual(loaded.shared_widths, {COLUMN_DATA: 240})


def make_sample_navigator_folder(root: Path) -> Path:
    data_folder = root / "Data"
    folder = data_folder / "First"
    folder.mkdir(parents=True)
    (folder / "First_info.json").write_text(
        '{"editable": {"Place": "Lake"}, "readonly": {"serial number": "123"}}',
        encoding="utf-8",
    )
    with h5py.File(folder / "sample.h5", "w") as h5_file:
        h5_file.attrs["time_start_seconds"] = 10.0
        h5_file.attrs["time_end_seconds"] = 72.0
        h5_file.attrs["series_index_json"] = json.dumps([
            {
                "name": "IMU/acc_x",
                "display_name": "acc_x",
                "navigator_name": "acc_x",
                "navigator_group": "IMU",
                "unit": "m/s2",
                "sample_count": 4,
                "sample_rate_hz": 100.0,
                "visible": True,
                "navigator_metadata": [
                    {"id": "sample_count", "title": "Sample count", "format": "integer", "value": 4},
                    {"id": "sample_rate", "title": "Sample rate", "format": "rate", "value": 100.0},
                    {"id": "min", "title": "Min", "format": "number", "value": 1.0},
                    {"id": "max", "title": "Max", "format": "number", "value": 4.0}
                ],
            }
        ])
    return data_folder


def first_item_of_type(navigator: DataNavigatorWidget, row_type: str) -> QTreeWidgetItem:
    for item in navigator.tree_items():
        if item.data(0, ITEM_TYPE_ROLE) == row_type:
            return item
    raise AssertionError(f"No item of type {row_type}")


def detailed_info_item_for_key(navigator: DataNavigatorWidget, key: str) -> QTreeWidgetItem:
    for item in navigator.tree_items():
        if (
            item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_DETAILED_INFO
            and str(item.data(0, ITEM_INFO_KEY_ROLE) or "").casefold() == key.casefold()
        ):
            return item
    raise AssertionError(f"No detailed info item for {key}")


if __name__ == "__main__":
    unittest.main()
