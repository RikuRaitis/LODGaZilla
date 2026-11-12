"""The AI panel: create plugins with an AI agent, below the Navigator.

Presentation only. Expanded, the panel shows two drop boxes side by side:

- **Magic drop**: files dropped on it are emitted with ``files_dropped`` and the
  main window turns them into H5 files with the chosen agent (Claude or Codex); any
  text in the AI input goes along as a hint.
- **Create plugin**: files dropped on it are attached to the next question in the
  AI conversation, which opens with a greeting.

Once either box is used, the boxes shrink to one row above the AI conversation
and AI input. A question is emitted with ``question_submitted``; the main window
sends it to the agent while creating a plugin (Create plugin), and answers with a
note otherwise.
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.navigator.controls import make_expand_icon
from src.gui.plots.icons import plot_control_icon

TITLE = "AI Integration"
PROVIDERS = ("Claude", "Codex")
INPUT_PLACEHOLDER = "Optional hints about the files, such as units; then drop files on Magic drop"
PLUGIN_INPUT_PLACEHOLDER = "Ask a question or describe the plugin you need"
# The main window's answer to a question asked outside Create plugin.
NOT_CONNECTED_REPLY = ("Questions go to the AI while creating a plugin: drop files on Create plugin, "
                       "or ask there. For Magic drop, drop the files on Magic drop.")
# Enter outside Create plugin: the typed text stays in the AI input as hints for the next drop.
HINTS_KEPT_NOTE = "Your text stays as hints for Magic drop: now drop the files on Magic drop."
MAX_INPUT_LINES = 10
MAGIC_DROP = "magic_drop"
CREATE_PLUGIN = "create_plugin"
# Title, description and graphic of each drop box.
DROP_ZONES = {
    MAGIC_DROP: ("Magic drop", "Drop data files: the AI reads them, and LODGaZilla creates H5 files.",
                 "ai-magic-drop"),
    CREATE_PLUGIN: ("Create plugin", "Drop example files and format specifications to build a plugin with AI.",
                    "ai-create-plugin"),
}
PLUGIN_GREETING = (
    "How can I help? Drop example files and the file format specification, or paste a link to it if "
    "there is one, and tell me whether you want me to analyse them and create a LODGaZilla plugin.\n"
    "We can also develop an import plugin for a device, such as a data logger over USB, or an export plugin."
)
ACCENT = QColor("#2196f3")


def dropped_files(event) -> list[Path]:  # type: ignore[no-untyped-def]
    """The local files of a drag or drop event; folders and other data are left out."""
    urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
    return [path for path in (Path(url.toLocalFile()) for url in urls if url.isLocalFile()) if path.is_file()]


class DropZone(QWidget):
    """A drop box: a large box graphic with its title, highlighted while files are dragged over it.

    Compact, it is one row with a small graphic and the title, above the AI conversation.
    """

    files_dropped = Signal(list)
    COMPACT_HEIGHT = 40
    MINIMUM_HEIGHT = 120

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.title, self.description, self.icon_name = DROP_ZONES[kind]
        self.setObjectName(kind)
        self.setAcceptDrops(True)
        self.setAccessibleName(self.title)
        self.setToolTip(self.title)
        self._compact = False
        self._highlighted = False
        self._icon = plot_control_icon(self, self.icon_name)
        self._apply_size()

    @property
    def is_compact(self) -> bool:
        return self._compact

    @property
    def is_highlighted(self) -> bool:
        return self._highlighted

    def set_compact(self, compact: bool) -> None:
        self._compact = bool(compact)
        self._apply_size()
        self.update()

    def _apply_size(self) -> None:
        if self._compact:
            self.setFixedHeight(self.COMPACT_HEIGHT)
        else:
            self.setMinimumHeight(self.MINIMUM_HEIGHT)
            self.setMaximumHeight(16_777_215)

    def changeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        # A theme change swaps the light and dark graphic.
        if event.type() == QEvent.Type.PaletteChange:
            self._icon = plot_control_icon(self, self.icon_name)
            self.update()
        super().changeEvent(event)

    def _set_highlighted(self, highlighted: bool) -> None:
        if highlighted != self._highlighted:
            self._highlighted = highlighted
            self.update()

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if dropped_files(event):
            event.acceptProposedAction()
            self._set_highlighted(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._set_highlighted(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._set_highlighted(False)
        files = dropped_files(event)
        if not files:
            event.ignore()
            return
        event.acceptProposedAction()
        self.files_dropped.emit(files)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        box = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        if self._highlighted:
            fill = QColor(ACCENT)
            fill.setAlpha(45)
            painter.setBrush(fill)
            painter.setPen(QPen(ACCENT, 2))
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(palette.color(QPalette.ColorRole.Mid), 1.5, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(box, 8, 8)
        title_font = self.font()
        title_font.setBold(True)
        title_height = QFontMetrics(title_font).lineSpacing()
        text_color = palette.color(QPalette.ColorRole.WindowText)
        if self._compact:
            size = self.height() - 10
            self._icon.paint(painter, 8, 5, size, size)
            painter.setFont(title_font)
            painter.setPen(text_color)
            painter.drawText(QRectF(14 + size, 0, self.width() - 22 - size, self.height()),
                             int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.title)
            return
        # The graphic as large as fits above the title and the wrapped description.
        wrap = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap)
        text_width = max(1, self.width() - 20)
        description_height = self.fontMetrics().boundingRect(0, 0, text_width, 10_000, wrap,
                                                             self.description).height()
        text_height = title_height + 2 + description_height
        size = int(max(16, min(self.width() - 24, self.height() - text_height - 20, 160)))
        top = max(6, (self.height() - size - 4 - text_height) // 2)
        self._icon.paint(painter, (self.width() - size) // 2, top, size, size)
        title_top = top + size + 4
        painter.setFont(title_font)
        painter.setPen(text_color)
        painter.drawText(QRectF(8, title_top, self.width() - 16, title_height),
                         int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), self.title)
        painter.setFont(self.font())
        painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
        painter.drawText(QRectF(10, title_top + title_height + 2, text_width, description_height), wrap,
                         self.description)


class AttachmentChip(QWidget):
    """One file attached to the next question, shown by name with a remove button."""

    remove_requested = Signal(Path)

    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(2)
        self.label = QLabel(path.name, self)
        self.label.setToolTip(str(path))
        self.remove_button = QToolButton(self)
        self.remove_button.setAutoRaise(True)
        self.remove_button.setIcon(plot_control_icon(self, "close"))
        self.remove_button.setIconSize(QSize(12, 12))
        self.remove_button.setToolTip(f"Remove {path.name}")
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self.path))
        layout.addWidget(self.label)
        layout.addWidget(self.remove_button)
        self.setStyleSheet("AttachmentChip { border: 1px solid palette(mid); border-radius: 10px; }")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


class AIInputFrame(QFrame):
    """Attachment chips and the AI input with its Send/Stop button.

    The AI input grows with its text up to MAX_INPUT_LINES; the frame then
    overlays the bottom of the AI conversation instead of shrinking it.
    """

    height_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ColorRole.Window)

        self.chip_row = QWidget(self)
        self.chip_layout = QHBoxLayout(self.chip_row)
        self.chip_layout.setContentsMargins(0, 0, 0, 0)
        self.chip_layout.setSpacing(4)
        self.chip_layout.addStretch(1)
        self.chip_row.hide()

        self.input = QPlainTextEdit(self)
        self.input.setPlaceholderText(INPUT_PLACEHOLDER)
        self.input.setTabChangesFocus(True)
        self.input.setFrameShape(QFrame.Shape.NoFrame)
        self.input.document().documentLayout().documentSizeChanged.connect(self.fit_input)
        self.send_button = QToolButton(self)
        self.send_button.setAutoRaise(True)
        self.send_button.setIconSize(QSize(20, 20))

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.addWidget(self.input, stretch=1)
        input_row.addWidget(self.send_button, alignment=Qt.AlignmentFlag.AlignBottom)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(self.chip_row)
        layout.addLayout(input_row)
        self.fit_input()

    def input_height(self, lines: int) -> int:
        margins = self.input.contentsMargins()
        document_margin = round(self.input.document().documentMargin())
        return (lines * self.input.fontMetrics().lineSpacing()
                + 2 * document_margin + margins.top() + margins.bottom())

    def input_lines(self) -> int:
        # A QPlainTextEdit document's height is its number of laid-out lines, wrapped ones included.
        lines = round(self.input.document().documentLayout().documentSize().height())
        return min(MAX_INPUT_LINES, max(1, lines))

    def fit_input(self, *_args) -> None:  # type: ignore[no-untyped-def]
        lines = self.input_lines()
        self.input.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if lines >= MAX_INPUT_LINES else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        height = self.input_height(lines)
        if height != self.input.height() or self.input.minimumHeight() != height:
            self.input.setFixedHeight(height)
            self.height_changed.emit()

    def frame_height(self, lines: int | None = None) -> int:
        """The frame's height for an AI input of ``lines`` lines (default: its current text)."""
        layout = self.layout()
        margins = layout.contentsMargins()
        row = max(self.input_height(self.input_lines() if lines is None else lines),
                  self.send_button.sizeHint().height())
        chips = 0 if self.chip_row.isHidden() else self.chip_row.sizeHint().height() + layout.spacing()
        return 2 * self.frameWidth() + margins.top() + margins.bottom() + chips + row


