"""Reusable data-folder navigator widget for the future main application."""

from __future__ import annotations

from calendar import month_name
from dataclasses import dataclass, replace
from datetime import date
import json
import math
import time
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QMimeData, QPersistentModelIndex, QPoint, QRect, QSignalBlocker, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QDrag, QKeySequence, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QFileDialog,
    QHeaderView,
    QMenu,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionHeader,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from src.app.settings import (
    DEFAULT_APP_SETTINGS_PATH,
    NAVIGATOR_DIAGNOSTICS_SECTION,
    load_default_info_metadata_settings,
    load_navigator_diagnostics_settings,
    load_navigator_control_settings,
    save_navigator_control_settings,
    save_settings_section,
)
from src.data.folder_date_parser import find_folder_date
from src.data.plot_policy import PlotSource, SELECTION_MIME_TYPE
from src.data.folder_info import (
    FolderInfoData,
    attachment_path,
    info_file_path,
    normalise_info_key,
    read_info_file,
    video_attachment_path,
    write_info_file,
)
from src.data.media import AUDIO_SUFFIX, MEDIA_GROUP
from src.gui.theme import apply_theme, load_theme
from src.gui.plots.data_drop import IMAGE_MIME_TYPE, VIDEO_MIME_TYPE, exec_data_drag
from src.data.navigator_data import (
    FolderInfoIndex,
    H5NavigatorData,
    H5Series,
    SeriesMetadata,
    add_default_info_keys,
    folder_info_index_from_data,
    filter_folders_by_metadata,
    format_log_length,
    folder_info_group_path,
    metadata_values_for_key,
    read_h5_navigator_data,
    resolved_path,
    update_folder_info_index,
)
from src.gui.navigator.controls import FILTER_KEY_PLACEHOLDER, NavigatorControlsWidget
from src.gui.navigator.settings import (
    COLUMN_CATEGORY_FOLDER,
    COLUMN_CATEGORY_INFO,
    COLUMN_CATEGORY_SERIES,
    COLUMN_CATEGORY_SOURCE,
    COLUMN_DATA,
    COLUMN_INFO_PAIR_VALUE,
    COLUMN_LOG_LENGTH,
    COLUMN_SOURCE_OUTPUT,
    COLUMN_SOURCE_PLUGIN,
    CUSTOM_COLUMN_PREFIX,
    SHARED_WIDTH_COLUMNS,
    ColumnProfileSettings,
    column_profile_settings,
    load_column_settings,
    sanitize_column_order,
    save_column_settings,
)
from src.app.startup_profile import mark as startup_mark
from src.jobs.navigator_snapshot import (
    FolderLinks,
    PHASE_DATA,
    PHASE_FINDING,
    PHASE_SOURCES,
    FileSignature as _FileSignature,
    NavigatorSnapshotJob,
    NavigatorSnapshotResult,
    NavigatorSourceSnapshot as _NavigatorSourceSnapshot,
    build_navigator_snapshot,
    file_signature as _file_signature,
    folder_links,
)
from src.jobs.source_detection import (
    STATE_INCOMPLETE,
    STATE_MISSING,
    STATE_NEW,
    STATE_OUTDATED,
    STATE_UNSUPPORTED,
    SourceDetection,
    SourceRow,
)
from src.data.folder_info import SOURCE_IMPORT


GROUPING_NONE = "[FILTER]"
GROUPING_OPTIONS = [GROUPING_NONE, "Year/Month/Day", "Year/Month", "Year", "Today/Earlier"]
FILTER_NONE = FILTER_KEY_PLACEHOLDER
FILTER_EMPTY = "(empty)"
ITEM_TYPE_ROLE = Qt.ItemDataRole.UserRole
ITEM_PATH_ROLE = Qt.ItemDataRole.UserRole + 1
ITEM_SERIES_ROLE = Qt.ItemDataRole.UserRole + 2
COLUMN_INFO_KEY_ROLE = Qt.ItemDataRole.UserRole + 3
ITEM_COLUMN_VALUES_ROLE = Qt.ItemDataRole.UserRole + 4
ITEM_INFO_KEY_ROLE = Qt.ItemDataRole.UserRole + 5
ITEM_INFO_EDITABLE_ROLE = Qt.ItemDataRole.UserRole + 6
ITEM_INFO_EMPTY_ROLE = Qt.ItemDataRole.UserRole + 7
ITEM_PROGRESS_COMPLETED_ROLE = Qt.ItemDataRole.UserRole + 8
ITEM_PROGRESS_TOTAL_ROLE = Qt.ItemDataRole.UserRole + 9
ITEM_DIAGNOSTIC_FLAGS_ROLE = Qt.ItemDataRole.UserRole + 12
ITEM_PROGRESS_TEXT_ROLE = Qt.ItemDataRole.UserRole + 10
ITEM_PROGRESS_ETA_ROLE = Qt.ItemDataRole.UserRole + 11
ITEM_VIDEO_PATH_ROLE = Qt.ItemDataRole.UserRole + 13
ITEM_VIDEO_OFFSET_ROLE = Qt.ItemDataRole.UserRole + 14
ITEM_IMAGE_PATH_ROLE = Qt.ItemDataRole.UserRole + 15
# A Source data row's SourceRow (src/jobs/source_detection.py).
ITEM_SOURCE_ROLE = Qt.ItemDataRole.UserRole + 16
# A Model attachment row's model folder (the folder holding placement.json).
ITEM_MODEL_PATH_ROLE = Qt.ItemDataRole.UserRole + 17
# A folder row still being read: a spinning circle replaces its open/close triangle.
ITEM_LOADING_ROLE = Qt.ItemDataRole.UserRole + 18
# A folder row whose contents (Info, Source data, Media and its series) are not made yet: they
# are made the first time it is opened, or restored as opened (ensure_folder_contents).
ITEM_CONTENT_PENDING_ROLE = Qt.ItemDataRole.UserRole + 19
# The loading circle turns this often (ms), one turn a second.
LOADING_ANIMATION_INTERVAL_MS = 50
ROW_TYPE_VIRTUAL = "virtual"
ROW_TYPE_FOLDER = "folder"
ROW_TYPE_GROUP = "series_group"
ROW_TYPE_SERIES = "series"
ROW_TYPE_INFO = "info"
ROW_TYPE_DETAILED_INFO = "detailed_info"
ROW_TYPE_ERROR = "error"
ROW_TYPE_MESSAGE = "message"
ROW_TYPE_VIDEO = "video"
ROW_TYPE_IMAGE = "image"
ROW_TYPE_MODEL = "model"
ROW_TYPE_SOURCE_GROUP = "source_group"
ROW_TYPE_SOURCE = "source"
SOURCE_GROUP_NAME = "Source data"
DEFAULT_SETTINGS_PATH = DEFAULT_APP_SETTINGS_PATH
SETTINGS_SAVE_DEBOUNCE_MS = 350
SETTINGS_SAVE_RETRY_MS = 1000
SETTINGS_SAVE_MAX_RETRIES = 10
DIAGNOSTIC_SAMPLE_LOSS = 1
DIAGNOSTIC_CLIPPING = 2
ROW_HIGHLIGHT_ALPHA = 70
ROW_HOVER_ALPHA = 35
PHYSICAL_DATA_COLUMN = 0
PHYSICAL_VALUE_COLUMN = 1
PHYSICAL_COLUMN_COUNT = 2


VALUE_CELL_PADDING = 4
LOGICAL_HEADER_HEIGHT = 24
LOGICAL_HEADER_RESIZE_MARGIN = 4
LOGICAL_HEADER_LABEL_LEFT_INSET = 4
LOGICAL_HEADER_SORT_WIDTH = 18


@dataclass(frozen=True)
class TreeColumn:
    column_id: str
    title: str
    always_visible: bool = False
    info_key: str = ""

    @property
    def short_title(self) -> str:
        return self.title


class CopyableTreeWidget(QTreeWidget):
    """Tree widget with plain-text clipboard support for selected cells."""

    hovered_item_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None, navigator=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.navigator = navigator
        self._hovered_item: QTreeWidgetItem | None = None
        self._horizontal_scroll_position_on_press: int | None = None
        self._preserve_horizontal_scroll_during_click = False
        self._horizontal_scroll_lock: int | None = None
        self._restoring_locked_scroll = False
        self._scroll_lock_generation = 0
        self._logical_value_click_active = False
        self.setMouseTracking(True)
        self.horizontalScrollBar().valueChanged.connect(self._enforce_horizontal_scroll_lock)
        # Turns the loading circles while any row is still being read.
        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(LOADING_ANIMATION_INTERVAL_MS)
        self._loading_timer.timeout.connect(self.viewport().update)

    def set_loading_animation(self, active: bool) -> None:
        """Turn the loading circles of rows marked ``ITEM_LOADING_ROLE``, or stop them."""
        if active and not self._loading_timer.isActive():
            self._loading_timer.start()
        elif not active and self._loading_timer.isActive():
            self._loading_timer.stop()

    def drawBranches(self, painter, rect, index) -> None:  # noqa: N802 - Qt override
        if not index.data(ITEM_LOADING_ROLE):
            super().drawBranches(painter, rect, index)
            return
        # Where the open/close triangle would be: the last indentation step.
        step = self.indentation()
        cell = QRect(rect.right() - step + 1, rect.top(), step, rect.height())
        size = max(6, min(12, cell.height() - 6, step - 6))
        circle = QRect(0, 0, size, size)
        circle.moveCenter(cell.center())
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self.palette().color(QPalette.ColorRole.Text)
        track = QColor(color)
        track.setAlpha(50)
        painter.setPen(QPen(track, 2))
        painter.drawEllipse(circle)
        painter.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        start = -int((time.monotonic() % 1.0) * 360 * 16)
        painter.drawArc(circle, start, 100 * 16)
        painter.restore()

    def _enforce_horizontal_scroll_lock(self, value: int) -> None:
        if self._horizontal_scroll_lock is None or self._restoring_locked_scroll:
            return
        scrollbar = self.horizontalScrollBar()
        target = min(self._horizontal_scroll_lock, scrollbar.maximum())
        if value == target:
            return
        self._restoring_locked_scroll = True
        try:
            scrollbar.setValue(target)
        finally:
            self._restoring_locked_scroll = False

    def scrollTo(  # noqa: N802
        self,
        index,
        hint=QAbstractItemView.ScrollHint.EnsureVisible,
    ) -> None:  # type: ignore[no-untyped-def]
        if self._preserve_horizontal_scroll_during_click:
            return
        super().scrollTo(index, hint)

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        position = event.position().toPoint()
        clicked_index = self.indexAt(position)
        logical_value_click = (
            self.navigator is not None
            and event.button() == Qt.MouseButton.LeftButton
            and clicked_index.isValid()
            and clicked_index.column() == PHYSICAL_VALUE_COLUMN
        )
        if self.navigator is not None and event.button() == Qt.MouseButton.LeftButton:
            self._horizontal_scroll_position_on_press = self.horizontalScrollBar().value()
            self._horizontal_scroll_lock = self._horizontal_scroll_position_on_press
            self._preserve_horizontal_scroll_during_click = True
            self._scroll_lock_generation += 1
        if logical_value_click:
            item = self.itemFromIndex(clicked_index)
            if item is not None:
                self._logical_value_click_active = True
                self.navigator.update_logical_cell_from_view_position(position)
                # Keep Qt's real current index out of the oversized physical
                # Value column. The logical subcell selection is tracked by
                # the navigator and painted by the delegate.
                self.setCurrentItem(
                    item,
                    PHYSICAL_DATA_COLUMN,
                    QItemSelectionModel.SelectionFlag.NoUpdate,
                )
                selection_model = self.selectionModel()
                if selection_model is not None:
                    selection_model.select(
                        clicked_index,
                        QItemSelectionModel.SelectionFlag.ClearAndSelect,
                    )
                if self._horizontal_scroll_position_on_press is not None:
                    self.horizontalScrollBar().setValue(self._horizontal_scroll_position_on_press)
                self.viewport().update()
                event.accept()
                return
        super().mousePressEvent(event)
        if self.navigator is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self.navigator.update_logical_cell_from_view_position(position)
        if self._horizontal_scroll_position_on_press is not None:
            self.horizontalScrollBar().setValue(self._horizontal_scroll_position_on_press)
        self.viewport().update()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        logical_value_click = self._logical_value_click_active and event.button() == Qt.MouseButton.LeftButton
        self._logical_value_click_active = False
        if logical_value_click:
            event.accept()
        else:
            super().mouseReleaseEvent(event)
        if self.navigator is None or event.button() != Qt.MouseButton.LeftButton:
            return
        if self._horizontal_scroll_position_on_press is not None:
            self.horizontalScrollBar().setValue(self._horizontal_scroll_position_on_press)
            self.navigator.restore_horizontal_scroll_position(self._horizontal_scroll_position_on_press)
        generation = self._scroll_lock_generation
        QTimer.singleShot(
            0, self,
            lambda: QTimer.singleShot(
                0, self,
                lambda: self._release_horizontal_scroll_lock(generation),
            ),
        )

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        position = event.position().toPoint()
        clicked_index = self.indexAt(position)
        if (
            self.navigator is not None
            and event.button() == Qt.MouseButton.LeftButton
            and clicked_index.isValid()
            and clicked_index.column() == PHYSICAL_VALUE_COLUMN
        ):
            # Logical Value cells are painted inside one real tree column.
            # Start the delegate editor explicitly, without handing the click
            # to Qt's normal cell-selection path (which scrolls that wide
            # physical column back into view).
            self._horizontal_scroll_position_on_press = self.horizontalScrollBar().value()
            self._horizontal_scroll_lock = self._horizontal_scroll_position_on_press
            self._preserve_horizontal_scroll_during_click = True
            self._scroll_lock_generation += 1
            if self.navigator.begin_logical_cell_edit(position):
                if self._horizontal_scroll_position_on_press is not None:
                    self.horizontalScrollBar().setValue(self._horizontal_scroll_position_on_press)
                generation = self._scroll_lock_generation
                QTimer.singleShot(0, self, lambda: self._release_horizontal_scroll_lock(generation))
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def _release_horizontal_scroll_lock(self, generation: int) -> None:
        if generation != self._scroll_lock_generation:
            return
        if self._horizontal_scroll_lock is not None:
            scrollbar = self.horizontalScrollBar()
            scrollbar.setValue(min(self._horizontal_scroll_lock, scrollbar.maximum()))
        self._horizontal_scroll_lock = None
        self._horizontal_scroll_position_on_press = None
        self._preserve_horizontal_scroll_during_click = False

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        position = event.position().toPoint()
        self.set_hovered_item(self.itemAt(position))
        if self.navigator is not None:
            self.navigator.update_hovered_logical_cell_from_view_position(position)
        super().mouseMoveEvent(event)

    def startDrag(self, supported_actions) -> None:  # type: ignore[no-untyped-def]
        """Expose a series row as a portable drop payload for host workspaces."""
        item = self.currentItem()
        if self.navigator is not None:
            sources = self.navigator.selected_plot_sources()
            # A video is dragged as a selection too, so it can join a Multi-series card.
            if len(sources) > 1 or (len(sources) == 1 and sources[0].kind == "video"):
                mime_data = QMimeData()
                mime_data.setData(SELECTION_MIME_TYPE, json.dumps([source.payload() for source in sources]).encode("utf-8"))
                drag = QDrag(self)
                drag.setMimeData(mime_data)
                exec_data_drag(drag, Qt.DropAction.CopyAction)
                return
        if self.navigator is not None and item is not None and item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_IMAGE:
            mime_data = QMimeData()
            mime_data.setData(IMAGE_MIME_TYPE, json.dumps({
                "folder": str(item.data(0, ITEM_PATH_ROLE)),
                "image_path": str(item.data(0, ITEM_IMAGE_PATH_ROLE)),
            }).encode("utf-8"))
            drag = QDrag(self)
            drag.setMimeData(mime_data)
            exec_data_drag(drag, Qt.DropAction.CopyAction)
            return
        if self.navigator is not None and item is not None and item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_VIDEO:
            video_path = str(item.data(0, ITEM_VIDEO_PATH_ROLE) or "")
            folder = str(item.data(0, ITEM_PATH_ROLE) or "")
            if video_path and folder:
                mime_data = QMimeData()
                mime_data.setData(
                    VIDEO_MIME_TYPE,
                    json.dumps({
                        "folder": folder,
                        "video_path": video_path,
                        "offset_seconds": float(item.data(0, ITEM_VIDEO_OFFSET_ROLE) or 0.0),
                    }).encode("utf-8"),
                )
                drag = QDrag(self)
                drag.setMimeData(mime_data)
                exec_data_drag(drag, Qt.DropAction.CopyAction)
                return
        gnss_series = (
            self.navigator.gnss_group_series(item)
            if self.navigator is not None and item is not None
            else None
        )
        if gnss_series is not None:
            h5_path, series_name = gnss_series
            mime_data = QMimeData()
            mime_data.setData(
                "application/x-lodgazilla-series",
                json.dumps({"h5_path": str(h5_path), "series_name": series_name}).encode("utf-8"),
            )
            mime_data.setData("application/x-lodgazilla-gnss-group", b"gnss")
            drag = QDrag(self)
            drag.setMimeData(mime_data)
            exec_data_drag(drag, Qt.DropAction.CopyAction)
            return
        if self.navigator is not None and item is not None and item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_SERIES:
            h5_path = str(item.data(0, ITEM_PATH_ROLE) or "")
            series_name = str(item.data(0, ITEM_SERIES_ROLE) or "")
            if h5_path and series_name:
                mime_data = QMimeData()
                mime_data.setData(
                    "application/x-lodgazilla-series",
                    json.dumps({"h5_path": h5_path, "series_name": series_name}).encode("utf-8"),
                )
                drag = QDrag(self)
                drag.setMimeData(mime_data)
                exec_data_drag(drag, Qt.DropAction.CopyAction)
                return
        super().startDrag(supported_actions)

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.set_hovered_item(None)
        if self.navigator is not None:
            self.navigator.clear_hovered_logical_cell()
        super().leaveEvent(event)

    def clear(self) -> None:
        self.set_hovered_item(None)
        if self.navigator is not None:
            self.navigator.clear_hovered_logical_cell()
        super().clear()

    def set_hovered_item(self, item: QTreeWidgetItem | None) -> None:
        if item is self._hovered_item:
            return
        self._hovered_item = item
        self.hovered_item_changed.emit(item)

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection_to_clipboard()
            event.accept()
            return
        super().keyPressEvent(event)

    def copy_selection_to_clipboard(self) -> None:
        text = self.selected_plain_text()
        if text:
            QApplication.clipboard().setText(text)

    def selected_plain_text(self) -> str:
        if self.navigator is not None:
            return self.navigator.selected_plain_text()
        current = self.currentIndex()
        indexes = [index for index in self.selectedIndexes() if index.isValid()]
        if current.isValid() and self.selection_looks_like_current_row(indexes, current):
            indexes = [current]
        if not indexes:
            if current.isValid():
                indexes = [current]
        if not indexes:
            return ""

        order = self.item_order()
        rows: dict[int, dict[int, str]] = {}
        for index in indexes:
            item = self.itemFromIndex(index)
            if item is None:
                continue
            row_key = order.get(id(item), len(order))
            rows.setdefault(row_key, {})[index.column()] = item.text(index.column())

        lines = []
        for row_key in sorted(rows):
            columns = rows[row_key]
            if not columns:
                continue
            first_column = min(columns)
            last_column = max(columns)
            lines.append("\t".join(columns.get(column, "") for column in range(first_column, last_column + 1)))
        return "\n".join(lines)

    def item_order(self) -> dict[int, int]:
        order: dict[int, int] = {}
        stack = [
            self.topLevelItem(index)
            for index in reversed(range(self.topLevelItemCount()))
        ]
        while stack:
            item = stack.pop()
            order[id(item)] = len(order)
            for child_index in reversed(range(item.childCount())):
                stack.append(item.child(child_index))
        return order

    def selection_looks_like_current_row(self, indexes, current) -> bool:  # type: ignore[no-untyped-def]
        if len(indexes) <= 1:
            return False
        return all(index.row() == current.row() and index.parent() == current.parent() for index in indexes)


