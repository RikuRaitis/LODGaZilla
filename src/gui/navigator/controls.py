"""Controls shown above the data navigator tree."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QObject,
    QSize,
    Signal,
    Qt,
)
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QStyle,
    QStyleOptionFrame,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.elide import PATH_ELLIPSIS, PATH_END_WIDTH_FRACTION, middle_elide_text  # noqa: F401


FILTER_KEY_PLACEHOLDER = "[KEY]"
# The data folder list's first item, which opens a folder dialog; its data marks it.
BROWSE_ITEM_TEXT = "[Browse…]"
BROWSE_ITEM = "__browse__"
FILTER_VALUE_PLACEHOLDER = "[VALUE]"


class ElidedPathLineEdit(QLineEdit):
    """Store a full path while showing a responsive middle-elided value."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self._syncing_display_text = False
        self.textEdited.connect(self._remember_edited_text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full_text = text
        self._sync_display_text()

    def text(self) -> str:
        return self._full_text

    def clear(self) -> None:
        self.setText("")

    def displayed_text(self) -> str:
        return super().text()

    def focusInEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._set_display_text(self._full_text)
        super().focusInEvent(event)
        self.setCursorPosition(len(self._full_text))

    def focusOutEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().focusOutEvent(event)
        self._sync_display_text()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._sync_display_text()

    def changeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().changeEvent(event)
        if event.type() in {QEvent.Type.FontChange, QEvent.Type.StyleChange}:
            self._sync_display_text()

    def _remember_edited_text(self, text: str) -> None:
        if not self._syncing_display_text:
            self._full_text = text
            self.setToolTip(text)

    def _sync_display_text(self) -> None:
        self.setToolTip(self._full_text)
        if self.hasFocus():
            display_text = self._full_text
        else:
            display_text = middle_elide_text(
                self._full_text,
                self.fontMetrics(),
                self._available_text_width(),
            )
        self._set_display_text(display_text)

    def _set_display_text(self, display_text: str) -> None:
        if super().text() == display_text:
            return
        self._syncing_display_text = True
        try:
            super().setText(display_text)
        finally:
            self._syncing_display_text = False

    def _available_text_width(self) -> int:
        option = QStyleOptionFrame()
        self.initStyleOption(option)
        content_rect = self.style().subElementRect(
            QStyle.SubElement.SE_LineEditContents,
            option,
            self,
        )
        return max(0, content_rect.width() - 2)


class RecentFolderComboBox(QComboBox):
    """Editable folder combo which keeps full paths out of its size hint.

    Its list holds a [Browse…] item that asks for a folder, then the recent folders.
    """

    browse_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setLineEdit(ElidedPathLineEdit())
        # The list is rebuilt from the recent folders; typed paths are not added to it.
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(20)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Connected first, so the field shows its path again before others see the selection.
        self.activated.connect(self._on_activated)
        self.set_recent_folders(())

    def set_recent_folders(self, folders: tuple[str, ...]) -> None:
        """Rebuild the list; the field keeps its folder."""
        edit = self.lineEdit()
        current = edit.text()
        self.clear()
        self.addItem(BROWSE_ITEM_TEXT, BROWSE_ITEM)
        for folder in folders:
            self.addItem(self.elided_folder_text(folder), folder)
        self.setCurrentIndex(-1)
        edit.setText(current)

    def is_browse_item(self, index: int) -> bool:
        return self.itemData(index, Qt.ItemDataRole.UserRole) == BROWSE_ITEM

    def _on_activated(self, index: int) -> None:
        if self.is_browse_item(index):
            # Selecting an item shows its text; keep showing the current folder instead.
            edit = self.lineEdit()
            edit.setText(edit.text())
            self.browse_requested.emit()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self.update_folder_item_texts()

    def showPopup(self) -> None:  # noqa: N802
        self.update_folder_item_texts()
        super().showPopup()

    def full_folder_path(self, index: int) -> str:
        value = self.itemData(index, Qt.ItemDataRole.UserRole)
        return str(value) if value is not None else self.itemText(index)

    def update_folder_item_texts(self) -> None:
        for index in range(self.count()):
            if self.is_browse_item(index):
                continue
            full_path = self.full_folder_path(index)
            display_text = self.elided_folder_text(full_path)
            if self.itemText(index) != display_text:
                self.setItemText(index, display_text)

    def elided_folder_text(self, full_path: str) -> str:
        # The popup uses the same useful width as the editable control.
        available_width = max(40, self.width() - 40)
        return middle_elide_text(full_path, self.fontMetrics(), available_width)


class NavigatorControlsWidget(QWidget):
    """The File bar, then grid-aligned Navigator controls and the centered tree icon row below them."""

    hide_irrelevant_changed = Signal(bool)
    # A Navigator diagnostic ("sample_loss" or "clipping") was switched on or off.
    diagnostic_toggled = Signal(str, bool)
    refresh_requested = Signal()
    # File bar buttons; the main window opens and saves workspaces.
    workspace_open_requested = Signal()
    workspace_save_requested = Signal()
    workspace_save_as_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        hide_irrelevant_series: bool = True,
        diagnostics_enabled: dict[str, bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self.folder_combo = RecentFolderComboBox()
        self.folder_edit = self.folder_combo.lineEdit()
        self.folder_edit.setPlaceholderText("Select a data folder")

        self.refresh_button = self._icon_button("Refresh navigator tree")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        enabled = diagnostics_enabled or {}
        self.diagnostic_buttons: dict[str, QToolButton] = {}
        for name in DIAGNOSTIC_BUTTONS:
            button = self._icon_button("", checkable=True)
            button.setChecked(bool(enabled.get(name, True)))
            button.toggled.connect(lambda checked, name=name: self.diagnostic_toggled.emit(name, checked))
            button.toggled.connect(lambda _checked: self.update_icons())
            self.diagnostic_buttons[name] = button
        self.sample_loss_button = self.diagnostic_buttons["sample_loss"]
        self.clipping_button = self.diagnostic_buttons["clipping"]
        self.hide_irrelevant_button = self._icon_button("", checkable=True)
        self.hide_irrelevant_button.setChecked(bool(hide_irrelevant_series))
        self.hide_irrelevant_button.toggled.connect(self.hide_irrelevant_changed.emit)
        self.hide_irrelevant_button.toggled.connect(lambda _checked: self.update_icons())

        # The File bar, above the group box: workspace files.
        self.open_workspace_button = self._icon_button("Open workspace… (Ctrl+O)")
        self.open_workspace_button.clicked.connect(self.workspace_open_requested.emit)
        self.recent_workspaces_button = self._icon_button("Open recent workspace")
        # The main window fills the menu when it is about to show.
        self.recent_workspaces_menu = QMenu(self.recent_workspaces_button)
        self.recent_workspaces_button.setMenu(self.recent_workspaces_menu)
        self.recent_workspaces_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.recent_workspaces_button.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        self.save_workspace_button = self._icon_button("Save workspace (Ctrl+S)")
        self.save_workspace_button.clicked.connect(self.workspace_save_requested.emit)
        self.save_workspace_as_button = self._icon_button("Save workspace as… (Ctrl+Shift+S)")
        self.save_workspace_as_button.clicked.connect(self.workspace_save_as_requested.emit)
        # The plugin batch actions for the whole data folder; the main window fills the
        # menu when it is about to show.
        self.batch_button = self._icon_button("Batch actions for the whole data folder")
        self.batch_menu = QMenu(self.batch_button)
        self.batch_button.setMenu(self.batch_menu)
        self.batch_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.batch_button.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        self.file_bar = QWidget()
        file_layout = QHBoxLayout(self.file_bar)
        file_layout.setContentsMargins(2, 0, 0, 2)
        file_layout.setSpacing(2)
        for button in (self.open_workspace_button, self.recent_workspaces_button, self.save_workspace_button,
                       self.save_workspace_as_button):
            file_layout.addWidget(button)
        file_layout.addStretch(1)

        self.group_by_combo = QComboBox()
        self.group_by_combo.setEditable(True)
        self.filter_key_combo = QComboBox()
        self.filter_key_combo.addItem(FILTER_KEY_PLACEHOLDER)
        self.filter_value_combo = QComboBox()
        self.filter_value_combo.setEditable(True)
        self.filter_value_combo.lineEdit().setPlaceholderText(FILTER_VALUE_PLACEHOLDER)
        self.filter_value_combo.setEnabled(False)

        filter_fields = QWidget()
        filter_layout = QHBoxLayout(filter_fields)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.addWidget(self.filter_key_combo, stretch=1)
        filter_layout.addWidget(self.filter_value_combo, stretch=1)

        # One grid, so every field shares its left and right edges.
        self.form_widget = QWidget()
        form = QGridLayout(self.form_widget)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setColumnStretch(1, 1)
        for row, (text, field) in enumerate((
            ("Data folder:", self.folder_combo),
            ("Group by:", self.group_by_combo),
            ("Filter:", filter_fields),
        )):
            form.addWidget(self._label(text), row, 0)
            form.addWidget(field, row, 1)

        # Tree actions, centered above the tree.
        self.icon_row = QWidget()
        icon_layout = QHBoxLayout(self.icon_row)
        icon_layout.setContentsMargins(0, 4, 0, 0)
        icon_layout.setSpacing(2)
        icon_layout.addStretch(1)
        for button in (self.refresh_button, self.batch_button, self.sample_loss_button, self.clipping_button,
                       self.hide_irrelevant_button):
            icon_layout.addWidget(button)
        icon_layout.addStretch(1)

        # An untitled frame groups the fields and the tree buttons.
        self.frame = QGroupBox()
        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(6, 6, 6, 4)
        frame_layout.setSpacing(0)
        frame_layout.addWidget(self.form_widget)
        frame_layout.addWidget(self.icon_row)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 0)
        layout.setSpacing(0)
        layout.addWidget(self.file_bar)
        layout.addWidget(self.frame)
        self.installEventFilter(self)
        self.update_icons()

    @staticmethod
    def _icon_button(tooltip: str, *, checkable: bool = False) -> QToolButton:
        button = QToolButton()
        button.setAutoRaise(True)
        button.setCheckable(checkable)
        button.setToolTip(tooltip)
        button.setFixedSize(25, 25)
        button.setIconSize(QSize(19, 19))
        return button

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return label

    def update_icons(self) -> None:
        self.refresh_button.setIcon(navigator_asset_icon(self, "refresh"))
        for button, icon in ((self.open_workspace_button, "workspace-open"),
                             (self.recent_workspaces_button, "workspace-open-recent"),
                             (self.save_workspace_button, "workspace-save"),
                             (self.save_workspace_as_button, "workspace-save-as"),
                             (self.batch_button, "batch-h5")):
            button.setIcon(navigator_asset_icon(self, icon))
        hiding = self.hide_irrelevant_button.isChecked()
        self.hide_irrelevant_button.setIcon(
            navigator_asset_icon(self, "hide-irrelevant" if hiding else "show-irrelevant")
        )
        self.hide_irrelevant_button.setToolTip(
            "Show irrelevant series" if hiding else "Hide irrelevant series"
        )
        for name, button in self.diagnostic_buttons.items():
            icon, label = DIAGNOSTIC_BUTTONS[name]
            button.setIcon(navigator_asset_icon(self, icon if button.isChecked() else f"{icon}-off"))
            button.setToolTip(f"{'Hide' if button.isChecked() else 'Show'} {label}")

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self and event.type() in {QEvent.Type.PaletteChange, QEvent.Type.StyleChange}:
            self.update_icons()
        return super().eventFilter(watched, event)


# Navigator diagnostics with a toggle button: setting name -> (icon, tooltip text).
DIAGNOSTIC_BUTTONS = {"sample_loss": ("sample-loss", "sample loss"), "clipping": ("clipping", "clipping")}


def contrasting_icon_color(background: QColor) -> QColor:
    """Choose a high-contrast icon color for either a light or dark palette."""

    luminance = (
        0.2126 * background.redF()
        + 0.7152 * background.greenF()
        + 0.0722 * background.blueF()
    )
    return QColor(28, 28, 28) if luminance >= 0.5 else QColor(238, 238, 238)


def navigator_asset_icon(widget: QWidget, name: str) -> QIcon:
    """Load a compact navigator SVG matched to the active light/dark palette."""
    color = contrasting_icon_color(widget.palette().color(QPalette.ColorRole.Window))
    variant = "light" if color.lightness() < 128 else "dark"
    root = Path(__file__).resolve().parents[3] / "assets" / "navigator_icons"
    return QIcon(str(root / variant / f"{name}.svg"))


def make_expand_icon(expanded: bool, color: QColor) -> QIcon:
    """Draw a solid up/down arrow that stays visible across application themes."""

    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    arrow = QPainterPath()
    if expanded:
        arrow.moveTo(3, 11)
        arrow.lineTo(9, 5)
        arrow.lineTo(15, 11)
    else:
        arrow.moveTo(3, 7)
        arrow.lineTo(9, 13)
        arrow.lineTo(15, 7)
    arrow.closeSubpath()
    painter.drawPath(arrow)
    painter.end()
    return QIcon(pixmap)