class _ConversationArea(QWidget):
    """The AI conversation with the AI input frame anchored to its bottom edge."""

    def __init__(self, conversation: QTextBrowser, input_frame: AIInputFrame, parent: QWidget) -> None:
        super().__init__(parent)
        self.conversation = conversation
        self.input_frame = input_frame
        conversation.setParent(self)
        input_frame.setParent(self)
        input_frame.height_changed.connect(self.update_geometry)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self.update_geometry()

    def update_geometry(self) -> None:
        width, height = self.width(), self.height()
        # The conversation leaves room for a one-line input; a taller input overlays it.
        resting = self.input_frame.frame_height(1)
        frame_height = min(height, self.input_frame.frame_height())
        self.conversation.setGeometry(0, 0, width, max(0, height - resting - 4))
        self.input_frame.setGeometry(0, height - frame_height, width, frame_height)
        self.input_frame.raise_()


class AIPanel(QWidget):
    """A one-line AI Integration row that expands to the drop boxes, AI conversation and AI input."""

    expanded_changed = Signal(bool)
    # The AI conversation opened, once, when a drop box was first used.
    conversation_started = Signal()
    question_submitted = Signal(str, list)
    stop_requested = Signal()
    sign_in_requested = Signal(str)
    provider_changed = Signal(str)
    # Magic drop: the dropped files and the hint text from the AI input.
    files_dropped = Signal(list, str)
    MINI_HEIGHT = 26
    # Expanded: the header and the drop boxes at their smallest.
    EXPANDED_MIN_HEIGHT = 190

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aiPanel")
        self.setAcceptDrops(True)
        self._expanded = False
        self._busy = False
        # The drop box last used: MAGIC_DROP or CREATE_PLUGIN; None until one is.
        self._mode: str | None = None
        self._attachments: list[Path] = []
        self._signed_in: dict[str, bool] = {provider: False for provider in PROVIDERS}

        # Minimized: one clickable row.
        self.mini_row = QToolButton(self)
        self.mini_row.setText(TITLE)
        self.mini_row.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.mini_row.setAutoRaise(True)
        self.mini_row.setFixedHeight(self.MINI_HEIGHT)
        self.mini_row.setSizePolicy(self.mini_row.sizePolicy().Policy.Expanding,
                                    self.mini_row.sizePolicy().Policy.Fixed)
        self.mini_row.setToolTip(f"Expand {TITLE}")
        self.mini_row.clicked.connect(lambda: self.set_expanded(True))

        # Expanded: header, AI conversation and the AI input frame over its bottom.
        self.body = QWidget(self)
        self.title_label = QLabel(f"<b>{TITLE}</b>", self.body)
        self.provider_combo = QComboBox(self.body)
        self.provider_combo.addItems(PROVIDERS)
        self.provider_combo.setToolTip("AI provider")
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        self.status_label = QLabel(self.body)
        self.sign_in_button = QPushButton("Sign in", self.body)
        self.sign_in_button.clicked.connect(lambda: self.sign_in_requested.emit(self.provider))
        self.collapse_button = QToolButton(self.body)
        self.collapse_button.setAutoRaise(True)
        self.collapse_button.setToolTip(f"Minimize {TITLE}")
        self.collapse_button.clicked.connect(lambda: self.set_expanded(False))
        header = QHBoxLayout()
        header.setContentsMargins(6, 0, 2, 0)
        header.addWidget(self.title_label)
        header.addWidget(self.provider_combo)
        header.addWidget(self.status_label, stretch=1)
        header.addWidget(self.sign_in_button)
        header.addWidget(self.collapse_button)

        # The drop boxes, side by side: large until one is used, then one compact row.
        self.drop_row = QWidget(self.body)
        drop_layout = QHBoxLayout(self.drop_row)
        drop_layout.setContentsMargins(0, 0, 0, 0)
        drop_layout.setSpacing(6)
        self.magic_drop_zone = DropZone(MAGIC_DROP, self.drop_row)
        self.plugin_zone = DropZone(CREATE_PLUGIN, self.drop_row)
        for zone in (self.magic_drop_zone, self.plugin_zone):
            drop_layout.addWidget(zone, stretch=1)
        self.magic_drop_zone.files_dropped.connect(self.start_magic_drop)
        self.plugin_zone.files_dropped.connect(self.attach_for_plugin)

        self.conversation = QTextBrowser()
        self.conversation.setOpenExternalLinks(False)
        self.input_frame = AIInputFrame()
        self.input = self.input_frame.input
        self.send_button = self.input_frame.send_button
        self.chip_row = self.input_frame.chip_row
        self.chip_layout = self.input_frame.chip_layout
        self.send_button.clicked.connect(self._on_send_button)
        self.input.installEventFilter(self)
        self.conversation_area = _ConversationArea(self.conversation, self.input_frame, self.body)

        # Files dropped on the text boxes reach the panel: while creating a plugin they are attached.
        for text_box in (self.conversation, self.input):
            text_box.setAcceptDrops(False)
            text_box.viewport().setAcceptDrops(False)
        self.conversation_area.hide()

        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(4, 0, 4, 4)
        body_layout.setSpacing(4)
        body_layout.addLayout(header)
        body_layout.addWidget(self.drop_row, stretch=1)
        body_layout.addWidget(self.conversation_area, stretch=1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.mini_row)
        layout.addWidget(self.body)
        self._apply_expanded_state()
        self._update_status()
        self._update_send_button()

    # Expanded and minimized states.

    @property
    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._apply_expanded_state()
        self.expanded_changed.emit(expanded)
        if expanded:
            self.input.setFocus(Qt.FocusReason.OtherFocusReason)

    def _apply_expanded_state(self) -> None:
        color = self.palette().color(self.foregroundRole())
        self.mini_row.setIcon(make_expand_icon(True, color))
        self.collapse_button.setIcon(make_expand_icon(False, color))
        self.mini_row.setVisible(not self._expanded)
        self.body.setVisible(self._expanded)
        if self._expanded:
            self.setMinimumHeight(self.EXPANDED_MIN_HEIGHT)
            self.setMaximumHeight(16_777_215)
        else:
            self.setFixedHeight(self.MINI_HEIGHT)
        self.updateGeometry()

    # Provider and sign-in status.

    @property
    def provider(self) -> str:
        return self.provider_combo.currentText()

    def set_signed_in(self, provider: str, signed_in: bool) -> None:
        self._signed_in[provider] = bool(signed_in)
        self._update_status()

    def _on_provider_changed(self, provider: str) -> None:
        self._update_status()
        self.provider_changed.emit(provider)

    def _update_status(self) -> None:
        signed_in = self._signed_in.get(self.provider, False)
        self.status_label.setText(f"{self.provider}: {'signed in' if signed_in else 'not signed in'}")
        self.sign_in_button.setVisible(not signed_in)

    # Send and Stop.

    @property
    def is_busy(self) -> bool:
        return self._busy

    def set_busy(self, busy: bool) -> None:
        """While the agent is thinking, the Send button becomes Stop."""
        self._busy = bool(busy)
        self._update_send_button()

    def _update_send_button(self) -> None:
        self.send_button.setIcon(plot_control_icon(self, "ai-stop" if self._busy else "ai-send"))
        self.send_button.setToolTip("Stop" if self._busy else "Send")
        self.provider_combo.setEnabled(not self._busy)

    def _on_send_button(self) -> None:
        if self._busy:
            self.stop_requested.emit()
        else:
            self.submit()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        # Enter sends; Shift+Enter starts a new line.
        if (watched is self.input and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            if not self._busy:
                self.submit()
            return True
        return super().eventFilter(watched, event)

    # Attachments.

    def attached_files(self) -> list[Path]:
        return list(self._attachments)

    def add_attachments(self, paths) -> None:  # type: ignore[no-untyped-def]
        """Attach files once each to the next question; folders and missing paths are ignored."""
        for path in (Path(item) for item in paths):
            if not path.is_file() or path in self._attachments:
                continue
            self._attachments.append(path)
            chip = AttachmentChip(path, self.chip_row)
            chip.remove_requested.connect(self.remove_attachment)
            self.chip_layout.insertWidget(self.chip_layout.count() - 1, chip)
        self._show_chips()

    def remove_attachment(self, path: Path) -> None:
        if path not in self._attachments:
            return
        self._attachments.remove(path)
        for chip in self._chips():
            if chip.path == path:
                self.chip_layout.removeWidget(chip)
                chip.hide()
                chip.deleteLater()
        self._show_chips()

    def _show_chips(self) -> None:
        self.chip_row.setVisible(bool(self._attachments))
        self.input_frame.height_changed.emit()

    def _chips(self) -> list[AttachmentChip]:
        items = (self.chip_layout.itemAt(index).widget() for index in range(self.chip_layout.count()))
        return [item for item in items if isinstance(item, AttachmentChip)]

    # The AI conversation.

    def submit(self) -> None:
        """Show the question and its files in the AI conversation and clear the AI input.

        Outside Create plugin, text alone is kept in the AI input as Magic drop hints.
        """
        text = self.input.toPlainText().strip()
        files = self.attached_files()
        if not text and not files:
            return
        if self._mode != CREATE_PLUGIN and not files:
            self.append_note(HINTS_KEPT_NOTE)
            return
        self.append_user_message(text, files)
        for path in files:
            self.remove_attachment(path)
        self.input.clear()
        self.question_submitted.emit(text, files)

    def append_user_message(self, text: str, files: list[Path]) -> None:
        lines = ["<b>You</b>"]
        if text:
            lines.append(html.escape(text).replace("\n", "<br>"))
        lines.extend(f"&#128206; {html.escape(path.name)}" for path in files)
        self._append_block("<br>".join(lines), quoted=True)

    def append_agent_message(self, text: str, agent: str = "") -> None:
        """A reply, headed by the agent that wrote it (default: the chosen provider)."""
        self._append_block(f"<b>{html.escape(agent or self.provider)}</b><br>"
                           f"{html.escape(text).replace(chr(10), '<br>')}", quoted=False)

    def append_note(self, text: str) -> None:
        """A progress line, such as a step or a tool the agent used, in the secondary text color."""
        color = self.palette().color(QPalette.ColorRole.PlaceholderText).name()
        self._append_block(f'<span style="color: {color};">{html.escape(text).replace(chr(10), "<br>")}</span>',
                           quoted=False)

    def _append_block(self, body: str, *, quoted: bool) -> None:
        if quoted:
            border = self.palette().color(QPalette.ColorRole.Mid).name()
            body = (f'<table width="100%" cellpadding="6" style="border: 1px solid {border};">'
                    f"<tr><td>{body}</td></tr></table>")
        self.conversation.append(body)
        scrollbar = self.conversation.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # The drop boxes.

    @property
    def mode(self) -> str | None:
        """The drop box last used, MAGIC_DROP or CREATE_PLUGIN; None while only the boxes show."""
        return self._mode

    def _open_conversation(self, mode: str) -> None:
        """Shrink the drop boxes to one row and show the AI conversation and AI input."""
        first = self._mode is None
        self._mode = mode
        self.input.setPlaceholderText(PLUGIN_INPUT_PLACEHOLDER if mode == CREATE_PLUGIN else INPUT_PLACEHOLDER)
        if first:
            for zone in (self.magic_drop_zone, self.plugin_zone):
                zone.set_compact(True)
            layout = self.body.layout()
            layout.setStretchFactor(self.drop_row, 0)
            self.conversation_area.show()
            self.conversation_started.emit()

    def start_magic_drop(self, files: list[Path]) -> None:
        """Files dropped on Magic drop: shown in the AI conversation and emitted with the typed hints."""
        self.set_expanded(True)
        self._open_conversation(MAGIC_DROP)
        hints = self.input.toPlainText().strip()
        self.append_user_message(f"Magic drop{': ' + hints if hints else ''}", files)
        self.input.clear()
        self.files_dropped.emit(files, hints)

    def attach_for_plugin(self, files: list[Path]) -> None:
        """Files dropped on Create plugin: attached to the next question, after a greeting."""
        self.set_expanded(True)
        if self._mode != CREATE_PLUGIN:
            self._open_conversation(CREATE_PLUGIN)
            self.append_agent_message(PLUGIN_GREETING)
        self.add_attachments(files)
        self.input.setFocus(Qt.FocusReason.OtherFocusReason)

    # Files dragged over the panel outside the drop boxes.

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if not dropped_files(event):
            event.ignore()
            return
        # Over the AI Integration row, the panel opens so the drop boxes can take the files.
        self.set_expanded(True)
        event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        # While creating a plugin, the AI conversation and AI input take files too; elsewhere only the boxes do.
        if dropped_files(event) and self._accepts_attachment_drop(event.position().toPoint()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        files = dropped_files(event)
        if not files or not self._accepts_attachment_drop(event.position().toPoint()):
            event.ignore()
            return
        event.acceptProposedAction()
        self.attach_for_plugin(files)

    def _accepts_attachment_drop(self, position) -> bool:  # type: ignore[no-untyped-def]
        area = self.conversation_area
        return (self._mode == CREATE_PLUGIN and area.isVisible()
                and area.rect().contains(area.mapFrom(self, position)))