class LogicalHeaderWidget(QWidget):
    """Styled header for the physical Data and logical Value columns."""

    def __init__(self, navigator: "DataNavigatorWidget") -> None:
        super().__init__(navigator)
        self.navigator = navigator
        self._resizing_column_id = ""
        self._resize_start_x = 0
        self._resize_start_width = 0
        self._dragged_column_id = ""
        self._pressed_sort_column_id = ""
        self._drag_position: QPoint | None = None
        self.setFixedHeight(LOGICAL_HEADER_HEIGHT)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.navigator.show_logical_header_context_menu)

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        header = self.navigator.tree.header()
        header_style = header.style()
        sections = self.navigator.logical_header_sections()
        for section_index, (column, rect) in enumerate(sections):
            option = QStyleOptionHeader()
            header.initStyleOption(option)
            option.rect = rect
            option.text = column.short_title
            option.section = section_index
            if len(sections) == 1:
                option.position = QStyleOptionHeader.SectionPosition.OnlyOneSection
            elif section_index == 0:
                option.position = QStyleOptionHeader.SectionPosition.Beginning
            elif section_index == len(sections) - 1:
                option.position = QStyleOptionHeader.SectionPosition.End
            else:
                option.position = QStyleOptionHeader.SectionPosition.Middle
            header_style.drawControl(QStyle.ControlElement.CE_HeaderSection, option, painter, header)
            label_option = QStyleOptionHeader(option)
            sort_rect = self.navigator.logical_header_sort_rect(rect)
            label_option.rect = rect.adjusted(
                LOGICAL_HEADER_LABEL_LEFT_INSET, 0,
                -(LOGICAL_HEADER_SORT_WIDTH + LOGICAL_HEADER_RESIZE_MARGIN + 1) if sort_rect is not None else 0, 0,
            )
            label_option.textAlignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            header_style.drawControl(QStyle.ControlElement.CE_HeaderLabel, label_option, painter, header)
            if sort_rect is not None:
                direction = (
                    self.navigator.folder_sort_direction
                    if self.navigator.folder_sort_column_id == column.column_id else 0
                )
                painter.save()
                painter.setClipRect(rect)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(QPen(self.palette().color(QPalette.ColorRole.ButtonText), 1.4))
                center = sort_rect.center()
                x, y = center.x(), center.y()
                painter.drawLine(x, y - 4, x, y + 4)
                if direction in (0, 1):
                    painter.drawLine(x - 3, y - 1, x, y - 4)
                    painter.drawLine(x + 3, y - 1, x, y - 4)
                if direction in (0, -1):
                    painter.drawLine(x - 3, y + 1, x, y + 4)
                    painter.drawLine(x + 3, y + 1, x, y + 4)
                painter.restore()

        if self._dragged_column_id and self._drag_position is not None:
            target = self.navigator.logical_header_drop_target(self._drag_position)
            dragged_section = next(
                ((column, rect) for column, rect in sections if column.column_id == self._dragged_column_id),
                None,
            )
            if target is not None:
                target_column, place_after = target
                target_rect = next(
                    (rect for column, rect in sections if column.column_id == target_column.column_id),
                    None,
                )
                if target_rect is not None:
                    marker_x = target_rect.right() + 1 if place_after else target_rect.left()
                    painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Highlight), 2))
                    painter.drawLine(marker_x, 1, marker_x, self.height() - 2)
            if dragged_section is not None:
                column, rect = dragged_section
                preview_rect = QRect(
                    self._drag_position.x() - rect.width() // 2,
                    rect.y(),
                    rect.width(),
                    rect.height(),
                )
                option = QStyleOptionHeader()
                header.initStyleOption(option)
                option.rect = preview_rect
                option.text = column.short_title
                option.position = QStyleOptionHeader.SectionPosition.OnlyOneSection
                painter.save()
                painter.setOpacity(0.7)
                header_style.drawControl(QStyle.ControlElement.CE_HeaderSection, option, painter, header)
                label_option = QStyleOptionHeader(option)
                label_option.rect = preview_rect.adjusted(LOGICAL_HEADER_LABEL_LEFT_INSET, 0, 0, 0)
                label_option.textAlignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                header_style.drawControl(QStyle.ControlElement.CE_HeaderLabel, label_option, painter, header)
                painter.restore()
        painter.end()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._resizing_column_id:
            delta = event.position().toPoint().x() - self._resize_start_x
            self.navigator.resize_logical_column(self._resizing_column_id, self._resize_start_width + delta)
            return
        if self._dragged_column_id:
            self._drag_position = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()
            return
        position = event.position().toPoint()
        sort_column = self.navigator.logical_header_sort_column(position)
        if self.navigator.logical_header_resize_column(position) is not None:
            cursor = Qt.CursorShape.SplitHCursor
        elif sort_column is not None:
            cursor = Qt.CursorShape.PointingHandCursor
        elif self.navigator.logical_header_draggable_column(position) is not None:
            cursor = Qt.CursorShape.OpenHandCursor
        else:
            cursor = Qt.CursorShape.ArrowCursor
        self.setCursor(
            cursor
        )
        self.setToolTip(
            f"Sort top-level folders by {sort_column.short_title}: ascending → descending → original order"
            if sort_column is not None else ""
        )

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.MouseButton.LeftButton:
            column = self.navigator.logical_header_resize_column(event.position().toPoint())
            if column is not None:
                self._resizing_column_id = column.column_id
                self._resize_start_x = event.position().toPoint().x()
                self._resize_start_width = self.navigator.logical_column_width(column)
                event.accept()
                return
            column = self.navigator.logical_header_sort_column(event.position().toPoint())
            if column is not None:
                self._pressed_sort_column_id = column.column_id
                event.accept()
                return
            column = self.navigator.logical_header_draggable_column(event.position().toPoint())
            if column is not None:
                self._dragged_column_id = column.column_id
                self._drag_position = event.position().toPoint()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._pressed_sort_column_id:
            pressed_column_id = self._pressed_sort_column_id
            self._pressed_sort_column_id = ""
            column = self.navigator.logical_header_sort_column(event.position().toPoint())
            if event.button() == Qt.MouseButton.LeftButton and column is not None and column.column_id == pressed_column_id:
                self.navigator.cycle_folder_sort(column.column_id)
            event.accept()
            return
        if self._resizing_column_id:
            self._resizing_column_id = ""
            self.navigator.save_column_settings()
            event.accept()
            return
        if self._dragged_column_id:
            dragged_column_id = self._dragged_column_id
            self._dragged_column_id = ""
            self._drag_position = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            target = self.navigator.logical_header_drop_target(event.position().toPoint())
            if target is not None:
                target_column, place_after = target
                self.navigator.move_logical_column_to(
                    dragged_column_id,
                    target_column.column_id,
                    place_after,
                )
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class FolderInfoEditDelegate(QStyledItemDelegate):
    """Paint and edit row-specific logical values inside the Value column."""

    def __init__(self, navigator: "DataNavigatorWidget") -> None:
        super().__init__(navigator.tree)
        self.navigator = navigator

    def createEditor(self, parent, option, index):  # type: ignore[no-untyped-def]
        item = self.navigator.tree.itemFromIndex(index)
        column = self.navigator.column_at(index.column(), item)
        if item is not None and column is not None:
            if (
                item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_FOLDER
                and self.navigator.is_folder_info_column_editable(item, column)
            ):
                return super().createEditor(parent, option, index)
            if (
                item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_DETAILED_INFO
                and bool(item.data(0, ITEM_INFO_EDITABLE_ROLE))
                and column.column_id in {COLUMN_DATA, COLUMN_INFO_PAIR_VALUE}
            ):
                return super().createEditor(parent, option, index)
        return None

    def setEditorData(self, editor, index) -> None:  # type: ignore[no-untyped-def]
        item = self.navigator.tree.itemFromIndex(index)
        column = self.navigator.column_at(index.column(), item)
        if item is not None and column is not None:
            editor.setText(self.navigator.item_editable_value(item, column.column_id))
            return
        super().setEditorData(editor, index)

    def setModelData(self, editor, model, index) -> None:  # type: ignore[no-untyped-def]
        item = self.navigator.tree.itemFromIndex(index)
        column = self.navigator.column_at(index.column(), item)
        if item is not None and column is not None:
            # Enter or focus loss without typing must not write anything,
            # least of all the Add metadata row's placeholder text.
            if editor.text() != self.navigator.item_editable_value(item, column.column_id):
                self.navigator.on_logical_cell_edited(item, column, editor.text())
            return
        super().setModelData(editor, model, index)

    def updateEditorGeometry(self, editor, option, index) -> None:  # type: ignore[no-untyped-def]
        item = self.navigator.tree.itemFromIndex(index)
        column = self.navigator.column_at(index.column(), item)
        if item is not None and column is not None and index.column() == PHYSICAL_VALUE_COLUMN:
            for logical_column, rect in self.navigator.value_column_sections(item, option.rect):
                if logical_column.column_id == column.column_id:
                    editor.setGeometry(rect.adjusted(1, 1, -1, -1))
                    return
        super().updateEditorGeometry(editor, option, index)

    def paint(self, painter, option, index) -> None:  # type: ignore[no-untyped-def]
        item = self.navigator.tree.itemFromIndex(index)
        total = int(item.data(0, ITEM_PROGRESS_TOTAL_ROLE) or 0) if item is not None else 0
        if item is None:
            super().paint(painter, option, index)
            return
        if index.column() == PHYSICAL_VALUE_COLUMN:
            self.paint_value_column(painter, option, item)
            return
        if index.column() != PHYSICAL_DATA_COLUMN or total <= 0:
            super().paint(painter, option, index)
            return

        completed = max(0, min(int(item.data(0, ITEM_PROGRESS_COMPLETED_ROLE) or 0), total))
        base_option = QStyleOptionViewItem(option)
        base_option.text = ""
        # The folder label is painted below together with the ETA.  Merely
        # clearing ``text`` is not enough with every Qt style: some styles read
        # the model display role again and paint it a second time.
        base_option.features &= ~QStyleOptionViewItem.ViewItemFeature.HasDisplay
        super().paint(painter, base_option, index)

        progress_width = round(option.rect.width() * completed / total)
        if progress_width > 0:
            progress_rect = option.rect.adjusted(0, 0, progress_width - option.rect.width(), 0)
            progress_color = QColor(option.palette.color(QPalette.ColorRole.Highlight))
            progress_color.setAlpha(145)
            painter.fillRect(progress_rect, progress_color)

        content_rect = option.rect.adjusted(4, 0, -4, 0)
        eta_text = str(item.data(0, ITEM_PROGRESS_ETA_ROLE) or "")
        eta_width = min(content_rect.width(), option.fontMetrics.horizontalAdvance(eta_text))
        label_rect = content_rect.adjusted(0, 0, -(eta_width + 8), 0)
        label_text = option.fontMetrics.elidedText(
            self.navigator.item_logical_value(item, COLUMN_DATA),
            Qt.TextElideMode.ElideMiddle,
            max(0, label_rect.width()),
        )
        if option.state & QStyle.StateFlag.State_Selected:
            text_color = option.palette.color(QPalette.ColorRole.HighlightedText)
        else:
            text_color = self.navigator.diagnostic_text_color(item) or option.palette.color(
                QPalette.ColorRole.Text
            )
        painter.setPen(text_color)
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label_text)
        painter.drawText(content_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, eta_text)

    def paint_value_column(self, painter, option, item: QTreeWidgetItem) -> None:  # type: ignore[no-untyped-def]
        # Let Qt paint the ordinary row background so Data and Value use the
        # exact same alternating-row colors. Remove its physical-column hover
        # and selection states; logical cells draw those states below.
        base_option = QStyleOptionViewItem(option)
        base_option.text = ""
        base_option.state &= ~(
            QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver
        )
        super().paint(
            painter,
            base_option,
            self.navigator.tree.indexFromItem(item, PHYSICAL_VALUE_COLUMN),
        )
        columns = self.navigator.value_columns_for_item(item)
        if not columns:
            return
        selected_column = self.navigator.selected_logical_column_for_item(item)
        hovered_column = self.navigator.hovered_logical_column_for_item(item)
        for column, rect in self.navigator.value_column_sections(item, option.rect):
            is_selected = (
                selected_column is not None
                and item is self.navigator.tree.currentItem()
                and column.column_id == selected_column.column_id
            )
            is_hovered = hovered_column is not None and column.column_id == hovered_column.column_id
            if is_selected:
                painter.fillRect(rect, option.palette.color(QPalette.ColorRole.Highlight))
            elif is_hovered:
                painter.fillRect(rect, option.palette.color(QPalette.ColorRole.Highlight))
            text_color = (
                option.palette.color(QPalette.ColorRole.HighlightedText)
                if is_selected or is_hovered
                else self.navigator.diagnostic_text_color(item) or option.palette.color(QPalette.ColorRole.Text)
            )
            painter.setPen(text_color)
            value = self.navigator.item_logical_value(item, column.column_id)
            text_rect = rect.adjusted(VALUE_CELL_PADDING, 0, -VALUE_CELL_PADDING, 0)
            text = option.fontMetrics.elidedText(value, Qt.TextElideMode.ElideRight, max(0, text_rect.width()))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            if is_selected:
                focus_color = QColor(option.palette.color(QPalette.ColorRole.Highlight))
                painter.setPen(QPen(focus_color, 1))
                painter.drawRect(rect.adjusted(1, 1, -2, -2))


class DataNavigatorWidget(QWidget):
    """Browse imported HDF5 caches by folder and date-derived groupings."""

    series_activated = Signal(Path, str)
    # The phase of a data folder read, as one line for the Internal log ("finding folders", ...).
    loading_status = Signal(str)
    gnss_group_activated = Signal(Path, str)
    video_activated = Signal(Path, Path, float)
    video_context_menu_requested = Signal(Path, Path, QPoint)
    image_activated = Signal(Path, Path)
    image_context_menu_requested = Signal(Path, Path, QPoint)
    # A Model attachment row: its folder and the model folder.
    model_context_menu_requested = Signal(Path, Path, QPoint)
    series_context_menu_requested = Signal(Path, str, QPoint)
    folder_context_menu_requested = Signal(Path, QPoint)
    # A Source data row: its folder and SourceRow (src/jobs/source_detection.py).
    source_activated = Signal(Path, object)
    source_context_menu_requested = Signal(Path, object, QPoint)
    series_group_context_menu_requested = Signal(object, QPoint)
    # A folder's Media group: the folder and its audio series (path, name), maybe none.
    media_group_context_menu_requested = Signal(Path, object, QPoint)
    folder_info_changed = Signal(Path)
    metadata_write_failed = Signal(Path, str)
    navigator_warning = Signal(str)
    # True while a background job reads the data folder, False when the tree is up to date.
    loading_changed = Signal(bool)

    def __init__(
        self,
        initial_folder: str | Path | None = None,
        parent: QWidget | None = None,
        *,
        settings_path: str | Path | None = DEFAULT_SETTINGS_PATH,
        defer_initial_load: bool = False,
    ) -> None:
        super().__init__(parent)
        apply_theme(load_theme(settings_path))
        self.setObjectName("dataNavigator")
        self.info_group_keys: set[str] = set()
        self._metadata_read_errors: dict[Path, str] = {}
        self.columns: list[TreeColumn] = []
        self.column_indexes: dict[str, int] = {}
        self.column_settings = load_column_settings(settings_path)
        self.settings_path = Path(settings_path).resolve() if settings_path is not None else None
        self.navigator_control_settings = load_navigator_control_settings(settings_path)
        self.diagnostic_row_settings = load_navigator_diagnostics_settings(settings_path)
        self.default_info_metadata_keys = set(
            load_default_info_metadata_settings(settings_path)["keys"]
        )
        if initial_folder is None and self.navigator_control_settings.recent_folders:
            initial_folder = self.navigator_control_settings.recent_folders[0]
        self.current_info_index = FolderInfoIndex(set(), set(), {}, {})
        self.current_series_columns = []
        self._source_snapshot: _NavigatorSourceSnapshot | None = None
        # On a data folder's first read, the folders read so far, shown until the read ends.
        # Never used as the previous snapshot of a refresh.
        self._partial_snapshot: _NavigatorSourceSnapshot | None = None
        # What the snapshot job uses to find Source data rows; set_source_detection changes it.
        self.source_detection = SourceDetection()
        # None builds snapshots in a background job while an event loop runs
        # and synchronously otherwise (start-up before exec(), scripted tests).
        self.background_refresh: bool | None = None
        self._snapshot_job: NavigatorSnapshotJob | None = None
        self._snapshot_refresh_pending = False
        self._snapshot_rescan_pending = False
        self._info_edits_since_job_start: dict[Path, tuple[FolderInfoData, _FileSignature | None]] = {}
        self.active_column_category = COLUMN_CATEGORY_FOLDER
        self.folder_sort_column_id = ""
        self.folder_sort_direction = 0
        self._original_root_order: dict[int, int] = {}
        # The folder rows shown, what each holds, and what decides the tree's layout; an
        # update with the same layout changes only the folders whose data changed.
        self._folder_items: dict[Path, QTreeWidgetItem] = {}
        self._folder_plans: dict[Path, FolderPlan] = {}
        self._view_key: tuple | None = None
        self._populating_tree = False
        self._applying_column_layout = False
        self._column_settings_dirty = False
        self._column_settings_save_retries = 0
        self._highlighted_row_items: list[QTreeWidgetItem] = []
        self._hovered_row_item: QTreeWidgetItem | None = None
        self._updating_row_highlights = False
        self._updating_info_items = False
        self._restoring_tree_expansion = False
        self._selected_logical_column_id = ""
        self._hovered_logical_column_id = ""
        self._hovered_value_index: QPersistentModelIndex | None = None

        self.column_settings_save_timer = QTimer(self)
        self.column_settings_save_timer.setSingleShot(True)
        self.column_settings_save_timer.timeout.connect(self.flush_column_settings)

        self.controls = NavigatorControlsWidget(
            hide_irrelevant_series=self.navigator_control_settings.hide_irrelevant_series,
            diagnostics_enabled={name: self.diagnostic_row_settings[name]["enabled"]
                                 for name in self.diagnostic_row_settings["order"]},
        )
        self.folder_edit = self.controls.folder_edit
        self.folder_combo = self.controls.folder_combo
        self.group_by_combo = self.controls.group_by_combo
        self.filter_key_combo = self.controls.filter_key_combo
        self.filter_value_combo = self.controls.filter_value_combo

        self.folder_edit.editingFinished.connect(self.on_folder_edit_finished)
        self.folder_combo.activated.connect(self.on_recent_folder_selected)
        self.controls.hide_irrelevant_changed.connect(self.on_hide_irrelevant_changed)
        self.controls.diagnostic_toggled.connect(self.on_diagnostic_toggled)
        self.controls.refresh_requested.connect(lambda: self.refresh_tree(rescan=True))
        self.folder_combo.browse_requested.connect(self.browse_for_folder)

        self.group_by_combo.addItems(GROUPING_OPTIONS)
        self.group_by_combo.setCurrentText(GROUPING_NONE)
        self.group_by_refresh_timer = QTimer(self)
        self.group_by_refresh_timer.setSingleShot(True)
        self.group_by_refresh_timer.setInterval(250)
        self.group_by_refresh_timer.timeout.connect(self.refresh_tree)
        self.group_by_combo.currentTextChanged.connect(self.schedule_group_by_refresh)
        self.group_by_combo.lineEdit().editingFinished.connect(self.refresh_tree)

        self.filter_key_combo.currentTextChanged.connect(self.on_filter_key_changed)

        self.filter_value_combo.activated.connect(lambda _index: self.refresh_tree())
        self.filter_value_combo.lineEdit().editingFinished.connect(self.refresh_tree)

        self.tree = CopyableTreeWidget(navigator=self)
        self.tree.setIndentation(10)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setDragEnabled(True)
        self.tree.setItemDelegate(FolderInfoEditDelegate(self))
        self.tree.itemChanged.connect(self.on_tree_item_changed)
        self.tree.currentItemChanged.connect(self.on_current_tree_item_changed)
        self.tree.currentItemChanged.connect(lambda _current, _previous: self.update_row_highlights())
        self.tree.hovered_item_changed.connect(self.on_hovered_tree_item_changed)
        self.tree.itemExpanded.connect(self.on_tree_item_expanded)
        self.tree.selectionModel().selectionChanged.connect(lambda _selected, _deselected: self.update_row_highlights())
        self.tree.itemDoubleClicked.connect(self.on_item_activated)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.on_context_menu_requested)
        header = self.tree.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.setSectionsMovable(False)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self.show_logical_header_context_menu)
        header.sectionResized.connect(self.on_physical_header_section_resized)
        self.tree.horizontalScrollBar().valueChanged.connect(lambda _value: self.logical_header.update())
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.logical_header = LogicalHeaderWidget(self)
        self.logical_header.setFixedHeight(header.sizeHint().height())
        self.tree.setHeaderHidden(True)

        layout = QVBoxLayout(self)
        # Keep the Navigator's top control row on the same baseline as the
        # Plot navigator header beside it.  The Qt default (9 px) margin made
        # this side start visibly lower even though both rows are 28 px high.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.controls)
        layout.addSpacing(6)
        layout.addWidget(self.logical_header)
        layout.addWidget(self.tree, stretch=1)

        self.configure_tree_columns(self.current_info_index)
        self.set_recent_folders(self.navigator_control_settings.recent_folders)

        # Until the deferred first load starts, refreshes only redraw: scanning on the GUI
        # thread before the event loop runs would delay the window.
        self._initial_load_pending = False
        if initial_folder is not None and defer_initial_load:
            # Show Loading… now and scan in the snapshot job once the event loop runs.
            folder_text = str(Path(initial_folder).resolve())
            self.folder_edit.setText(folder_text)
            self.remember_data_folder(folder_text)
            self.rebuild_tree()
            self._initial_load_pending = True
            QTimer.singleShot(0, self, self._start_initial_load)
        elif initial_folder is not None:
            startup_mark("current directory navigator load started")
            self.set_data_folder(initial_folder)
            startup_mark("current directory navigator tree populated")

    def _start_initial_load(self) -> None:
        """Build the first snapshot in the background unless a refresh already did."""
        self._initial_load_pending = False
        data_folder = self.data_folder()
        if data_folder is None or not data_folder.is_dir():
            self.rebuild_tree()
        elif self._snapshot_for_root(data_folder) is None and self._snapshot_job is None:
            self._request_snapshot(data_folder, rescan=False)

    def set_data_folder(self, folder: str | Path) -> None:
        folder_text = str(Path(folder).resolve())
        self.folder_edit.setText(folder_text)
        self.remember_data_folder(folder_text)
        self.refresh_tree()

    def on_folder_edit_finished(self) -> None:
        folder = self.folder_edit.text().strip()
        # Leaving the field unchanged must not rebuild the tree; Refresh does that.
        recent = self.navigator_control_settings.recent_folders
        if folder and not (recent and str(Path(folder).resolve()) == recent[0]):
            self.set_data_folder(folder)

    def on_recent_folder_selected(self, _index: int) -> None:
        if self.folder_combo.is_browse_item(_index):
            return  # [Browse…] asks for a folder through browse_requested.
        folder = self.folder_combo.full_folder_path(_index).strip()
        if folder:
            self.set_data_folder(folder)

    def set_recent_folders(self, folders: tuple[str, ...]) -> None:
        self.folder_combo.blockSignals(True)
        try:
            self.folder_combo.set_recent_folders(folders)
        finally:
            self.folder_combo.blockSignals(False)

    def remember_data_folder(self, folder: str) -> None:
        folders = [folder, *self.navigator_control_settings.recent_folders]
        recent_folders = tuple(dict.fromkeys(item for item in folders if item))[:10]
        if recent_folders == self.navigator_control_settings.recent_folders:
            return
        self.navigator_control_settings = replace(self.navigator_control_settings, recent_folders=recent_folders)
        self.set_recent_folders(recent_folders)
        if self.settings_path is not None:
            save_navigator_control_settings(self.settings_path, self.navigator_control_settings)

    def on_hide_irrelevant_changed(self, hide_irrelevant_series: bool) -> None:
        """Persist the toolbar toggle and immediately rebuild the visible tree."""
        self.navigator_control_settings = replace(
            self.navigator_control_settings, hide_irrelevant_series=bool(hide_irrelevant_series)
        )
        if self.settings_path is not None:
            save_navigator_control_settings(self.settings_path, self.navigator_control_settings)
        self.refresh_tree()

    def on_diagnostic_toggled(self, name: str, enabled: bool) -> None:
        """Persist a Sample loss or Clipping button and restyle the tree rows at once."""
        self.diagnostic_row_settings[name] = {**self.diagnostic_row_settings[name], "enabled": bool(enabled)}
        if self.settings_path is not None:
            section = {key: value for key, value in self.diagnostic_row_settings.items()}
            save_settings_section(self.settings_path, NAVIGATOR_DIAGNOSTICS_SECTION, section)
        self.apply_diagnostic_row_styles()
        self.update_row_highlights()
        self.tree.viewport().update()

    def reload_navigator_control_settings(self) -> None:
        """Apply navigator settings saved by the Settings dialog to this widget."""
        if self.settings_path is None:
            return
        settings = load_navigator_control_settings(self.settings_path)
        default_keys = set(load_default_info_metadata_settings(self.settings_path)["keys"])
        # The tree is rebuilt only when a setting it is built from changed.
        tree_changed = (
            default_keys != self.default_info_metadata_keys
            or replace(settings, recent_folders=())
            != replace(self.navigator_control_settings, recent_folders=())
        )
        self.navigator_control_settings = settings
        self.diagnostic_row_settings = load_navigator_diagnostics_settings(self.settings_path)
        self.default_info_metadata_keys = default_keys
        self.controls.blockSignals(True)
        try:
            self.controls.hide_irrelevant_button.setChecked(settings.hide_irrelevant_series)
            for name, button in self.controls.diagnostic_buttons.items():
                button.setChecked(self.diagnostic_row_settings[name]["enabled"])
        finally:
            self.controls.blockSignals(False)
        self.apply_diagnostic_row_styles()
        self.update_row_highlights()
        self.logical_header.update()
        self.tree.viewport().update()
        self.set_recent_folders(settings.recent_folders)
        if tree_changed:
            self.refresh_tree()

    def data_folder(self) -> Path | None:
        text = self.folder_edit.text().strip()
        return Path(text).resolve() if text else None

    def workspace_state(self) -> dict[str, str]:
        """The data folder, grouping and filter saved in a workspace (.umw) file."""
        folder = self.data_folder()
        return {
            "folder": str(folder) if folder is not None else "",
            "group_by": self.group_by_combo.currentText().strip() or GROUPING_NONE,
            "filter_key": self.filter_key_combo.currentText().strip() or FILTER_NONE,
            "filter_value": self.filter_value_combo.currentText(),
        }

    def restore_workspace_state(self, state: dict[str, str]) -> None:
        """Show the saved folder, grouping and filter; the tree is rebuilt once."""
        combos = (self.group_by_combo, self.filter_key_combo, self.filter_value_combo)
        for combo in combos:
            combo.blockSignals(True)
        try:
            # Metadata keys are listed once the folder is read, so add a saved one now.
            for combo, text in ((self.group_by_combo, str(state.get("group_by") or GROUPING_NONE)),
                                (self.filter_key_combo, str(state.get("filter_key") or FILTER_NONE))):
                if combo.findText(text) < 0:
                    combo.addItem(text)
                combo.setCurrentText(text)
            filtered = self.filter_key_combo.currentText() != FILTER_NONE
            self.filter_value_combo.setEnabled(filtered)
            self.filter_value_combo.setEditText(str(state.get("filter_value") or "") if filtered else "")
        finally:
            for combo in combos:
                combo.blockSignals(False)
        folder = str(state.get("folder") or "")
        if folder and Path(folder).resolve() != self.data_folder():
            self.set_data_folder(folder)
        else:
            self.refresh_tree()

    def browse_for_folder(self) -> None:
        current_folder = self.data_folder()
        start_folder = str(current_folder if current_folder and current_folder.is_dir() else Path.cwd())
        selected_folder = QFileDialog.getExistingDirectory(self, "Select data folder", start_folder)
        if selected_folder:
            self.set_data_folder(selected_folder)

    def set_source_detection(self, detection: SourceDetection) -> None:
        """Use these enabled importers and exports for the Source data rows, and look again."""
        if detection == self.source_detection:
            return
        self.source_detection = detection
        if self.data_folder() is not None and not self._initial_load_pending:
            self.refresh_tree()

    def _display_snapshot(self) -> _NavigatorSourceSnapshot | None:
        """The snapshot the tree shows: the finished one, or a first read's folders so far."""
        return self._source_snapshot if self._source_snapshot is not None else self._partial_snapshot

    def all_source_rows(self) -> list[tuple[Path, SourceRow]]:
        """Every folder's Source data rows, with their folder, for the Batch menu."""
        snapshot = self._display_snapshot()
        if snapshot is None:
            return []
        return [(folder, row) for folder in snapshot.folders for row in snapshot.source_rows.get(folder, [])]

    def source_rows(self, folder: Path) -> list[SourceRow]:
        snapshot = self._display_snapshot()
        return list(snapshot.source_rows.get(resolved_path(folder), [])) if snapshot is not None else []

    def invalidate_source_snapshot(self) -> None:
        """Require a filesystem scan on the next tree refresh."""
        self._source_snapshot = None

    def _folder_info_from_snapshot(self, folder: Path) -> FolderInfoData | None:
        snapshot = self._display_snapshot()
        if snapshot is None:
            return None
        return snapshot.folder_infos.get(resolved_path(folder))

    def _h5_data_from_snapshot(self, h5_path: Path) -> H5NavigatorData:
        snapshot = self._display_snapshot()
        if snapshot is None:
            return read_h5_navigator_data(h5_path)
        cached = snapshot.h5_data.get(resolved_path(h5_path))
        return cached if cached is not None else read_h5_navigator_data(h5_path)

    def snapshot_refresh_in_progress(self) -> bool:
        return self._snapshot_job is not None

    def _uses_background_refresh(self) -> bool:
        if self.background_refresh is not None:
            return self.background_refresh
        return QThread.currentThread().loopLevel() > 0

    def _snapshot_for_root(self, root: Path) -> _NavigatorSourceSnapshot | None:
        snapshot = self._source_snapshot
        return snapshot if snapshot is not None and snapshot.root == root else None

    def cancel_background_refresh(self) -> None:
        """Cancel an in-flight snapshot job, for example during shutdown."""
        self._cancel_snapshot_job()

    def _cancel_snapshot_job(self) -> None:
        job = self._snapshot_job
        self._snapshot_job = None
        self._partial_snapshot = None
        self._snapshot_refresh_pending = False
        self._snapshot_rescan_pending = False
        self._info_edits_since_job_start = {}
        if job is not None:
            job.cancel()
            self.loading_changed.emit(False)

    def _request_snapshot(self, root: Path, *, rescan: bool) -> None:
        """Start a background snapshot build, coalescing requests for one root."""
        job = self._snapshot_job
        if job is not None and job.root == root:
            self._snapshot_refresh_pending = True
            self._snapshot_rescan_pending = self._snapshot_rescan_pending or rescan
            return
        self._cancel_snapshot_job()
        self._start_snapshot_job(root, rescan=rescan)

    def _start_snapshot_job(self, root: Path, *, rescan: bool) -> None:
        self._info_edits_since_job_start = {}
        self._logged_phase = None
        self.loading_changed.emit(True)
        self._snapshot_job = NavigatorSnapshotJob(
            self,
            root,
            self._snapshot_for_root(root),
            self._on_snapshot_job_finished,
            rescan=rescan,
            detection=self.source_detection,
            on_progress=self._on_snapshot_progress,
            on_partial=lambda snapshot, job_root=root: self._on_snapshot_partial(job_root, snapshot),
        )

    def _on_snapshot_partial(self, root: Path, snapshot: _NavigatorSourceSnapshot) -> None:
        """Show a first read's folders so far, keeping the Loading row above them."""
        job = self._snapshot_job
        if job is None or job.root != root or self._source_snapshot is not None or root != self.data_folder():
            return
        # A metadata edit made since the read started is newer than what it read.
        for folder, (info, signature) in self._info_edits_since_job_start.items():
            if folder in snapshot.info_signatures:
                snapshot.folder_infos[folder] = info
                snapshot.info_signatures[folder] = signature
        self._partial_snapshot = snapshot
        self.update_tree_view()

    def _on_snapshot_progress(self, done: int, total: int, phase: str = PHASE_DATA) -> None:
        """Report each phase of a data folder read once, as it starts, for the Internal log."""
        if phase != getattr(self, "_logged_phase", None):
            self._logged_phase = phase
            self.loading_status.emit(loading_phase_text(phase, total))

    def _on_snapshot_job_finished(self, job: NavigatorSnapshotJob, result: object) -> None:
        if job is not self._snapshot_job:
            return
        self._snapshot_job = None
        self._partial_snapshot = None
        pending = self._snapshot_refresh_pending
        pending_rescan = self._snapshot_rescan_pending
        self._snapshot_refresh_pending = False
        self._snapshot_rescan_pending = False
        edits = self._info_edits_since_job_start
        self._info_edits_since_job_start = {}
        if job.root != self.data_folder():
            self.loading_changed.emit(False)
            return
        if isinstance(result, NavigatorSnapshotResult):
            # A metadata edit committed while the worker ran is newer than
            # what the worker may have read.
            for folder, (info, signature) in edits.items():
                if folder in result.snapshot.info_signatures:
                    result.snapshot.folder_infos[folder] = info
                    result.snapshot.info_signatures[folder] = signature
            if self._apply_snapshot_result(result):
                self.update_tree_view()
            else:
                # Nothing changed, but no folder is loading any more.
                self.mark_loading_folders(frozenset())
        elif isinstance(result, BaseException):
            self.navigator_warning.emit(f"Could not read data folder {job.root}: {result}")
            if self._snapshot_for_root(job.root) is None:
                self.rebuild_tree()
        if pending:
            self._start_snapshot_job(job.root, rescan=pending_rescan)
        else:
            self.loading_changed.emit(False)

    def _apply_snapshot_result(self, result: NavigatorSnapshotResult) -> bool:
        self._source_snapshot = result.snapshot
        for folder, error in result.read_errors.items():
            self._report_metadata_read_failure(folder, error)
        for folder in result.read_ok:
            self._metadata_read_errors.pop(folder, None)
        return result.changed

    def _update_source_snapshot(self, data_folder: Path, *, rescan: bool) -> _NavigatorSourceSnapshot:
        """Build the snapshot on the calling thread, reusing unchanged entries."""
        root = data_folder.resolve()
        startup_mark("navigator snapshot build started")
        result = build_navigator_snapshot(root, self._snapshot_for_root(root), rescan=rescan,
                                          detection=self.source_detection)
        startup_mark(
            f"navigator snapshot build completed ({len(result.snapshot.folders)} folders, "
            f"{len(result.snapshot.h5_files)} HDF5 files)"
        )
        self._apply_snapshot_result(result)
        return result.snapshot

    def _report_metadata_read_failure(self, folder: Path, error: str) -> None:
        """Emit one warning per current folder read failure, not one per refresh."""
        folder = folder.resolve()
        if self._metadata_read_errors.get(folder) == error:
            return
        self._metadata_read_errors[folder] = error
        self.navigator_warning.emit(f"Could not read folder metadata for {folder.name}: {error}")

    @staticmethod
    def _series_columns_from_h5_data(h5_data: dict[Path, H5NavigatorData]) -> list[SeriesMetadata]:
        columns: dict[str, SeriesMetadata] = {}
        for entry in h5_data.values():
            for series in entry.series:
                for metadata in series.metadata:
                    columns.setdefault(metadata.column_id, SeriesMetadata(metadata.column_id, metadata.title, ""))
        return list(columns.values())

    @staticmethod
    def _folder_log_lengths_from_h5_data(h5_data: dict[Path, H5NavigatorData]) -> dict[Path, str]:
        ranges_by_folder: dict[Path, tuple[float, float]] = {}
        for h5_path, entry in h5_data.items():
            time_range = entry.time_range
            if time_range is None:
                continue
            folder = resolved_path(h5_path.parent)
            current = ranges_by_folder.get(folder)
            ranges_by_folder[folder] = time_range if current is None else (
                min(current[0], time_range[0]), max(current[1], time_range[1])
            )
        return {
            folder: format_log_length(maximum - minimum)
            for folder, (minimum, maximum) in ranges_by_folder.items()
            if maximum >= minimum
        }

    def refresh_tree(self, *_ignored: object, rescan: bool = False) -> None:
        """Rebuild the tree, re-reading disk data that changed since the last scan.

        With an event loop running, disk access happens in a background job:
        a cached snapshot is shown at once and replaced if the job finds
        changes. ``rescan`` also looks for new or removed folders and files.
        """
        data_folder = self.data_folder()
        if data_folder is None or not data_folder.is_dir():
            self._cancel_snapshot_job()
            self.rebuild_tree()
            return
        if not self._uses_background_refresh():
            if self._initial_load_pending:
                # The first load scans whatever folder is current once the loop runs.
                self.rebuild_tree()
                return
            self._cancel_snapshot_job()
            self._update_source_snapshot(data_folder, rescan=rescan)
            self.rebuild_tree()
            return
        snapshot = self._snapshot_for_root(data_folder)
        if snapshot is None:
            self.rebuild_tree()
        elif not rescan:
            # Filter, Group by and display changes reuse cached data at once.
            self.rebuild_tree()
        self._request_snapshot(data_folder, rescan=rescan)

    def rebuild_tree(self) -> None:
        """Rebuild the tree widget from the current snapshot without disk scans.

        Only the folder rows are made; each folder's contents are made when it is opened
        (``ensure_folder_contents``), or when it is restored as opened.
        """
        tree_state = self.capture_tree_state()
        self.update_active_column_geometry()
        self.clear_row_highlights()
        self._original_root_order = {}
        self._folder_items = {}
        self._folder_plans = {}
        self._view_key = None
        self.tree.clear()
        # The rows marked loading are gone; mark_loading_folders starts it again for new ones.
        self.tree.set_loading_animation(False)
        data_folder = self.data_folder()
        if data_folder is None:
            return
        if not data_folder.is_dir():
            self.add_message_item(f"Folder does not exist: {data_folder}")
            return
        snapshot, partial = self._current_snapshot(data_folder)
        if snapshot is None:
            # Nothing read yet; the Internal log says what is being read.
            return
        startup_mark("navigator snapshot and column preparation started")
        view = self._prepare_view(snapshot, data_folder)
        startup_mark("navigator snapshot and column preparation completed")
        if isinstance(view, str):
            self.add_message_item(view)
            return

        log_lengths_by_folder = self._folder_log_lengths_from_h5_data(snapshot.h5_data)
        self._folder_plans = view.plans
        self._populating_tree = True
        startup_mark("navigator tree widget population started")
        try:
            if view.mode == GROUPING_NONE:
                self.populate_folder_tree(data_folder, view.folders, view.root_files, view.info_index,
                                          log_lengths_by_folder)
            else:
                self.populate_grouped_tree(data_folder, view.folders, view.root_files, view.mode, view.info_index,
                                           log_lengths_by_folder)
        finally:
            self._populating_tree = False
        startup_mark("navigator tree widget population completed")

        startup_mark("navigator tree sorting and display finalization started")
        self.sort_tree_children(self.tree.invisibleRootItem())
        self._original_root_order = {
            id(self.tree.topLevelItem(index)): index for index in range(self.tree.topLevelItemCount())
        }
        self.apply_folder_sort()
        self.apply_diagnostic_row_styles()
        self.tree.collapseAll()
        self.restore_tree_state(tree_state)
        self.resize_data_column_if_unsized()
        self.mark_loading_folders(snapshot.loading if partial else frozenset())
        self._view_key = view.key
        startup_mark("navigator tree sorting and display finalization completed")

    def update_tree_view(self) -> None:
        """Show the current snapshot, changing only the folders whose data changed.

        When the folders shown, their grouping and the filter are the same as the tree's, each
        changed folder's row is updated, and its contents made again if it was opened; the rest
        of the tree, its expansion and selection stay. Otherwise the tree is rebuilt.
        """
        data_folder = self.data_folder()
        if self._view_key is None or data_folder is None or not self._folder_items:
            self.rebuild_tree()
            return
        snapshot, partial = self._current_snapshot(data_folder)
        if snapshot is None:
            self.rebuild_tree()
            return
        view = self._prepare_view(snapshot, data_folder)
        if isinstance(view, str) or view.key != self._view_key:
            self.rebuild_tree()
            return
        log_lengths = self._folder_log_lengths_from_h5_data(snapshot.h5_data)
        changed = [folder for folder, plan in view.plans.items() if not plan.same_as(self._folder_plans.get(folder))]
        self._folder_plans = view.plans
        if changed:
            opened = [folder for folder in changed if not self._folder_items[folder].data(0, ITEM_CONTENT_PENDING_ROLE)]
            state = self.capture_tree_state() if opened else None
            for folder in changed:
                item = self._folder_items[folder]
                values = item.data(0, ITEM_COLUMN_VALUES_ROLE) or {}
                display_name = str(values.get(COLUMN_DATA, folder.name))
                self.set_item_values(item, self.folder_row_values(folder, display_name, view.info_index, log_lengths))
                if folder in opened:
                    for child in [item.child(index) for index in reversed(range(item.childCount()))]:
                        if child.data(0, ITEM_TYPE_ROLE) != ROW_TYPE_FOLDER:
                            item.removeChild(child)
                    self._build_folder_contents(item, folder)
                    self.sort_tree_children(item)
            self.apply_diagnostic_row_styles()
            if state is not None:
                self.restore_tree_state(state)
            self.apply_folder_sort()
        self.mark_loading_folders(snapshot.loading if partial else frozenset())

    def _current_snapshot(self, data_folder: Path) -> tuple[_NavigatorSourceSnapshot | None, bool]:
        """The snapshot to show and whether it is a first read's part."""
        snapshot = self._snapshot_for_root(data_folder)
        partial = snapshot is None and self._partial_snapshot is not None and self._partial_snapshot.root == data_folder
        return (self._partial_snapshot if partial else snapshot), partial

    def _prepare_view(self, snapshot: _NavigatorSourceSnapshot, data_folder: Path) -> "_TreeView | str":
        """The folders to show with the Group by and Filter settings, or a message to show instead.

        Also brings the Group by and Filter choices and the columns up to date with the snapshot.
        """
        h5_files = list(snapshot.h5_files)
        folders = list(snapshot.folders)
        info_index = add_default_info_keys(
            folder_info_index_from_data(snapshot.folder_infos), self.default_info_metadata_keys
        )
        self.current_info_index = info_index
        self.current_series_columns = self._series_columns_from_h5_data(snapshot.h5_data)
        self.update_group_by_options(info_index.keys)
        self.update_filter_options(info_index)
        self.configure_tree_columns(info_index)
        filter_key = self.filter_key_combo.currentText().strip()
        filter_value = self.filter_value_combo.currentText()
        if filter_key and filter_key != FILTER_NONE and filter_value.strip():
            folders = filter_folders_by_metadata(
                folders,
                info_index,
                filter_key,
                filter_value,
                empty_label=FILTER_EMPTY,
            )
            matching_folders = set(folders)
            h5_files = [h5_path for h5_path in h5_files if h5_path.parent in matching_folders]
        mode = self.group_by_combo.currentText().strip() or GROUPING_NONE
        is_info_grouping = mode in self.info_group_keys
        if mode not in GROUPING_OPTIONS and not is_info_grouping:
            needle = mode.casefold()
            matching_folders = {
                folder
                for folder in folders
                if needle in str(folder.relative_to(data_folder)).casefold()
            }
            for folder in tuple(matching_folders):
                parent = folder.parent
                while parent != data_folder:
                    matching_folders.add(parent)
                    parent = parent.parent
            folders = [folder for folder in folders if folder in matching_folders]
            h5_files = [
                h5_path
                for h5_path in h5_files
                if h5_path.parent in matching_folders
            ]
            mode = GROUPING_NONE

        if not folders and not h5_files:
            filter_active = filter_key != FILTER_NONE and bool(filter_value.strip())
            return "No folders match the filter" if filter_active else "No folders or HDF5 files found"
        plans, root_files = self._build_folder_plans(snapshot, folders, h5_files)
        group_paths = (tuple(self.group_path(folder, data_folder, mode, info_index) for folder in folders)
                       if mode != GROUPING_NONE else ())
        key = (data_folder, mode, tuple(folders), tuple(root_files), group_paths,
               self.navigator_control_settings.hide_irrelevant_series)
        return _TreeView(mode, info_index, folders, root_files, plans, key)

    def _folder_links(self, folder: Path, info: FolderInfoData | None) -> FolderLinks | None:
        """A folder's attachment paths: from the background read, or resolved here after an edit."""
        if info is None:
            return None
        snapshot = self._display_snapshot()
        links = snapshot.links.get(resolved_path(folder)) if snapshot is not None else None
        return links if links is not None and links.info is info else folder_links(folder, info)

    def _build_folder_plans(
        self, snapshot: _NavigatorSourceSnapshot, folders: list[Path], h5_files: list[Path],
    ) -> tuple[dict[Path, "FolderPlan"], list[Path]]:
        """Each folder's H5 files and linked audio, and the H5 files outside the folders.

        Audio stored elsewhere, such as beside a video in a parent folder, appears only in the
        linking folder's Media group. Audio H5 files whose link was removed are left out, like
        the removed link.
        """
        folder_set = set(folders)
        scanned = set(h5_files)
        linked: set[Path] = set()
        attached: dict[Path, list[Path]] = {}
        for folder in folders:
            links = self._folder_links(folder, snapshot.folder_infos.get(folder))
            for audio_path, exists in links.audio if links is not None else ():
                linked.add(audio_path)
                if audio_path.parent != folder and (audio_path in scanned or exists):
                    attached.setdefault(folder, []).append(audio_path)
        attached_files = {path for paths in attached.values() for path in paths}
        own: dict[Path, list[Path]] = {}
        root_files: list[Path] = []
        for path in h5_files:
            if path in attached_files or (path.name.endswith(AUDIO_SUFFIX) and path not in linked):
                continue
            if path.parent in folder_set:
                own.setdefault(path.parent, []).append(path)
            else:
                root_files.append(path)
        hide_irrelevant = self.navigator_control_settings.hide_irrelevant_series
        plans: dict[Path, FolderPlan] = {}
        for folder in folders:
            audio, files = tuple(attached.get(folder, ())), tuple(own.get(folder, ()))
            flags, data = 0, []
            for path in (*audio, *files):
                entry = snapshot.h5_data.get(path)
                data.append(entry)
                for series in entry.series if entry is not None else ():
                    if hide_irrelevant and not series.visible:
                        continue
                    for metadata in series.metadata:
                        if metadata.column_id == "warning":
                            flags |= diagnostic_flags_from_warning(metadata.value)
            plans[folder] = FolderPlan(
                files, audio, flags,
                (snapshot.folder_infos.get(folder), snapshot.source_rows.get(folder), *data))
        return plans, root_files

    def mark_loading_folders(self, loading: frozenset[Path]) -> None:
        """A loading circle on each folder row still being read; none once the read is done."""
        previous = self._updating_info_items
        self._updating_info_items = True
        try:
            for folder, item in self._folder_items.items():
                still_loading = folder in loading
                if bool(item.data(0, ITEM_LOADING_ROLE)) != still_loading:
                    item.setData(0, ITEM_LOADING_ROLE, still_loading)
        finally:
            self._updating_info_items = previous
        self.tree.set_loading_animation(any(folder in loading for folder in self._folder_items))

    def update_group_by_options(self, info_keys: set[str]) -> None:
        current_text = self.group_by_combo.currentText().strip()
        self.info_group_keys = set(info_keys)

        self.group_by_combo.blockSignals(True)
        self.group_by_combo.clear()
        self.group_by_combo.addItems(GROUPING_OPTIONS)
        for key in sorted(info_keys, key=str.casefold):
            if key not in GROUPING_OPTIONS:
                self.group_by_combo.addItem(key)
        if current_text:
            index = self.group_by_combo.findText(current_text)
            if index >= 0:
                self.group_by_combo.setCurrentIndex(index)
            else:
                self.group_by_combo.setEditText(current_text)
        else:
            self.group_by_combo.setCurrentText(GROUPING_NONE)
        self.group_by_combo.blockSignals(False)

    def update_filter_options(self, info_index: FolderInfoIndex) -> None:
        current_key = self.filter_key_combo.currentText().strip() or FILTER_NONE
        current_value = self.filter_value_combo.currentText()

        self.filter_key_combo.blockSignals(True)
        self.filter_key_combo.clear()
        self.filter_key_combo.addItem(FILTER_NONE)
        self.filter_key_combo.addItems(sorted(info_index.keys, key=str.casefold))
        key_index = self.filter_key_combo.findText(current_key)
        self.filter_key_combo.setCurrentIndex(max(0, key_index))
        self.filter_key_combo.blockSignals(False)

        self.update_filter_value_options(current_value=current_value)

    def update_filter_value_options(self, *, current_value: str = "") -> None:
        key = self.filter_key_combo.currentText().strip()
        enabled = bool(key and key != FILTER_NONE)
        values = metadata_values_for_key(self.current_info_index, key) if enabled else []

        self.filter_value_combo.blockSignals(True)
        self.filter_value_combo.clear()
        if any(not value.strip() for value in values):
            self.filter_value_combo.addItem(FILTER_EMPTY)
        self.filter_value_combo.addItems(value for value in values if value.strip())
        self.filter_value_combo.setEditText(current_value if enabled else "")
        self.filter_value_combo.setEnabled(enabled)
        self.filter_value_combo.blockSignals(False)

    def on_filter_key_changed(self, key: str) -> None:
        self.update_filter_value_options()
        if key.strip() != FILTER_NONE:
            self.filter_value_combo.setFocus()
        self.refresh_tree()

    def configure_tree_columns(self, info_index: FolderInfoIndex) -> None:
        self.clear_row_highlights()
        self.columns = columns_for_category(
            self.active_column_category,
            info_index,
            self.current_series_columns,
        )
        self.column_indexes = {
            column.column_id: index
            for index, column in enumerate(self.columns)
        }

        self._applying_column_layout = True
        try:
            self.tree.setColumnCount(PHYSICAL_COLUMN_COUNT)
            self.tree.setHeaderLabels(["Data", "Value"])
            self.tree.headerItem().setToolTip(PHYSICAL_DATA_COLUMN, "Data")
            self.tree.headerItem().setToolTip(PHYSICAL_VALUE_COLUMN, "Row-specific values")
            self.apply_saved_column_widths()
            # Values are held in ITEM_COLUMN_VALUES_ROLE and the custom
            # delegate resolves the active logical columns while painting.
            # Reapplying every tree item on a row-type switch is redundant
            # and causes a large first-expansion pause for big data folders.
        finally:
            self._applying_column_layout = False
        self.update_physical_value_column_width()
        self.logical_header.update()
        self.tree.viewport().update()
        self.update_row_highlights()

    def current_column_order(self) -> list[str]:
        order = self.active_column_profile().order
        if order:
            return sanitize_column_order(order)
        return sanitize_column_order([column.column_id for column in self.logical_columns_for_category(self.active_column_category)])

    def current_column_widths(self) -> dict[str, int]:
        widths = dict(self.active_column_profile().widths)
        data_width = self.tree.header().sectionSize(PHYSICAL_DATA_COLUMN)
        if data_width > 0:
            widths[COLUMN_DATA] = data_width
        return widths

    def update_active_column_geometry(self) -> None:
        if not self.columns:
            return
        profile = self.active_column_profile()
        profile.order = sanitize_column_order(self.current_column_order())
        widths = self.current_column_widths()
        for column_id in SHARED_WIDTH_COLUMNS:
            width = widths.pop(column_id, None)
            if width is not None:
                self.column_settings.shared_widths[column_id] = width
            profile.widths.pop(column_id, None)
        profile.widths.update(widths)

    def apply_saved_column_widths(self) -> None:
        header = self.tree.header()
        width = self.column_settings.shared_widths.get(COLUMN_DATA)
        if width is not None and width > 0:
            header.resizeSection(PHYSICAL_DATA_COLUMN, width)

    def required_value_column_width(self) -> int:
        """Return the widest visible logical-value configuration for any row type."""
        categories = (COLUMN_CATEGORY_FOLDER, COLUMN_CATEGORY_SERIES, COLUMN_CATEGORY_INFO, COLUMN_CATEGORY_SOURCE)
        return max(
            (
                sum(
                    profile.widths.get(column.column_id, default_logical_column_width(column))
                    for column in self.value_columns_for_category(category)
                )
                for category in categories
                for profile in [column_profile_settings(self.column_settings, category)]
            ),
            default=0,
        )

    def update_physical_value_column_width(self) -> None:
        """Keep the physical column wide enough for every logical row layout."""
        header = self.tree.header()
        data_width = header.sectionSize(PHYSICAL_DATA_COLUMN)
        available_width = max(0, self.tree.viewport().width() - data_width)
        target_width = max(self.required_value_column_width(), available_width)
        if header.sectionSize(PHYSICAL_VALUE_COLUMN) != target_width:
            header.resizeSection(PHYSICAL_VALUE_COLUMN, target_width)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        QTimer.singleShot(0, self, self.update_physical_value_column_width)

    def resize_data_column_if_unsized(self) -> None:
        if COLUMN_DATA in self.column_settings.shared_widths:
            return
        self.tree.resizeColumnToContents(PHYSICAL_DATA_COLUMN)

    def row_highlight_brush(self) -> QBrush:
        color = QColor(self.tree.palette().color(QPalette.ColorRole.Highlight))
        color.setAlpha(ROW_HIGHLIGHT_ALPHA)
        return QBrush(color)

    def row_hover_brush(self) -> QBrush:
        color = QColor(self.tree.palette().color(QPalette.ColorRole.Highlight))
        color.setAlpha(ROW_HOVER_ALPHA)
        return QBrush(color)

    def on_hovered_tree_item_changed(self, item: QTreeWidgetItem | None) -> None:
        previous_item = self._hovered_row_item
        self._hovered_row_item = item
        self.update_row_highlights([previous_item] if previous_item is not None else [])

    def update_row_highlights(self, additional_items: list[QTreeWidgetItem] | None = None) -> None:
        if self._updating_row_highlights:
            return
        highlighted_items = self.selected_row_items()
        highlighted_ids = {id(item) for item in highlighted_items}
        affected_items = {
            id(item): item
            for item in [
                *self._highlighted_row_items,
                *highlighted_items,
                *([] if self._hovered_row_item is None else [self._hovered_row_item]),
                *(additional_items or []),
            ]
        }

        self._updating_row_highlights = True
        try:
            row_brush = self.row_highlight_brush()
            hover_brush = self.row_hover_brush()
            hovered_id = id(self._hovered_row_item) if self._hovered_row_item is not None else None
            for item_id, item in affected_items.items():
                if item_id in highlighted_ids:
                    brush = row_brush
                elif item_id == hovered_id:
                    brush = hover_brush
                else:
                    brush = QBrush()
                self.set_row_background(item, brush)
        finally:
            self._updating_row_highlights = False
        self._highlighted_row_items = highlighted_items

    def selected_row_items(self) -> list[QTreeWidgetItem]:
        selection_model = self.tree.selectionModel()
        if selection_model is None:
            return []
        current_index = selection_model.currentIndex()
        if not current_index.isValid():
            return []
        current_item = self.tree.itemFromIndex(current_index)
        return [current_item] if current_item is not None else []

    def clear_row_highlights(self) -> None:
        self._updating_row_highlights = True
        try:
            for item in self.tree_items():
                self.set_row_background(item, QBrush())
        finally:
            self._updating_row_highlights = False
        self._highlighted_row_items = []

    def set_row_background(self, item: QTreeWidgetItem, brush: QBrush) -> None:
        for column_index in range(self.tree.columnCount()):
            item.setBackground(column_index, brush)

    def set_row_foreground(self, item: QTreeWidgetItem, brush: QBrush) -> None:
        for column_index in range(self.tree.columnCount()):
            item.setForeground(column_index, brush)

    def diagnostic_row_style(self, item: QTreeWidgetItem) -> dict[str, object] | None:
        flags = int(item.data(0, ITEM_DIAGNOSTIC_FLAGS_ROLE) or 0)
        warning_flags = {"sample_loss": DIAGNOSTIC_SAMPLE_LOSS, "clipping": DIAGNOSTIC_CLIPPING}
        for name in self.diagnostic_row_settings["order"]:
            style = self.diagnostic_row_settings[name]
            if style["enabled"] and flags & warning_flags[name]:
                return style
        return None

    def diagnostic_text_color(self, item: QTreeWidgetItem) -> QColor | None:
        style = self.diagnostic_row_style(item)
        return QColor(str(style["color"])) if style is not None else None

    def apply_diagnostic_row_styles(self) -> None:
        previous_updating = self._updating_row_highlights
        self._updating_row_highlights = True
        try:
            for item in self.tree_items():
                item.setData(0, ITEM_DIAGNOSTIC_FLAGS_ROLE, 0)
                self.set_row_background(item, QBrush())
                self.set_row_foreground(item, QBrush())
                font = item.font(PHYSICAL_DATA_COLUMN)
                font.setBold(False)
                font.setItalic(False)
                for column_index in range(self.tree.columnCount()):
                    item.setFont(column_index, font)

            for item in self.tree_items():
                if item.data(0, ITEM_TYPE_ROLE) != ROW_TYPE_SERIES:
                    continue
                flags = diagnostic_flags_from_item(item)
                if not flags:
                    continue
                current: QTreeWidgetItem | None = item
                while current is not None:
                    current.setData(
                        0,
                        ITEM_DIAGNOSTIC_FLAGS_ROLE,
                        int(current.data(0, ITEM_DIAGNOSTIC_FLAGS_ROLE) or 0) | flags,
                    )
                    current = current.parent()

            # A folder's flags come from its data, so they show before its contents are made.
            for folder, item in self._folder_items.items():
                plan = self._folder_plans.get(folder)
                current = item if plan is not None and plan.flags else None
                while current is not None:
                    current.setData(
                        0,
                        ITEM_DIAGNOSTIC_FLAGS_ROLE,
                        int(current.data(0, ITEM_DIAGNOSTIC_FLAGS_ROLE) or 0) | plan.flags,
                    )
                    current = current.parent()

            for item in self.tree_items():
                style = self.diagnostic_row_style(item)
                if style is None:
                    continue
                self.set_row_foreground(item, QBrush(QColor(str(style["color"]))))
                font = item.font(PHYSICAL_DATA_COLUMN)
                font.setBold(bool(style["bold"]))
                font.setItalic(bool(style["italic"]))
                for column_index in range(self.tree.columnCount()):
                    item.setFont(column_index, font)
            self.apply_source_row_styles()
        finally:
            self._updating_row_highlights = previous_updating

    def apply_source_row_styles(self) -> None:
        """Source data rows not imported or exported yet are dimmed and in italics."""
        dimmed = QBrush(self.tree.palette().color(QPalette.ColorRole.PlaceholderText))
        for item in self.tree_items():
            row = item.data(0, ITEM_SOURCE_ROLE)
            if not isinstance(row, SourceRow) or row.state not in {STATE_NEW, STATE_MISSING, STATE_UNSUPPORTED, STATE_INCOMPLETE}:
                continue
            self.set_row_foreground(item, dimmed)
            font = item.font(PHYSICAL_DATA_COLUMN)
            font.setItalic(True)
            for column_index in range(self.tree.columnCount()):
                item.setFont(column_index, font)

    def column_at(self, column_index: int, item: QTreeWidgetItem | None = None) -> TreeColumn | None:
        if column_index == PHYSICAL_DATA_COLUMN:
            return self.logical_column_for_id(COLUMN_DATA, column_category_for_item(item))
        if column_index == PHYSICAL_VALUE_COLUMN:
            selected = self.selected_logical_column_for_item(item)
            if selected is not None:
                return selected
        return None

    def set_item_progress(
        self,
        item: QTreeWidgetItem,
        completed: int,
        total: int,
        text: str,
        eta_text: str,
    ) -> None:
        item.setData(0, ITEM_PROGRESS_COMPLETED_ROLE, completed)
        item.setData(0, ITEM_PROGRESS_TOTAL_ROLE, total)
        item.setData(0, ITEM_PROGRESS_TEXT_ROLE, text)
        item.setData(0, ITEM_PROGRESS_ETA_ROLE, eta_text)
        self.reapply_item_values(item)
        # Clear the physical DisplayRole text while the custom delegate owns
        # this cell. Some Qt styles paint it even when the delegate disables
        # HasDisplay, which caused the duplicate folder label.
        item.setText(PHYSICAL_DATA_COLUMN, "")
        self.tree.viewport().update(self.tree.visualItemRect(item))

    def clear_item_progress(self, item: QTreeWidgetItem) -> None:
        item.setData(0, ITEM_PROGRESS_COMPLETED_ROLE, None)
        item.setData(0, ITEM_PROGRESS_TOTAL_ROLE, None)
        item.setData(0, ITEM_PROGRESS_TEXT_ROLE, None)
        item.setData(0, ITEM_PROGRESS_ETA_ROLE, None)
        self.reapply_item_values(item)
        self.tree.viewport().update(self.tree.visualItemRect(item))

    def active_column_profile(self) -> ColumnProfileSettings:
        return column_profile_settings(self.column_settings, self.active_column_category)

    def logical_columns_for_category(self, category: str) -> list[TreeColumn]:
        columns = columns_for_category(category, self.current_info_index, self.current_series_columns)
        by_id = {column.column_id: column for column in columns}
        profile = column_profile_settings(self.column_settings, category)
        ordered_ids = [
            column_id
            for column_id in sanitize_column_order(profile.order)
            if column_id in by_id
        ]
        ordered_ids.extend(column.column_id for column in columns if column.column_id not in ordered_ids)
        return [by_id[column_id] for column_id in ordered_ids]

    def value_columns_for_category(self, category: str) -> list[TreeColumn]:
        profile = column_profile_settings(self.column_settings, category)
        return [
            column
            for column in self.logical_columns_for_category(category)
            if column.column_id != COLUMN_DATA and column.column_id not in profile.hidden
        ]

    def value_columns_for_item(self, item: QTreeWidgetItem | None) -> list[TreeColumn]:
        return self.value_columns_for_category(column_category_for_item(item))

    def logical_column_for_id(self, column_id: str, category: str) -> TreeColumn | None:
        for column in self.logical_columns_for_category(category):
            if column.column_id == column_id:
                return column
        return None

    def logical_column_width(self, column: TreeColumn) -> int:
        if column.column_id == COLUMN_DATA:
            return self.tree.header().sectionSize(PHYSICAL_DATA_COLUMN)
        return self.active_column_profile().widths.get(column.column_id, default_logical_column_width(column))

    def resize_logical_column(self, column_id: str, width: int) -> None:
        column = self.logical_column_for_id(column_id, self.active_column_category)
        if column is None:
            return
        if column.column_id == COLUMN_DATA:
            self.tree.header().resizeSection(PHYSICAL_DATA_COLUMN, max(60, int(width)))
            return
        self.active_column_profile().widths[column_id] = max(30, int(width))
        self.update_physical_value_column_width()
        self.logical_header.update()
        self.tree.viewport().update()

    def logical_header_sections(self) -> list[tuple[TreeColumn, QRect]]:
        data_width = self.tree.header().sectionSize(PHYSICAL_DATA_COLUMN)
        value_width = self.tree.header().sectionSize(PHYSICAL_VALUE_COLUMN)
        horizontal_offset = self.tree.horizontalScrollBar().value()
        data_column = self.logical_column_for_id(COLUMN_DATA, self.active_column_category)
        if data_column is None:
            data_column = TreeColumn(COLUMN_DATA, "Data", always_visible=True)
        value_rect = QRect(
            data_width - horizontal_offset,
            0,
            value_width,
            self.logical_header.height(),
        )
        return [
            (data_column, QRect(-horizontal_offset, 0, data_width, self.logical_header.height())),
            *self.logical_sections(
                self.value_columns_for_category(self.active_column_category),
                value_rect,
                self.active_column_profile(),
            ),
        ]

    def value_column_sections(
        self,
        item: QTreeWidgetItem,
        value_rect: QRect,
    ) -> list[tuple[TreeColumn, QRect]]:
        category = column_category_for_item(item)
        positional_widths = None
        if self.navigator_control_settings.align_columns_of_different_type:
            active_profile = self.active_column_profile()
            positional_widths = [
                active_profile.widths.get(column.column_id, default_logical_column_width(column))
                for column in self.value_columns_for_category(self.active_column_category)
            ]
        return self.logical_sections(
            self.value_columns_for_item(item),
            value_rect,
            column_profile_settings(self.column_settings, category),
            positional_widths,
        )

    def logical_sections(
        self,
        columns: list[TreeColumn],
        rect: QRect,
        profile: ColumnProfileSettings,
        positional_widths: list[int] | None = None,
    ) -> list[tuple[TreeColumn, QRect]]:
        sections: list[tuple[TreeColumn, QRect]] = []
        x = rect.x()
        right = rect.x() + rect.width()
        for index, column in enumerate(columns):
            width = (
                positional_widths[index]
                if positional_widths is not None and index < len(positional_widths)
                else profile.widths.get(column.column_id, default_logical_column_width(column))
            )
            if index == len(columns) - 1:
                width = max(width, right - x)
            cell_rect = QRect(x, rect.y(), max(0, min(width, right - x)), rect.height())
            sections.append((column, cell_rect))
            x += width
            if x >= right:
                break
        return sections

    def logical_header_resize_column(self, position: QPoint) -> TreeColumn | None:
        for column, rect in self.logical_header_sections()[:-1]:
            if abs(position.x() - rect.right()) <= LOGICAL_HEADER_RESIZE_MARGIN:
                return column
        return None

    def logical_header_sort_rect(self, rect: QRect) -> QRect | None:
        if self.active_column_category != COLUMN_CATEGORY_FOLDER:
            return None
        return QRect(
            rect.right() - LOGICAL_HEADER_RESIZE_MARGIN - LOGICAL_HEADER_SORT_WIDTH,
            rect.top(), LOGICAL_HEADER_SORT_WIDTH, rect.height(),
        )

    def logical_header_sort_column(self, position: QPoint) -> TreeColumn | None:
        for column, rect in self.logical_header_sections():
            sort_rect = self.logical_header_sort_rect(rect)
            if sort_rect is not None and rect.contains(position) and sort_rect.contains(position):
                return column
        return None

    def cycle_folder_sort(self, column_id: str) -> None:
        if self.active_column_category != COLUMN_CATEGORY_FOLDER:
            return
        if self.folder_sort_column_id != column_id:
            self.folder_sort_column_id = column_id
            self.folder_sort_direction = 1
        else:
            self.folder_sort_direction = {0: 1, 1: -1, -1: 0}[self.folder_sort_direction]
            if not self.folder_sort_direction:
                self.folder_sort_column_id = ""
        self.apply_folder_sort()
        self.logical_header.update()

    def apply_folder_sort(self) -> None:
        """Reorder only root folder siblings, preserving their complete subtrees."""
        if self._populating_tree or not self._original_root_order:
            return
        root = self.tree.invisibleRootItem()
        current = [root.child(index) for index in range(root.childCount())]
        original = sorted(current, key=lambda item: self._original_root_order.get(id(item), len(current)))
        folders = [item for item in original if item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_FOLDER]
        if self.folder_sort_direction:
            values = {}
            for item in folders:
                # Progress text temporarily replaces Data when painting, but is not a folder name.
                stored = item.data(0, ITEM_COLUMN_VALUES_ROLE) or {}
                values[id(item)] = str(stored.get(self.folder_sort_column_id, "")).strip()
            populated = [item for item in folders if values[id(item)]]
            empty = [item for item in folders if not values[id(item)]]
            numeric_values = {}
            try:
                for item in populated:
                    value = values[id(item)]
                    if self.folder_sort_column_id == COLUMN_LOG_LENGTH:
                        number = 0.0
                        for part in value.removesuffix("s").split(":"):
                            number = number * 60 + float(part)
                    elif self.folder_sort_column_id != COLUMN_DATA:
                        number = float(value)
                    else:
                        raise ValueError
                    if not math.isfinite(number):
                        raise ValueError
                    numeric_values[id(item)] = number
            except ValueError:
                numeric_values = {}
            populated.sort(
                key=lambda item: numeric_values[id(item)] if numeric_values else values[id(item)].casefold(),
                reverse=self.folder_sort_direction == -1,
            )
            folders = populated + empty
        folder_iterator = iter(folders)
        ordered = [next(folder_iterator) if item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_FOLDER else item for item in original]
        if ordered == current:
            return

        expanded = [item for item in self.tree_items() if item.isExpanded()]
        selected = [(self.tree.itemFromIndex(index), index.column()) for index in self.tree.selectedIndexes()]
        current_item, current_column = self.tree.currentItem(), self.tree.currentColumn()
        horizontal, vertical = self.tree.horizontalScrollBar().value(), self.tree.verticalScrollBar().value()
        selection_model = self.tree.selectionModel()
        self.clear_hovered_logical_cell()
        self.tree.setUpdatesEnabled(False)
        try:
            with QSignalBlocker(self.tree), QSignalBlocker(selection_model):
                root.takeChildren()
                root.addChildren(ordered)
                for item in expanded:
                    item.setExpanded(True)
                if current_item is not None:
                    self.tree.setCurrentItem(current_item, current_column, QItemSelectionModel.SelectionFlag.NoUpdate)
                for item, column in selected:
                    selection_model.select(self.tree.indexFromItem(item, column), QItemSelectionModel.SelectionFlag.Select)
                self.tree.horizontalScrollBar().setValue(horizontal)
                self.tree.verticalScrollBar().setValue(vertical)
        finally:
            self.tree.setUpdatesEnabled(True)

    def logical_header_draggable_column(self, position: QPoint) -> TreeColumn | None:
        for column, rect in self.logical_header_sections():
            if rect.contains(position) and column.column_id != COLUMN_DATA:
                return column
        return None

    def logical_header_drop_target(self, position: QPoint) -> tuple[TreeColumn, bool] | None:
        columns = self.value_columns_for_category(self.active_column_category)
        sections = self.logical_header_sections()[1:]
        if not columns or not sections:
            return None
        for column, rect in sections:
            if position.x() < rect.center().x():
                return column, False
        return sections[-1][0], True

    def update_logical_cell_from_view_position(self, position: QPoint) -> None:
        index = self.tree.indexAt(position)
        if not index.isValid():
            self._selected_logical_column_id = ""
            self.logical_header.update()
            return
        item = self.tree.itemFromIndex(index)
        if item is None:
            return
        if index.column() == PHYSICAL_DATA_COLUMN:
            self._selected_logical_column_id = COLUMN_DATA
        elif index.column() == PHYSICAL_VALUE_COLUMN:
            self._selected_logical_column_id = self.logical_column_id_at_value_position(item, position) or ""
        self.logical_header.update()

    def update_hovered_logical_cell_from_view_position(self, position: QPoint) -> None:
        index = self.tree.indexAt(position)
        if not index.isValid():
            self.clear_hovered_logical_cell()
            return
        item = self.tree.itemFromIndex(index)
        if item is None:
            self.clear_hovered_logical_cell()
            return
        if index.column() == PHYSICAL_VALUE_COLUMN:
            self._hovered_logical_column_id = self.logical_column_id_at_value_position(item, position) or ""
            self._hovered_value_index = QPersistentModelIndex(index)
        else:
            self.clear_hovered_logical_cell()
            return
        self.tree.viewport().update()

    def clear_hovered_logical_cell(self) -> None:
        if not self._hovered_logical_column_id and self._hovered_value_index is None:
            return
        self._hovered_logical_column_id = ""
        self._hovered_value_index = None
        self.tree.viewport().update()

    def logical_column_id_at_value_position(self, item: QTreeWidgetItem, position: QPoint) -> str | None:
        value_rect = self.tree.visualRect(self.tree.indexFromItem(item, PHYSICAL_VALUE_COLUMN))
        for column, rect in self.value_column_sections(item, value_rect):
            if rect.contains(position):
                return column.column_id
        return None

    def selected_logical_column_for_item(self, item: QTreeWidgetItem | None) -> TreeColumn | None:
        category = column_category_for_item(item)
        if self._selected_logical_column_id:
            column = self.logical_column_for_id(self._selected_logical_column_id, category)
            if column is not None:
                return column
        visible_columns = self.value_columns_for_category(category)
        return visible_columns[0] if visible_columns else None

    def hovered_logical_column_for_item(self, item: QTreeWidgetItem | None) -> TreeColumn | None:
        if (
            item is None
            or not self._hovered_logical_column_id
            or self._hovered_value_index is None
            or self._hovered_value_index
            != self.tree.indexFromItem(item, PHYSICAL_VALUE_COLUMN)
        ):
            return None
        return self.logical_column_for_id(
            self._hovered_logical_column_id,
            column_category_for_item(item),
        )

    def selected_plain_text(self) -> str:
        item = self.tree.currentItem()
        if item is None:
            return ""
        if self._selected_logical_column_id:
            selected_column = self.logical_column_for_id(
                self._selected_logical_column_id,
                column_category_for_item(item),
            )
            if selected_column is not None:
                return self.item_logical_value(item, selected_column.column_id)
        column = self.column_at(self.tree.currentColumn(), item)
        if column is None:
            return ""
        return self.item_logical_value(item, column.column_id)

    def item_logical_value(self, item: QTreeWidgetItem, column_id: str) -> str:
        if column_id == COLUMN_DATA:
            progress_text = item.data(0, ITEM_PROGRESS_TEXT_ROLE)
            if progress_text is not None:
                return str(progress_text)
        return self.item_logical_value_without_progress(item, column_id)

    def item_logical_value_without_progress(self, item: QTreeWidgetItem, column_id: str) -> str:
        values = item.data(0, ITEM_COLUMN_VALUES_ROLE)
        if isinstance(values, dict):
            return str(values.get(column_id, ""))
        return ""

    def item_editable_value(self, item: QTreeWidgetItem, column_id: str) -> str:
        """Return the text an editor starts with; placeholders start empty."""
        if (
            item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_DETAILED_INFO
            and bool(item.data(0, ITEM_INFO_EMPTY_ROLE))
        ):
            return ""
        return self.item_logical_value(item, column_id)

    def is_folder_info_column_editable(self, item: QTreeWidgetItem, column: TreeColumn) -> bool:
        if item.data(0, ITEM_TYPE_ROLE) != ROW_TYPE_FOLDER or not column.info_key:
            return False
        raw_path = item.data(0, ITEM_PATH_ROLE)
        if not raw_path:
            return False
        folder = Path(str(raw_path)).resolve()
        editable_values = self.current_info_index.editable_values_by_folder.get(folder, {})
        values = self.current_info_index.values_by_folder.get(folder, {})
        return column.info_key in editable_values or column.info_key not in values

    def begin_logical_cell_edit(self, position: QPoint) -> bool:
        """Open the existing delegate editor for an editable Value subcell."""

        index = self.tree.indexAt(position)
        if not index.isValid() or index.column() != PHYSICAL_VALUE_COLUMN:
            return False
        item = self.tree.itemFromIndex(index)
        if item is None:
            return False
        self.update_logical_cell_from_view_position(position)
        column = self.selected_logical_column_for_item(item)
        if column is None:
            return False
        item_type = item.data(0, ITEM_TYPE_ROLE)
        editable = (
            item_type == ROW_TYPE_FOLDER
            and self.is_folder_info_column_editable(item, column)
        ) or (
            item_type == ROW_TYPE_DETAILED_INFO
            and bool(item.data(0, ITEM_INFO_EDITABLE_ROLE))
            and column.column_id == COLUMN_INFO_PAIR_VALUE
        )
        if not editable:
            return False
        return self.tree.edit(index)

    def on_current_tree_item_changed(self, item: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None) -> None:
        category = column_category_for_item(item)
        if category == self.active_column_category:
            self.logical_header.update()
            return
        self.update_active_column_geometry()
        self.active_column_category = category
        self.configure_tree_columns(self.current_info_index)
        # Keep expansion responsive even when an editor or OneDrive briefly
        # holds the settings file open.
        self.schedule_column_settings_save(SETTINGS_SAVE_DEBOUNCE_MS)

    def restore_horizontal_scroll_position(self, position: int) -> None:
        scrollbar = self.tree.horizontalScrollBar()
        QTimer.singleShot(
            0, self,
            lambda: QTimer.singleShot(
                0, self,
                lambda: scrollbar.setValue(min(position, scrollbar.maximum())),
            ),
        )

    def on_tree_item_expanded(self, item: QTreeWidgetItem) -> None:
        self.ensure_folder_contents(item)
        if self._restoring_tree_expansion:
            return
        self.tree.setCurrentItem(
            item,
            PHYSICAL_DATA_COLUMN,
            QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )

    def schedule_group_by_refresh(self, *_args) -> None:  # type: ignore[no-untyped-def]
        """Avoid rebuilding a large navigator tree for every typed character."""
        self.group_by_refresh_timer.start()

    def show_logical_header_context_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        profile = self.active_column_profile()
        clicked_column = None
        if self.sender() is self.logical_header:
            for column, rect in self.logical_header_sections():
                if rect.contains(position):
                    clicked_column = column
                    break
        if clicked_column is not None:
            move_left_action = menu.addAction(f"Move {clicked_column.short_title} left")
            move_left_action.triggered.connect(lambda: self.move_logical_column(clicked_column.column_id, -1))
            move_right_action = menu.addAction(f"Move {clicked_column.short_title} right")
            move_right_action.triggered.connect(lambda: self.move_logical_column(clicked_column.column_id, 1))
            menu.addSeparator()
        for column in self.logical_columns_for_category(self.active_column_category):
            action = menu.addAction(column.title)
            action.setCheckable(True)
            action.setChecked(column.column_id not in profile.hidden or column.always_visible)
            action.setEnabled(not column.always_visible)
            action.toggled.connect(
                lambda checked, column_id=column.column_id: self.set_column_visible(column_id, checked)
            )
        sender = self.sender()
        if sender is self.logical_header:
            global_position = self.logical_header.mapToGlobal(position)
        else:
            global_position = self.tree.header().mapToGlobal(position)
        menu.exec(global_position)

    def move_logical_column(self, column_id: str, direction: int) -> None:
        visible_columns = self.value_columns_for_category(self.active_column_category)
        visible_ids = [column.column_id for column in visible_columns]
        if column_id not in visible_ids:
            return
        index = visible_ids.index(column_id)
        target = index + direction
        if target < 0 or target >= len(visible_columns):
            return
        self.move_logical_column_to(
            column_id,
            visible_columns[target].column_id,
            place_after=direction > 0,
        )

    def move_logical_column_to(
        self,
        column_id: str,
        target_column_id: str,
        place_after: bool,
    ) -> None:
        if column_id in {COLUMN_DATA, target_column_id}:
            return
        profile = self.active_column_profile()
        order = [
            column.column_id
            for column in self.logical_columns_for_category(self.active_column_category)
        ]
        if column_id not in order or target_column_id not in order:
            return
        order.remove(column_id)
        target_index = order.index(target_column_id)
        order.insert(target_index + int(place_after), column_id)
        profile.order = sanitize_column_order(order)
        self.update_physical_value_column_width()
        self.logical_header.update()
        self.tree.viewport().update()
        self.save_column_settings()

    def set_column_visible(self, column_id: str, visible: bool) -> None:
        column = self.logical_column_for_id(column_id, self.active_column_category)
        if column is None:
            return
        if column.always_visible:
            visible = True
        profile = self.active_column_profile()
        if visible:
            profile.hidden.discard(column_id)
        else:
            profile.hidden.add(column_id)
        self.update_physical_value_column_width()
        self.logical_header.update()
        self.tree.viewport().update()
        self.save_column_settings()

    def on_physical_header_section_resized(self, logical_index: int, _old_size: int, _new_size: int) -> None:
        if self._applying_column_layout:
            return
        self.update_physical_value_column_width()
        self.logical_header.update()
        if logical_index == PHYSICAL_DATA_COLUMN:
            self.schedule_column_settings_save(SETTINGS_SAVE_DEBOUNCE_MS)

    def save_column_settings(self) -> None:
        if self.settings_path is None:
            return
        self.update_active_column_geometry()
        self._column_settings_dirty = True
        self.flush_column_settings()

    def schedule_column_settings_save(self, delay_ms: int) -> None:
        if self.settings_path is None:
            return
        self.update_active_column_geometry()
        self._column_settings_dirty = True
        self.column_settings_save_timer.start(delay_ms)

    def flush_column_settings(self) -> None:
        if self.settings_path is None or not self._column_settings_dirty:
            return
        try:
            save_column_settings(self.settings_path, self.column_settings)
        except OSError:
            self._column_settings_save_retries += 1
            if self._column_settings_save_retries <= SETTINGS_SAVE_MAX_RETRIES:
                self.column_settings_save_timer.start(SETTINGS_SAVE_RETRY_MS)
            return
        self._column_settings_dirty = False
        self._column_settings_save_retries = 0

    def on_tree_item_changed(self, item: QTreeWidgetItem, column_index: int) -> None:
        if (
            self._populating_tree
            or self._applying_column_layout
            or self._updating_row_highlights
            or self._updating_info_items
        ):
            return
        item_type = item.data(0, ITEM_TYPE_ROLE)
        column = self.column_at(column_index, item)
        if column is None:
            return
        if item_type == ROW_TYPE_FOLDER:
            self.on_folder_info_column_changed(item, column, self.item_logical_value(item, column.column_id))
            return
        if item_type == ROW_TYPE_DETAILED_INFO:
            self.on_detailed_info_column_changed(item, column, self.item_logical_value(item, column.column_id))

    def on_logical_cell_edited(self, item: QTreeWidgetItem, column: TreeColumn, value: str) -> None:
        item_type = item.data(0, ITEM_TYPE_ROLE)
        if item_type == ROW_TYPE_FOLDER:
            self.on_folder_info_column_changed(item, column, value)
            return
        if item_type == ROW_TYPE_DETAILED_INFO:
            self.on_detailed_info_column_changed(item, column, value)

    def on_folder_info_column_changed(self, item: QTreeWidgetItem, column: TreeColumn, value: str) -> None:
        if not self.is_folder_info_column_editable(item, column):
            return
        raw_path = item.data(0, ITEM_PATH_ROLE)
        if not raw_path:
            return
        folder = Path(str(raw_path)).resolve()
        try:
            info = read_info_file(folder)
            # Recheck the file in case metadata changed since the index was built.
            if column.info_key in info.readonly:
                return
            info.editable[column.info_key] = value
            write_info_file(folder, info)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.reapply_item_values(item)
            self.metadata_write_failed.emit(folder, str(error))
            return
        values = item.data(0, ITEM_COLUMN_VALUES_ROLE)
        if isinstance(values, dict):
            values[column.column_id] = value
            self.set_item_values(item, values)
        self.store_folder_info(folder, info)
        self.update_info_subtree_values(folder, info)
        self.sync_folder_info_column_values(folder)
        self.folder_info_changed.emit(folder)

    def on_detailed_info_column_changed(self, item: QTreeWidgetItem, column: TreeColumn, value: str) -> None:
        if not bool(item.data(0, ITEM_INFO_EDITABLE_ROLE)):
            return
        if column.column_id not in {COLUMN_DATA, COLUMN_INFO_PAIR_VALUE}:
            return
        raw_path = item.data(0, ITEM_PATH_ROLE)
        old_key = normalise_info_key(item.data(0, ITEM_INFO_KEY_ROLE) or "")
        if not raw_path:
            return
        folder = Path(str(raw_path)).resolve()
        try:
            info = read_info_file(folder)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.reapply_item_values(item)
            self.metadata_write_failed.emit(folder, str(error))
            return

        if not old_key:
            self.create_info_pair_from_empty_row(item, column, value, folder, info)
            return

        if old_key not in info.editable:
            self.reapply_item_values(item)
            return

        if column.column_id == COLUMN_DATA:
            new_key = normalise_info_key(value)
            if (
                not new_key
                or (new_key != old_key and (new_key in info.editable or new_key in info.readonly))
            ):
                self.reapply_item_values(item)
                return
            value = info.editable.pop(old_key)
            info.editable[new_key] = value
        else:
            new_key = old_key
            info.editable[old_key] = value

        try:
            write_info_file(folder, info)
        except (OSError, ValueError) as error:
            self.reapply_item_values(item)
            self.metadata_write_failed.emit(folder, str(error))
            return

        self.update_detailed_info_item(item, new_key, info.editable[new_key], True)
        self.store_folder_info(folder, info)
        self.sync_folder_info_column_values(folder)
        self.folder_info_changed.emit(folder)

    def create_info_pair_from_empty_row(
        self,
        item: QTreeWidgetItem,
        column: TreeColumn,
        edited_value: str,
        folder: Path,
        info,
    ) -> None:  # type: ignore[no-untyped-def]
        values = item.data(0, ITEM_COLUMN_VALUES_ROLE)
        current_value = ""
        if isinstance(values, dict) and not bool(item.data(0, ITEM_INFO_EMPTY_ROLE)):
            current_value = values.get(COLUMN_INFO_PAIR_VALUE, "")
        if column.column_id == COLUMN_DATA:
            new_key = normalise_info_key(edited_value)
            if not new_key or new_key in info.editable or new_key in info.readonly:
                self.reapply_item_values(item)
                return
            value = str(current_value)
        else:
            new_key = next_default_info_key(info.editable, info.readonly)
            value = edited_value

        info.editable[new_key] = value
        try:
            write_info_file(folder, info)
        except (OSError, ValueError) as error:
            self.reapply_item_values(item)
            self.metadata_write_failed.emit(folder, str(error))
            return

        self.update_detailed_info_item(item, new_key, value, True, is_empty=False)
        self.ensure_empty_detailed_info_row(item.parent(), folder)
        self.store_folder_info(folder, info)
        self.sync_folder_info_column_values(folder)
        self.folder_info_changed.emit(folder)

    def sort_tree_children(self, parent: QTreeWidgetItem) -> None:
        """Place branches before leaves and sort each category alphabetically."""

        children = [parent.child(index) for index in range(parent.childCount())]
        for child in children:
            self.sort_tree_children(child)
        children.sort(key=tree_item_sort_key)
        if children:
            parent.takeChildren()
            parent.addChildren(children)

    def item_identity(self, item: QTreeWidgetItem) -> tuple[str, ...]:
        """Identify a row by type and logical source, independent of its display text."""
        item_type = str(item.data(0, ITEM_TYPE_ROLE) or "")
        if item_type in {ROW_TYPE_VIRTUAL, ROW_TYPE_GROUP, ROW_TYPE_MESSAGE, ROW_TYPE_ERROR}:
            return item_type, self.item_logical_value_without_progress(item, COLUMN_DATA)
        detail = ""
        if item_type == ROW_TYPE_SERIES:
            detail = str(item.data(0, ITEM_SERIES_ROLE) or "")
        elif item_type == ROW_TYPE_DETAILED_INFO:
            detail = (
                "(add metadata row)" if bool(item.data(0, ITEM_INFO_EMPTY_ROLE))
                else str(item.data(0, ITEM_INFO_KEY_ROLE) or "")
            )
        elif item_type == ROW_TYPE_VIDEO:
            detail = str(item.data(0, ITEM_VIDEO_PATH_ROLE) or "")
        elif item_type == ROW_TYPE_IMAGE:
            detail = str(item.data(0, ITEM_IMAGE_PATH_ROLE) or "")
        elif item_type == ROW_TYPE_MODEL:
            detail = str(item.data(0, ITEM_MODEL_PATH_ROLE) or "")
        elif item_type == ROW_TYPE_SOURCE:
            row = item.data(0, ITEM_SOURCE_ROLE)
            if isinstance(row, SourceRow):
                # An import keeps its row when done; an export row by its output.
                detail = (f"import-set|{row.plugin_id}|{row.set_key}" if row.set_key else
                          f"import|{row.source}" if row.kind == SOURCE_IMPORT else
                          f"export|{row.plugin_id}|{row.operation}|{row.output}")
        return item_type, str(item.data(0, ITEM_PATH_ROLE) or ""), detail

    def item_identity_paths(self) -> dict[tuple[tuple[str, ...], ...], QTreeWidgetItem]:
        """Map each row's identity path from the root to its item."""
        paths: dict[tuple[tuple[str, ...], ...], QTreeWidgetItem] = {}

        def visit(item: QTreeWidgetItem, parent_path: tuple[tuple[str, ...], ...]) -> None:
            path = (*parent_path, self.item_identity(item))
            paths.setdefault(path, item)
            for child_index in range(item.childCount()):
                visit(item.child(child_index), path)

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index), ())
        return paths

    def restore_expanded_item_paths(self, expanded: set[tuple[tuple[str, ...], ...]]) -> None:
        """Open the rows that were open; a folder opened again gets its contents first."""
        self._restoring_tree_expansion = True
        seen: set[tuple[tuple[str, ...], ...]] = set()

        def visit(item: QTreeWidgetItem, parent_path: tuple[tuple[str, ...], ...]) -> None:
            path = (*parent_path, self.item_identity(item))
            opened = path in expanded and path not in seen
            seen.add(path)
            if opened:
                self.ensure_folder_contents(item)
            item.setExpanded(opened)
            for child_index in range(item.childCount()):
                visit(item.child(child_index), path)

        try:
            for index in range(self.tree.topLevelItemCount()):
                visit(self.tree.topLevelItem(index), ())
        finally:
            self._restoring_tree_expansion = False

    def ensure_folder_contents(self, item: QTreeWidgetItem) -> bool:
        """Make a folder row's contents if they are not made yet; True when they were made now."""
        if not item.data(0, ITEM_CONTENT_PENDING_ROLE):
            return False
        previous = self._updating_info_items
        self._updating_info_items = True
        try:
            item.setData(0, ITEM_CONTENT_PENDING_ROLE, False)
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicatorWhenChildless)
        finally:
            self._updating_info_items = previous
        self._build_folder_contents(item, Path(str(item.data(0, ITEM_PATH_ROLE))))
        self.sort_tree_children(item)
        self.apply_diagnostic_row_styles()
        return True

    def ensure_all_folder_contents(self, *, expand: bool = False) -> None:
        """Make every folder's contents, as if each had been opened; ``expand`` also opens them."""
        previous = self._restoring_tree_expansion
        self._restoring_tree_expansion = True
        try:
            for item in list(self._folder_items.values()):
                self.ensure_folder_contents(item)
                if expand:
                    item.setExpanded(True)
        finally:
            self._restoring_tree_expansion = previous

    def _build_folder_contents(self, item: QTreeWidgetItem, folder: Path) -> None:
        """A folder's Info, Source data and Media rows and its series."""
        plan = self._folder_plans.get(folder)
        info = self._folder_info_from_snapshot(folder)
        nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}
        previous = self._populating_tree
        self._populating_tree = True
        try:
            self.add_folder_info_rows(item, folder, info)
            self.add_folder_source_rows(item, folder)
            self.add_folder_video_row(item, folder, info, nodes)
            self.add_folder_image_rows(item, folder, info, nodes)
            self.add_folder_model_rows(item, folder, info, nodes)
            for h5_path in (*plan.linked_audio, *plan.h5_files) if plan is not None else ():
                self.add_h5_series(item, h5_path, nodes, str(folder))
        finally:
            self._populating_tree = previous

    def capture_tree_state(self) -> dict[str, object]:
        """Save expansion, selection, current row and folder progress for a rebuild."""
        paths = self.item_identity_paths()
        path_by_item = {id(item): path for path, item in paths.items()}
        current_item = self.tree.currentItem()
        selection_model = self.tree.selectionModel()
        selected = []
        if selection_model is not None:
            for index in selection_model.selectedIndexes():
                item = self.tree.itemFromIndex(index)
                if item is not None and id(item) in path_by_item:
                    selected.append((path_by_item[id(item)], index.column()))
        progress = {}
        for item in paths.values():
            total = item.data(0, ITEM_PROGRESS_TOTAL_ROLE)
            if item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_FOLDER and total:
                progress[str(item.data(0, ITEM_PATH_ROLE))] = (
                    int(item.data(0, ITEM_PROGRESS_COMPLETED_ROLE) or 0),
                    int(total),
                    str(item.data(0, ITEM_PROGRESS_TEXT_ROLE) or ""),
                    str(item.data(0, ITEM_PROGRESS_ETA_ROLE) or ""),
                )
        return {
            "expanded": {path for path, item in paths.items() if item.isExpanded()},
            "current": path_by_item.get(id(current_item)) if current_item is not None else None,
            "current_column": self.tree.currentColumn(),
            "selected": selected,
            "logical_column": self._selected_logical_column_id,
            "progress": progress,
        }

    def restore_tree_state(self, state: dict[str, object]) -> None:
        self.restore_expanded_item_paths(state["expanded"])  # type: ignore[arg-type]
        paths = self.item_identity_paths()
        progress = state["progress"]
        assert isinstance(progress, dict)
        for item in paths.values():
            if item.data(0, ITEM_TYPE_ROLE) != ROW_TYPE_FOLDER:
                continue
            saved = progress.get(str(item.data(0, ITEM_PATH_ROLE)))
            if saved is not None:
                self.set_item_progress(item, *saved)
        current_item = paths.get(state["current"]) if state["current"] is not None else None  # type: ignore[arg-type]
        if current_item is None:
            return
        selection_model = self.tree.selectionModel()
        self._restoring_tree_expansion = True
        try:
            self.tree.setCurrentItem(
                current_item,
                max(0, int(state["current_column"])),  # type: ignore[arg-type]
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
            for path, column in state["selected"]:  # type: ignore[union-attr]
                item = paths.get(path)
                if item is not None and selection_model is not None:
                    selection_model.select(
                        self.tree.indexFromItem(item, column),
                        QItemSelectionModel.SelectionFlag.Select,
                    )
        finally:
            self._restoring_tree_expansion = False
        self._selected_logical_column_id = str(state["logical_column"] or "")
        self.update_row_highlights()
        self.logical_header.update()

    def populate_folder_tree(
        self,
        data_folder: Path,
        folders: list[Path],
        root_files: list[Path],
        info_index: FolderInfoIndex,
        log_lengths_by_folder: dict[Path, str],
    ) -> None:
        """Folder rows (their contents are made when opened) and the H5 files outside folders."""
        folder_nodes: dict[Path, QTreeWidgetItem] = {}
        for folder in folders:
            parent = folder_nodes.get(folder.parent, self.tree.invisibleRootItem())
            item = self.make_pending_folder_item(folder, folder.name, info_index, log_lengths_by_folder)
            parent.addChild(item)
            folder_nodes[folder] = item
        self._folder_items = folder_nodes
        series_group_nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}
        for h5_path in root_files:
            parent = folder_nodes.get(h5_path.parent, self.tree.invisibleRootItem())
            self.add_h5_series(parent, h5_path, series_group_nodes, str(h5_path.parent))

    def populate_grouped_tree(
        self,
        data_folder: Path,
        folders: list[Path],
        root_files: list[Path],
        mode: str,
        info_index: FolderInfoIndex,
        log_lengths_by_folder: dict[Path, str],
    ) -> None:
        group_nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}
        folder_nodes: dict[Path, QTreeWidgetItem] = {}
        for folder in folders:
            parent = self.add_group_nodes(group_nodes, self.group_path(folder, data_folder, mode, info_index))
            relative_folder = folder.relative_to(data_folder).as_posix()
            item = self.make_pending_folder_item(folder, relative_folder, info_index, log_lengths_by_folder)
            parent.addChild(item)
            folder_nodes[folder] = item
        self._folder_items = folder_nodes
        series_group_nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}
        for h5_path in root_files:
            parent = self.add_group_nodes(group_nodes, self.group_path(h5_path.parent, data_folder, mode, info_index))
            self.add_h5_series(parent, h5_path, series_group_nodes, str(h5_path.parent))

    def make_pending_folder_item(
        self, folder: Path, display_name: str, info_index: FolderInfoIndex, log_lengths_by_folder: dict[Path, str],
    ) -> QTreeWidgetItem:
        """A folder row with an open/close triangle; its contents are made when it is opened."""
        item = self.make_folder_item(folder, display_name, info_index, log_lengths_by_folder)
        item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_FOLDER)
        item.setData(0, ITEM_PATH_ROLE, str(folder))
        item.setData(0, ITEM_CONTENT_PENDING_ROLE, True)
        item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        self.reapply_item_values(item)
        return item

    def add_group_nodes(
        self,
        nodes: dict[tuple[str, ...], QTreeWidgetItem],
        group_path: tuple[str, ...],
    ) -> QTreeWidgetItem:
        parent = self.tree.invisibleRootItem()
        path_key: list[str] = []
        for group_name in group_path:
            path_key.append(group_name)
            key = tuple(path_key)
            if key not in nodes:
                item = self.make_item({COLUMN_DATA: group_name})
                item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_VIRTUAL)
                self.reapply_item_values(item)
                parent.addChild(item)
                nodes[key] = item
            parent = nodes[key]
        return parent

    def group_path(
        self,
        source_path: Path,
        data_folder: Path,
        mode: str,
        info_index: FolderInfoIndex,
    ) -> tuple[str, ...]:
        # Scanned folders and H5 files are known without asking the disk.
        snapshot = self._display_snapshot()
        if snapshot is not None and source_path in snapshot.folder_infos:
            source_folder = source_path
        elif snapshot is not None and source_path in snapshot.h5_data:
            source_folder = source_path.parent
        else:
            source_folder = source_path if source_path.is_dir() else source_path.parent
        if mode == GROUPING_NONE:
            relative_parent = source_folder.relative_to(data_folder)
            return relative_parent.parts if relative_parent.parts else ("(root)",)

        if mode in self.info_group_keys:
            return folder_info_group_path(source_folder, mode, info_index.values_by_folder)

        parsed = find_folder_date(source_folder, data_folder)
        if not parsed.is_known:
            return ("Unknown",)

        timestamp = parsed.timestamp
        assert timestamp is not None
        if mode == "Year/Month/Day":
            return (f"{timestamp.year:04d}", month_name[timestamp.month], f"{timestamp.day:02d}")
        if mode == "Year/Month":
            return (f"{timestamp.year:04d}", month_name[timestamp.month])
        if mode == "Year":
            return (f"{timestamp.year:04d}",)
        if mode == "Today/Earlier":
            return ("Today" if timestamp.date() == date.today() else "Earlier",)
        return ("Unknown",)

    def add_folder_info_rows(
        self, folder_item: QTreeWidgetItem, folder: Path, info: FolderInfoData | None
    ) -> None:
        info_item = self.make_info_item(folder)
        folder_item.addChild(info_item)
        if info is not None:
            for key, value, editable in folder_info_pairs(info.editable, info.readonly):
                info_item.addChild(self.make_detailed_info_item(folder, key, value, editable))
        info_item.addChild(self.make_detailed_info_item(folder, "", "", True, is_empty=True))

    def add_folder_source_rows(self, folder_item: QTreeWidgetItem, folder: Path) -> None:
        """The folder's Source data group: what was imported or exported, and what can be."""
        rows = self.source_rows(folder)
        if not rows:
            return
        group = self.make_item({COLUMN_DATA: SOURCE_GROUP_NAME})
        group.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_SOURCE_GROUP)
        group.setData(0, ITEM_PATH_ROLE, str(resolved_path(folder)))
        self.reapply_item_values(group)
        folder_item.addChild(group)
        for row in rows:
            group.addChild(self.make_source_item(folder, row))

    def make_source_item(self, folder: Path, row: SourceRow) -> QTreeWidgetItem:
        item = self.make_item({
            COLUMN_DATA: f"{row.source} ({len(row.inputs)} files)" if row.set_key else row.source,
            COLUMN_SOURCE_OUTPUT: source_output_text(row),
            COLUMN_SOURCE_PLUGIN: row.plugin,
        })
        item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_SOURCE)
        item.setData(0, ITEM_PATH_ROLE, str(resolved_path(folder)))
        item.setData(0, ITEM_SOURCE_ROLE, row)
        if row.set_key:
            for name in (*row.inputs, *row.dependencies):
                child = replace(row, source=name, member=name, set_key="", inputs=(), output="")
                item.addChild(self.make_source_item(folder, child))
        if row.inputs:
            item.setToolTip(0, ", ".join(row.inputs))
        if row.problem:
            item.setToolTip(0, row.problem)
        self.reapply_item_values(item)
        return item

    def media_group_item(
        self, folder_item: QTreeWidgetItem, folder: Path, nodes: dict[tuple[str, ...], QTreeWidgetItem] | None
    ) -> QTreeWidgetItem:
        """The folder's Media series group, shared by video, image and audio rows."""
        if nodes is None:
            nodes = {}
        return self.add_series_group_nodes(folder_item, nodes, str(folder), MEDIA_GROUP)

    def add_folder_video_row(
        self,
        folder_item: QTreeWidgetItem,
        folder: Path,
        info: FolderInfoData | None,
        nodes: dict[tuple[str, ...], QTreeWidgetItem] | None = None,
    ) -> None:
        """Add one plot-able row for each attached video, in the folder's Media group."""
        if info is None or not info.videos:
            return
        folder_item = self.media_group_item(folder_item, folder, nodes)
        links = self._folder_links(folder, info)
        for video, video_path in zip(info.videos, links.videos if links is not None else ()):
            raw_offset = video.get("offset_seconds", "0")
            try:
                offset = float(raw_offset or 0.0)
            except (TypeError, ValueError):
                offset = 0.0
                self.navigator_warning.emit(
                    f"Invalid video offset for {video_path.name}: {raw_offset!r}; using 0 seconds."
                )
            item = self.make_item({COLUMN_DATA: video_path.name})
            item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_VIDEO)
            item.setData(0, ITEM_PATH_ROLE, str(resolved_path(folder)))
            item.setData(0, ITEM_VIDEO_PATH_ROLE, str(video_path))
            item.setData(0, ITEM_VIDEO_OFFSET_ROLE, offset)
            item.setToolTip(0, str(video_path))
            self.reapply_item_values(item)
            folder_item.addChild(item)

    def add_folder_image_rows(
        self,
        folder_item: QTreeWidgetItem,
        folder: Path,
        info: FolderInfoData | None,
        nodes: dict[tuple[str, ...], QTreeWidgetItem] | None = None,
    ) -> None:
        if info is None or not info.images:
            return
        folder_item = self.media_group_item(folder_item, folder, nodes)
        links = self._folder_links(folder, info)
        for image_path in links.images if links is not None else ():
            item = self.make_item({COLUMN_DATA: image_path.name})
            item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_IMAGE)
            item.setData(0, ITEM_PATH_ROLE, str(resolved_path(folder)))
            item.setData(0, ITEM_IMAGE_PATH_ROLE, str(image_path))
            item.setToolTip(0, str(image_path))
            self.reapply_item_values(item)
            folder_item.addChild(item)

    def add_folder_model_rows(
        self,
        folder_item: QTreeWidgetItem,
        folder: Path,
        info: FolderInfoData | None,
        nodes: dict[tuple[str, ...], QTreeWidgetItem] | None = None,
    ) -> None:
        """A Model attachment row per linked 3D model folder, in the Media group."""
        if info is None or not info.models:
            return
        folder_item = self.media_group_item(folder_item, folder, nodes)
        links = self._folder_links(folder, info)
        for model_path in links.models if links is not None else ():
            item = self.make_item({COLUMN_DATA: model_path.name})
            item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_MODEL)
            item.setData(0, ITEM_PATH_ROLE, str(resolved_path(folder)))
            item.setData(0, ITEM_MODEL_PATH_ROLE, str(model_path))
            item.setToolTip(0, f"3D model: {model_path}")
            self.reapply_item_values(item)
            folder_item.addChild(item)

    def make_info_item(self, folder: Path) -> QTreeWidgetItem:
        item = self.make_item({
            COLUMN_DATA: "Info",
        })
        item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_INFO)
        item.setData(0, ITEM_PATH_ROLE, str(resolved_path(folder)))
        self.reapply_item_values(item)
        return item

    def make_detailed_info_item(
        self,
        folder: Path,
        key: str,
        value: str,
        editable: bool,
        *,
        is_empty: bool = False,
    ) -> QTreeWidgetItem:
        item = self.make_item({
            COLUMN_DATA: "[ADD NEW KEY]" if is_empty else key,
            COLUMN_INFO_PAIR_VALUE: "[ADD NEW VALUE]" if is_empty else value,
        })
        item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_DETAILED_INFO)
        item.setData(0, ITEM_PATH_ROLE, str(resolved_path(folder)))
        item.setData(0, ITEM_INFO_KEY_ROLE, key)
        item.setData(0, ITEM_INFO_EDITABLE_ROLE, editable)
        item.setData(0, ITEM_INFO_EMPTY_ROLE, is_empty)
        if editable:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.reapply_item_values(item)
        return item

    def add_h5_series(
        self,
        parent: QTreeWidgetItem,
        h5_path: Path,
        group_nodes: dict[tuple[str, ...], QTreeWidgetItem],
        parent_key: str,
    ) -> None:
        h5_data = self._h5_data_from_snapshot(h5_path)
        series, error = h5_data.series, h5_data.error
        if error:
            error_item = self.make_item({COLUMN_DATA: f"Unreadable: {h5_path.name}"})
            error_item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_ERROR)
            self.reapply_item_values(error_item)
            parent.addChild(error_item)
            return

        for item in series:
            if self.navigator_control_settings.hide_irrelevant_series and not item.visible:
                continue
            series_parent = self.add_series_group_nodes(
                parent,
                group_nodes,
                parent_key,
                item.navigator_group,
            )

            series_item = self.make_series_item(item)
            series_item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_SERIES)
            series_item.setData(0, ITEM_PATH_ROLE, str(h5_path))
            series_item.setData(0, ITEM_SERIES_ROLE, item.name)
            self.reapply_item_values(series_item)
            series_parent.addChild(series_item)

    def add_series_group_nodes(
        self,
        parent: QTreeWidgetItem,
        nodes: dict[tuple[str, ...], QTreeWidgetItem],
        parent_key: str,
        group_path: str,
    ) -> QTreeWidgetItem:
        current_parent = parent
        key_parts = [parent_key]
        for group_name in (part.strip() for part in group_path.split("/") if part.strip()):
            key_parts.append(group_name)
            key = tuple(key_parts)
            if key not in nodes:
                group_item = self.make_item({COLUMN_DATA: group_name})
                group_item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_GROUP)
                self.reapply_item_values(group_item)
                current_parent.addChild(group_item)
                nodes[key] = group_item
            current_parent = nodes[key]
        return current_parent

    def add_message_item(self, text: str) -> None:
        item = self.make_item({COLUMN_DATA: text})
        item.setData(0, ITEM_TYPE_ROLE, ROW_TYPE_MESSAGE)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.reapply_item_values(item)
        self.tree.addTopLevelItem(item)

    def make_folder_item(
        self,
        folder: Path,
        display_name: str,
        info_index: FolderInfoIndex,
        log_lengths_by_folder: dict[Path, str],
    ) -> QTreeWidgetItem:
        item = self.make_item(self.folder_row_values(folder, display_name, info_index, log_lengths_by_folder))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        return item

    def folder_row_values(
        self, folder: Path, display_name: str, info_index: FolderInfoIndex, log_lengths_by_folder: dict[Path, str],
    ) -> dict[str, str]:
        """A folder row's logical cells: its name, log length and metadata columns."""
        folder = resolved_path(folder)
        values = {
            COLUMN_DATA: display_name,
            COLUMN_LOG_LENGTH: log_lengths_by_folder.get(folder, ""),
        }
        for key, value in info_index.values_by_folder.get(folder, {}).items():
            values[info_column_id(key)] = value
        return values

    def make_series_item(self, series: H5Series) -> QTreeWidgetItem:
        values = {COLUMN_DATA: series.navigator_name}
        values.update({
            series_column_id(metadata.column_id): metadata.value
            for metadata in series.metadata
        })
        return self.make_item(values)

    def make_item(self, values: dict[str, str]) -> QTreeWidgetItem:
        item = QTreeWidgetItem([""] * PHYSICAL_COLUMN_COUNT)
        item.setData(0, ITEM_COLUMN_VALUES_ROLE, dict(values))
        self.apply_item_values(item)
        return item

    def tree_items(self):
        stack = [self.tree.topLevelItem(index) for index in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            yield item
            stack.extend(item.child(index) for index in range(item.childCount()))

    def apply_item_values(self, item: QTreeWidgetItem) -> None:
        values = item.data(0, ITEM_COLUMN_VALUES_ROLE)
        if not isinstance(values, dict):
            return
        item.setText(PHYSICAL_DATA_COLUMN, self.item_logical_value(item, COLUMN_DATA))
        # The delegate paints the row-specific logical cells in this physical
        # column. Leaving the model text empty prevents Qt drawing a duplicate
        # tab-separated fallback underneath that custom rendering.
        item.setText(PHYSICAL_VALUE_COLUMN, "")

    def reapply_item_values(self, item: QTreeWidgetItem) -> None:
        previous = self._updating_info_items
        self._updating_info_items = True
        try:
            self.apply_item_values(item)
        finally:
            self._updating_info_items = previous

    def set_item_values(self, item: QTreeWidgetItem, values: dict[str, str]) -> None:
        """Store logical values without re-entering the edit handlers."""
        previous = self._updating_info_items
        self._updating_info_items = True
        try:
            item.setData(0, ITEM_COLUMN_VALUES_ROLE, values)
            self.apply_item_values(item)
        finally:
            self._updating_info_items = previous

    def update_detailed_info_item(
        self,
        item: QTreeWidgetItem,
        key: str,
        value: str,
        editable: bool,
        *,
        is_empty: bool = False,
    ) -> None:
        values = item.data(0, ITEM_COLUMN_VALUES_ROLE)
        if not isinstance(values, dict):
            values = {}
        values.update({
            COLUMN_DATA: key,
            COLUMN_INFO_PAIR_VALUE: value,
        })
        # Every setData emits itemChanged; keep the whole update inside the
        # guard so a key edit is not handled a second time.
        previous = self._updating_info_items
        self._updating_info_items = True
        try:
            item.setData(0, ITEM_INFO_KEY_ROLE, key)
            item.setData(0, ITEM_INFO_EDITABLE_ROLE, editable)
            item.setData(0, ITEM_INFO_EMPTY_ROLE, is_empty)
            self.set_item_values(item, values)
        finally:
            self._updating_info_items = previous

    def ensure_empty_detailed_info_row(self, info_item: QTreeWidgetItem | None, folder: Path) -> None:
        if info_item is None:
            return
        for index in range(info_item.childCount()):
            child = info_item.child(index)
            if (
                child.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_DETAILED_INFO
                and bool(child.data(0, ITEM_INFO_EMPTY_ROLE))
            ):
                return
        info_item.addChild(self.make_detailed_info_item(folder, "", "", True, is_empty=True))
        self.sort_tree_children(info_item)

    def store_folder_info(self, folder: Path, info: FolderInfoData) -> None:
        """Record metadata just written for one folder and update its index entry."""
        folder = folder.resolve()
        signature = _file_signature(info_file_path(folder))
        snapshot = self._source_snapshot
        if snapshot is not None and folder in snapshot.info_signatures:
            snapshot.folder_infos[folder] = info
            snapshot.info_signatures[folder] = signature
        if self._snapshot_job is not None:
            self._info_edits_since_job_start[folder] = (info, signature)
        self.reload_info_index(folder, info)

    def reload_info_index(self, folder: Path | None = None, info: FolderInfoData | None = None) -> None:
        """Refresh the metadata index from memory; only the given folder changes."""
        if folder is not None:
            self.current_info_index = update_folder_info_index(
                self.current_info_index, folder, info, self.default_info_metadata_keys
            )
        else:
            data_folder = self.data_folder()
            snapshot = self._snapshot_for_root(data_folder) if data_folder is not None else None
            if snapshot is None:
                return
            self.current_info_index = add_default_info_keys(
                folder_info_index_from_data(snapshot.folder_infos), self.default_info_metadata_keys
            )
        self.update_group_by_options(self.current_info_index.keys)
        self.update_filter_options(self.current_info_index)

    def sync_folder_info_column_values(self, folder: Path) -> None:
        folder = folder.resolve()
        for item in self.tree_items():
            if item.data(0, ITEM_TYPE_ROLE) != ROW_TYPE_FOLDER:
                continue
            if Path(str(item.data(0, ITEM_PATH_ROLE))).resolve() != folder:
                continue
            values = item.data(0, ITEM_COLUMN_VALUES_ROLE)
            if isinstance(values, dict):
                for column_id in list(values):
                    if column_id.startswith(CUSTOM_COLUMN_PREFIX):
                        del values[column_id]
                for key, value in self.current_info_index.values_by_folder.get(folder, {}).items():
                    values[info_column_id(key)] = value
                self.set_item_values(item, values)
            if self.folder_sort_direction:
                # Let the delegate finish committing before moving its row.
                QTimer.singleShot(0, self, self.apply_folder_sort)
            return

    def update_info_subtree_values(self, folder: Path, info: FolderInfoData | None = None) -> None:
        folder = folder.resolve()
        if info is None:
            try:
                info = read_info_file(folder)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                self.metadata_write_failed.emit(folder, str(error))
                return
        details = {
            key: (value, editable)
            for key, value, editable in folder_info_pairs(info.editable, info.readonly)
        }
        for item in self.tree_items():
            if item.data(0, ITEM_TYPE_ROLE) != ROW_TYPE_INFO:
                continue
            if Path(str(item.data(0, ITEM_PATH_ROLE))).resolve() != folder:
                continue
            existing_keys = set()
            for child_index in range(item.childCount()):
                child = item.child(child_index)
                key = str(child.data(0, ITEM_INFO_KEY_ROLE) or "")
                existing_keys.add(key)
                detail = details.get(key)
                if detail is not None:
                    value, editable = detail
                    self.update_detailed_info_item(child, key, value, editable)
            missing_keys = details.keys() - existing_keys
            for key in missing_keys:
                value, editable = details[key]
                item.addChild(self.make_detailed_info_item(folder, key, value, editable))
            if missing_keys:
                self.sort_tree_children(item)

    def on_item_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        item_type = item.data(0, ITEM_TYPE_ROLE)
        if item_type in {ROW_TYPE_FOLDER, ROW_TYPE_SOURCE_GROUP}:
            # QTreeWidget expands or collapses it; imports and exports start from Source rows.
            return
        if item_type == ROW_TYPE_SOURCE:
            row = item.data(0, ITEM_SOURCE_ROLE)
            if isinstance(row, SourceRow) and not row.member:
                self.source_activated.emit(Path(str(item.data(0, ITEM_PATH_ROLE))), row)
            return
        if item_type == ROW_TYPE_GROUP:
            # QTreeWidget handles expansion; group activation never opens plots.
            return
        if item_type == ROW_TYPE_IMAGE:
            self.image_activated.emit(
                Path(str(item.data(0, ITEM_PATH_ROLE))),
                Path(str(item.data(0, ITEM_IMAGE_PATH_ROLE))),
            )
            return
        if item_type == ROW_TYPE_VIDEO:
            self.video_activated.emit(
                Path(str(item.data(0, ITEM_PATH_ROLE))),
                Path(str(item.data(0, ITEM_VIDEO_PATH_ROLE))),
                float(item.data(0, ITEM_VIDEO_OFFSET_ROLE) or 0.0),
            )
            return
        if item_type != ROW_TYPE_SERIES:
            return
        self.series_activated.emit(Path(item.data(0, ITEM_PATH_ROLE)), str(item.data(0, ITEM_SERIES_ROLE)))

    def on_context_menu_requested(self, position: QPoint) -> None:
        item = self.tree.itemAt(position)
        if item is None:
            return
        self.update_logical_cell_from_view_position(position)
        column = self.tree.columnAt(position.x())
        # Preserve the selection when opening a selected item's plot menu.
        if column >= 0 and item not in self.tree.selectedItems():
            self.tree.setCurrentItem(item, column)
        item_type = item.data(0, ITEM_TYPE_ROLE)
        if item_type == ROW_TYPE_MODEL:
            self.model_context_menu_requested.emit(
                Path(str(item.data(0, ITEM_PATH_ROLE))),
                Path(str(item.data(0, ITEM_MODEL_PATH_ROLE))),
                position,
            )
            return
        if item_type == ROW_TYPE_IMAGE:
            self.image_context_menu_requested.emit(
                Path(str(item.data(0, ITEM_PATH_ROLE))),
                Path(str(item.data(0, ITEM_IMAGE_PATH_ROLE))),
                position,
            )
            return
        if item_type == ROW_TYPE_VIDEO:
            self.video_context_menu_requested.emit(
                Path(str(item.data(0, ITEM_PATH_ROLE))),
                Path(str(item.data(0, ITEM_VIDEO_PATH_ROLE))),
                position,
            )
            return
        if item_type == ROW_TYPE_DETAILED_INFO:
            self.show_detailed_info_context_menu(item, position)
            return
        if item_type == ROW_TYPE_SOURCE:
            row = item.data(0, ITEM_SOURCE_ROLE)
            if isinstance(row, SourceRow):
                self.source_context_menu_requested.emit(Path(str(item.data(0, ITEM_PATH_ROLE))), row, position)
            return
        if item_type == ROW_TYPE_SERIES:
            self.series_context_menu_requested.emit(
                Path(str(item.data(0, ITEM_PATH_ROLE))),
                str(item.data(0, ITEM_SERIES_ROLE)),
                position,
            )
            return
        if item_type == ROW_TYPE_GROUP:
            entries = []
            pending = [item]
            while pending:
                child = pending.pop()
                if child.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_SERIES:
                    entries.append((Path(str(child.data(0, ITEM_PATH_ROLE))),
                                    str(child.data(0, ITEM_SERIES_ROLE))))
                pending.extend(child.child(index) for index in reversed(range(child.childCount())))
            folder = self.media_group_folder(item)
            if folder is not None:
                self.media_group_context_menu_requested.emit(folder, entries, position)
            elif entries:
                self.series_group_context_menu_requested.emit(entries, position)
            return
        if item_type != ROW_TYPE_FOLDER:
            return
        items = self.tree.selectedItems() if item in self.tree.selectedItems() else [item]
        # The Folder menu's actions are for one folder; several selected folders have none.
        if len(self.folders_for_items(items)) <= 1:
            self.folder_context_menu_requested.emit(Path(str(item.data(0, ITEM_PATH_ROLE))), position)

    @staticmethod
    def media_group_folder(item: QTreeWidgetItem) -> Path | None:
        """The folder of a folder's Media group row; None for any other row."""
        parent = item.parent()
        if (item.data(0, ITEM_TYPE_ROLE) != ROW_TYPE_GROUP or item.text(0) != MEDIA_GROUP or parent is None
                or parent.data(0, ITEM_TYPE_ROLE) != ROW_TYPE_FOLDER):
            return None
        return Path(str(parent.data(0, ITEM_PATH_ROLE)))

    @staticmethod
    def folders_for_items(items: list[QTreeWidgetItem]) -> list[Path]:
        """The selected folders, and the visible folders under selected virtual groups, in tree order."""
        folders: list[Path] = []
        for item in items:
            pending = [item]
            while pending:
                current = pending.pop()
                item_type = current.data(0, ITEM_TYPE_ROLE)
                if item_type == ROW_TYPE_FOLDER:
                    folder = Path(str(current.data(0, ITEM_PATH_ROLE)))
                    if folder not in folders:
                        folders.append(folder)
                elif item_type == ROW_TYPE_VIRTUAL:
                    pending.extend(child for child in (current.child(index) for index in
                                                       reversed(range(current.childCount())))
                                   if not child.isHidden())
        return folders

    def selected_series_entries(self) -> list[tuple[Path, str]]:
        entries: list[tuple[Path, str]] = []
        for selected in self.tree.selectedItems():
            if selected.data(0, ITEM_TYPE_ROLE) != ROW_TYPE_SERIES:
                continue
            entries.append((Path(str(selected.data(0, ITEM_PATH_ROLE))), str(selected.data(0, ITEM_SERIES_ROLE))))
        return entries

    def selected_plot_sources(self):
        return self.plot_sources_for_items(self.tree.selectedItems())

    def plot_sources_for_items(self, items):
        sources = []
        pending = list(reversed(items))
        while pending:
            item = pending.pop()
            kind = item.data(0, ITEM_TYPE_ROLE)
            path = Path(str(item.data(0, ITEM_PATH_ROLE)))
            if kind == ROW_TYPE_GROUP:
                pending.extend(item.child(index) for index in reversed(range(item.childCount())))
            elif kind == ROW_TYPE_SERIES:
                sources.append(PlotSource("series", path, str(item.data(0, ITEM_SERIES_ROLE))))
            elif kind == ROW_TYPE_IMAGE:
                sources.append(PlotSource("image", Path(str(item.data(0, ITEM_IMAGE_PATH_ROLE))), folder=path))
            elif kind == ROW_TYPE_VIDEO:
                sources.append(PlotSource("video", Path(str(item.data(0, ITEM_VIDEO_PATH_ROLE))), folder=path,
                                          offset=float(item.data(0, ITEM_VIDEO_OFFSET_ROLE) or 0)))
        return list(dict.fromkeys(sources))

    def gnss_group_series(self, item: QTreeWidgetItem) -> tuple[Path, str] | None:
        """Return a GNSS child series when this is the group-level GNSS row."""
        if item.data(0, ITEM_TYPE_ROLE) != ROW_TYPE_GROUP or self.item_logical_value(item, COLUMN_DATA).casefold() != "gnss":
            return None
        stack = [item.child(index) for index in range(item.childCount())]
        while stack:
            child = stack.pop(0)
            if child.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_SERIES:
                return Path(str(child.data(0, ITEM_PATH_ROLE))), str(child.data(0, ITEM_SERIES_ROLE))
            stack.extend(child.child(index) for index in range(child.childCount()))
        return None

    def show_detailed_info_context_menu(self, item: QTreeWidgetItem, position: QPoint) -> None:
        raw_path = item.data(0, ITEM_PATH_ROLE)
        key = str(item.data(0, ITEM_INFO_KEY_ROLE) or "")
        if not raw_path or not key:
            return
        menu = QMenu(self)
        delete_action = menu.addAction("Delete key/value pair")
        delete_action.setEnabled(bool(item.data(0, ITEM_INFO_EDITABLE_ROLE)))
        delete_action.triggered.connect(lambda: self.delete_editable_info_pair(Path(str(raw_path)).resolve(), key))
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def delete_editable_info_pair(self, folder: Path, key: str) -> None:
        try:
            info = read_info_file(folder)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.metadata_write_failed.emit(folder, str(error))
            return
        if key not in info.editable:
            return
        del info.editable[key]
        try:
            write_info_file(folder, info)
        except (OSError, ValueError) as error:
            self.metadata_write_failed.emit(folder, str(error))
            return
        self.store_folder_info(folder, info)
        self.folder_info_changed.emit(folder)
        self.refresh_tree()


def loading_phase_text(phase: str, total: int) -> str:
    """One Internal log line as a phase of a data folder read starts."""
    if phase == PHASE_FINDING:
        return "Finding folders…"
    folders = f"{total} folder" + ("" if total == 1 else "s")
    if phase == PHASE_SOURCES:
        return f"Finding Source data in {folders}…"
    return f"Reading data of {folders}…"


def source_output_text(row: SourceRow) -> str:
    """The Output column of a Source data row."""
    if row.member:
        return "Reference" if row.member in row.dependencies else "Input"
    if row.state == STATE_INCOMPLETE:
        return "Incomplete - " + row.problem
    if row.state == STATE_NEW and row.set_key:
        return f"{row.output} (not imported)"
    if row.state in {STATE_NEW, STATE_UNSUPPORTED}:
        return "Not imported" if row.kind == SOURCE_IMPORT else "Not exported"
    if row.state == STATE_OUTDATED:
        return f"{row.output} (outdated)"
    if row.state == STATE_MISSING:
        return f"{row.output} (missing)"
    return row.output


def info_column_id(key: str) -> str:
    return f"{CUSTOM_COLUMN_PREFIX}{normalise_info_key(key)}"


def series_column_id(key: str) -> str:
    return key


@dataclass(frozen=True)
class FolderPlan:
    """What a folder row holds, worked out from the snapshot without making its rows."""

    h5_files: tuple[Path, ...]
    # Audio H5 files stored elsewhere and linked to this folder.
    linked_audio: tuple[Path, ...]
    # Its series' Sample loss and Clipping flags (DIAGNOSTIC_*).
    flags: int
    # The snapshot objects its rows are made from: its info, Source data rows and H5 data.
    parts: tuple

    def same_as(self, other: "FolderPlan | None") -> bool:
        """Whether a folder's rows would be the same: the same files and the same objects."""
        return (other is not None and self.h5_files == other.h5_files and self.linked_audio == other.linked_audio
                and self.flags == other.flags and len(self.parts) == len(other.parts)
                and all(mine is theirs for mine, theirs in zip(self.parts, other.parts)))


@dataclass(frozen=True)
class _TreeView:
    """The folders the tree shows and what decides its layout (``key``)."""

    mode: str
    info_index: FolderInfoIndex
    folders: list[Path]
    root_files: list[Path]
    plans: dict[Path, FolderPlan]
    key: tuple


def diagnostic_flags_from_item(item: QTreeWidgetItem) -> int:
    values = item.data(0, ITEM_COLUMN_VALUES_ROLE)
    if not isinstance(values, dict):
        return 0
    return diagnostic_flags_from_warning(str(values.get(series_column_id("warning"), "")))


def diagnostic_flags_from_warning(warning: str) -> int:
    """DIAGNOSTIC_* flags named by a series' warning text."""
    warning = warning.casefold()
    flags = 0
    if "sampleloss" in warning or "sample loss" in warning:
        flags |= DIAGNOSTIC_SAMPLE_LOSS
    if "clipping" in warning:
        flags |= DIAGNOSTIC_CLIPPING
    return flags


def folder_info_pairs(
    editable: dict[str, str],
    readonly: dict[str, str],
) -> list[tuple[str, str, bool]]:
    pairs = [(key, value, True) for key, value in editable.items()]
    pairs.extend((key, value, False) for key, value in readonly.items())
    return pairs


def next_default_info_key(editable: dict[str, str], readonly: dict[str, str]) -> str:
    existing = set(editable) | set(readonly)
    if "key" not in existing:
        return "key"
    index = 2
    while f"key_{index}" in existing:
        index += 1
    return f"key_{index}"


def default_logical_column_width(column: TreeColumn) -> int:
    if column.column_id == COLUMN_LOG_LENGTH:
        return 78
    if column.column_id == COLUMN_INFO_PAIR_VALUE:
        return 160
    if column.info_key:
        return 120
    return 100


def tree_item_sort_key(item: QTreeWidgetItem) -> tuple[int, int, str]:
    item_type = str(item.data(0, ITEM_TYPE_ROLE) or "")
    if item_type == ROW_TYPE_INFO:
        return (0, 0, item.text(0).casefold())
    if item_type == ROW_TYPE_SOURCE_GROUP:
        return (0, 1, "")
    if item_type == ROW_TYPE_SOURCE:
        # Imports, then exports, each in file-name order.
        row = item.data(0, ITEM_SOURCE_ROLE)
        if isinstance(row, SourceRow):
            return (1, 0 if row.kind == SOURCE_IMPORT else 1, f"{row.source.casefold()}|{row.output.casefold()}")
    if item_type == ROW_TYPE_DETAILED_INFO and bool(item.data(0, ITEM_INFO_EMPTY_ROLE)):
        return (2, 1, "")
    branch = item.childCount() > 0 or item_type == ROW_TYPE_FOLDER
    return (1, 0 if branch else 1, item.text(0).casefold())


def columns_for_category(
    category: str,
    info_index: FolderInfoIndex,
    series_columns=(),
) -> list[TreeColumn]:
    columns = [TreeColumn(COLUMN_DATA, "Data", always_visible=True)]
    if category == COLUMN_CATEGORY_FOLDER:
        columns.append(TreeColumn(COLUMN_LOG_LENGTH, "Log length"))
        columns.extend(
            TreeColumn(info_column_id(key), key, info_key=key)
            for key in sorted(info_index.keys, key=str.casefold)
        )
    elif category == COLUMN_CATEGORY_SERIES:
        columns.extend(
            TreeColumn(series_column_id(column.column_id), column.title)
            for column in series_columns
        )
    elif category == COLUMN_CATEGORY_INFO:
        columns.append(TreeColumn(COLUMN_INFO_PAIR_VALUE, "Value"))
    elif category == COLUMN_CATEGORY_SOURCE:
        columns[0] = TreeColumn(COLUMN_DATA, "Source", always_visible=True)
        columns.append(TreeColumn(COLUMN_SOURCE_OUTPUT, "Output"))
        columns.append(TreeColumn(COLUMN_SOURCE_PLUGIN, "Plugin"))
    return columns


def column_category_for_item(item: QTreeWidgetItem | None) -> str:
    if item is None:
        return COLUMN_CATEGORY_FOLDER
    item_type = str(item.data(0, ITEM_TYPE_ROLE) or "")
    if item_type in {ROW_TYPE_VIRTUAL, ROW_TYPE_FOLDER}:
        return COLUMN_CATEGORY_FOLDER
    if item_type in {ROW_TYPE_INFO, ROW_TYPE_DETAILED_INFO}:
        return COLUMN_CATEGORY_INFO
    if item_type in {ROW_TYPE_SOURCE_GROUP, ROW_TYPE_SOURCE}:
        return COLUMN_CATEGORY_SOURCE
    return COLUMN_CATEGORY_SERIES
