"""Shared card behavior for plot types hosted by LODGaZilla."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPalette
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
                               QToolButton, QVBoxLayout, QWidget)

from src.app.settings import SERIES_COLOR_DEFAULTS
from src.data.plot_policy import CARD_TITLES
from src.gui.plots.card_help import mouse_help_html
from src.gui.plots.icons import plot_control_icon
from src.data.sampling import (
    SAMPLE_LIMIT_ALL, SAMPLE_LIMIT_CHOICES, coverage_percent_text, estimated_shown, sample_counts,
)


SERIES_MIME_TYPE = "application/x-lodgazilla-series"
PLOT_MIME_TYPE = "application/x-lodgazilla-plot"

# Draw style selector: how a card draws its series. Auto is each card's own way (a time
# card adds points only when zoomed in close); Lines draws only lines, Points only points.
DRAW_AUTO = "auto"
DRAW_LINES = "lines"
DRAW_POINTS = "points"
DRAW_STYLES = {DRAW_AUTO: "Auto: lines, and points when zoomed in", DRAW_LINES: "Lines",
               DRAW_POINTS: "Points, without lines between them"}


def _limit_text(limit: int) -> str:
    """Short Sample limit selector text: "20k max", "1M max" or "all"."""
    if limit <= SAMPLE_LIMIT_ALL:
        return "all"
    if limit % 1_000_000 == 0:
        return f"{limit // 1_000_000}M max"
    return f"{limit // 1_000}k max" if limit % 1_000 == 0 else f"{limit} max"


def series_legend_name(h5_path: Path, series_name: str) -> str:
    """Return a disambiguating legend name without changing the H5 identifier."""
    folder_name = Path(h5_path).parent.name
    return f"{folder_name}/{series_name}" if folder_name else str(series_name)


def first_unused_color(palette, used, fallback_index: int = 0) -> str:  # type: ignore[no-untyped-def]
    """Return the first palette color not used in a card, cycling once all are used."""
    used_names = {str(color).casefold() for color in used}
    return next(
        (color for color in palette if color.casefold() not in used_names),
        palette[fallback_index % len(palette)],
    )


# Default series, track and lane colors, from the Settings dialog. Cards share
# this list, so `set_series_colors` changes it in place.
SERIES_COLORS = list(SERIES_COLOR_DEFAULTS)


def set_series_colors(colors) -> None:  # type: ignore[no-untyped-def]
    """Use these default colors for series added from now on."""
    SERIES_COLORS[:] = list(colors) or list(SERIES_COLOR_DEFAULTS)


def assign_series_colors(entries, previous, palette) -> list[str]:  # type: ignore[no-untyped-def]
    """Keep each retained source's color; give new sources an unused palette color."""
    colors = {entry: previous[entry] for entry in entries if entry in previous}
    used = list(colors.values())
    for index, entry in enumerate(entries):
        if entry not in colors:
            colors[entry] = first_unused_color(palette, used, index)
            used.append(colors[entry])
    return [colors[entry] for entry in entries]


def entry_state(entry) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """A series source as saved in a workspace file."""
    return {"path": str(entry[0]), "name": str(entry[1])}


def state_entry(state) -> tuple[Path, str]:  # type: ignore[no-untyped-def]
    return Path(state["path"]), str(state["name"])


def view_range_state(view_box) -> list[list[float]]:  # type: ignore[no-untyped-def]
    return [[float(low), float(high)] for low, high in view_box.viewRange()]


def apply_view_range_state(view_box, state, *, y=True) -> None:  # type: ignore[no-untyped-def]
    """Show a saved ``[[x0, x1], [y0, y1]]`` range; malformed ranges are ignored."""
    try:
        (x_low, x_high), (y_low, y_high) = state
        ranges = {"xRange": (float(x_low), float(x_high))}
        if y:
            ranges["yRange"] = (float(y_low), float(y_high))
    except (TypeError, ValueError):
        return
    view_box.setRange(**ranges, padding=0)


class MouseHelpPopup(QLabel):
    """A card's mouse help; any mouse press outside it, or leaving the window, closes it."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget) and watched.isWidgetType():
            if watched is not self and not self.isAncestorOf(watched):
                card = self.parentWidget()
                # A press on the help button closes the help; its click must not reopen it.
                card._help_closed_by_button = watched is getattr(card, "help_button", None)
                self.close()
        elif kind == QEvent.Type.ApplicationDeactivate:
            self.close()
        return False

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        QApplication.instance().removeEventFilter(self)
        card = self.parentWidget()
        if getattr(card, "_help_popup", None) is self:
            card._help_popup = None
        super().closeEvent(event)


class TimeOffsetEditor(QDoubleSpinBox):
    """The card's time offset field; Escape closes it like a click elsewhere."""

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.key() == Qt.Key.Key_Escape:
            self.clearFocus()
            event.accept()
            return
        super().keyPressEvent(event)


class PlotBaseWidget(QFrame):
    """Common interactive frame for all LODGaZilla plot types."""

    activated = Signal(object)
    close_requested = Signal(object)
    series_dropped = Signal(object, Path, str)
    series_inserted = Signal(object, Path, str, int)
    legend_changed = Signal(object)
    plot_dropped = Signal(object, object, bool)
    reorder_drag_started = Signal(object)
    reorder_drag_finished = Signal()
    height_resize_requested = Signal(object, int)
    wheel_scrolled = Signal(int)
    # The card and its previous time offset, after the offset changed.
    time_offset_changed = Signal(object, float)
    # Cards without a time base, such as images, have no time offset.
    supports_time_offset = True
    supports_horizontal_layout = False
    accepts_series_drop = False
    accepts_axis_drop = False
    accepts_time_axis_drop = False
    # Cards that draw every Nth sample set a default to show the Sample limit selector.
    default_sample_limit: int | None = None
    sample_limit_noun = "graph"
    # Cards that draw series as lines show the Draw style selector and implement `apply_draw_style`.
    supports_draw_style = False
    # The card type whose mouse actions the help button lists, from `card_help.MOUSE_HELP`.
    help_topic: str | None = None

    def __init__(self, h5_path: Path, series_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.series: list[tuple[Path, str]] = [(Path(h5_path), str(series_name))]
        self._drag_start: QPoint | None = None
        self.setObjectName("lodgazillaPlot")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(80)
        self.setAcceptDrops(True)
        self.setProperty("activePlot", False)
        # Card controls: the header controls are hidden until the card's Card controls button
        # opens them; they close again when the card stops being active. The strip still drags.
        self._header_controls_shown = False
        self._suppressed_header_controls: list[QWidget] = []

        self.close_button = QToolButton(self)
        self.close_button.setIconSize(QSize(18, 18))
        self.close_button.setIcon(plot_control_icon(self, "close"))
        self.close_button.setAccessibleName("Close plot")
        self.close_button.setToolTip("Close plot")
        self.close_button.setAutoRaise(True)
        self.close_button.setFixedSize(26, 26)
        self.close_button.clicked.connect(lambda: self.close_requested.emit(self))
        # Left of the Close button: opens and closes this card's header controls.
        self.controls_button = QToolButton(self)
        self.controls_button.setCheckable(True)
        self.controls_button.setIconSize(QSize(18, 18))
        self.controls_button.setAccessibleName("Card controls")
        self.controls_button.setAutoRaise(True)
        self.controls_button.setFixedSize(self.close_button.size())
        self.controls_button.clicked.connect(self._controls_button_clicked)
        self._show_controls_button_state()
        self.drag_handle = PlotDragHandle(self)
        self.resize_handle = PlotHeightHandle(self)
        self.resize_handle.dragged.connect(lambda delta: self.height_resize_requested.emit(self, delta))
        # Extra header controls, laid out right to left starting beside the Card controls button,
        # and left to right from the card's left edge. The rest of the strip drags the card.
        self.header_controls: list[QWidget] = []
        self.header_left_controls: list[QWidget] = []
        # Display time offset, immediately left of the Close button: shared time = card time + offset.
        # The Offset button opens the Offset editor in its place; it closes when it loses focus.
        self._time_offset = 0.0
        self._offset_icon_name = "time-offset"
        self.offset_button = QToolButton(self)
        self.offset_button.setAccessibleName("Time offset")
        self.offset_button.setToolTip("Time offset")
        self.offset_button.setAutoRaise(True)
        self.offset_button.setFixedSize(self.close_button.size())
        self.offset_button.setIconSize(QSize(18, 18))
        self.offset_button.setIcon(plot_control_icon(self, self._offset_icon_name))
        self.offset_button.clicked.connect(self.show_time_offset_editor)
        self.offset_editor = TimeOffsetEditor(self)
        self.offset_editor.setAccessibleName("Time offset")
        self.offset_editor.setToolTip("Time offset")
        self.offset_editor.setRange(-1e7, 1e7)
        self.offset_editor.setDecimals(3)
        self.offset_editor.setSingleStep(0.1)
        self.offset_editor.setSuffix(" s")
        self.offset_editor.setKeyboardTracking(False)
        self.offset_editor.setFixedSize(110, self.close_button.height() - 2)
        self.offset_editor.hide()
        self.offset_editor.valueChanged.connect(self.set_edited_time_offset)
        self.offset_editor.editingFinished.connect(self.hide_time_offset_editor)
        self.offset_button.setVisible(self.supports_time_offset)
        self.header_controls.extend((self.offset_button, self.offset_editor))
        # Mouse help, left of the Offset button; the card's only mouse-usage hint.
        self.help_button = QToolButton(self)
        self.help_button.setToolTip("Mouse actions in this card")
        self.help_button.setAccessibleName("Mouse actions")
        self.help_button.setAutoRaise(True)
        self.help_button.setFixedSize(self.close_button.size())
        self.help_button.setIconSize(QSize(18, 18))
        self.help_button.setIcon(plot_control_icon(self, "help"))
        self.help_button.clicked.connect(self.show_mouse_help)
        # The flag set when a press on this button closed the help applies to that press's
        # click only; clear it after the release (clicked comes first), even without a click.
        self.help_button.released.connect(
            lambda: QTimer.singleShot(0, self, lambda: setattr(self, "_help_closed_by_button", False)))
        self.header_controls.append(self.help_button)
        self._help_popup: MouseHelpPopup | None = None
        self._help_closed_by_button = False
        self.sample_limit = self.default_sample_limit
        if self.default_sample_limit is not None:
            self._create_sample_limit_selector()
        self.draw_style = DRAW_AUTO
        if self.supports_draw_style:
            self._create_draw_style_selector()

        self.frame_layout = QVBoxLayout(self)
        self.frame_layout.setContentsMargins(3, 3, 3, 5)
        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.frame_layout.addLayout(self.content_layout, stretch=1)
        self._apply_active_style()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        # Leave the full frame border unobscured; this interactive area must
        # not make the bottom active border appear thinner than the other sides.
        self.resize_handle.setGeometry(8, max(0, self.height() - 8), max(1, self.width() - 16), 6)
        # Preserve the six-pixel grip with the tighter bottom padding. Its
        # transparent top pixel overlaps the content and must receive input.
        self.resize_handle.raise_()
        button_size = self.close_button.width()
        self.drag_handle.setGeometry(3, 3, max(1, self.width() - 6), button_size + 4)
        self.close_button.setGeometry(max(0, self.width() - button_size - 5), 5, button_size, button_size)
        self.controls_button.setGeometry(max(0, self.close_button.x() - button_size - 2), 5, button_size, button_size)
        self.drag_handle.raise_()
        self.close_button.raise_()
        self.controls_button.raise_()
        self._layout_header_controls()

    def show_mouse_help(self) -> None:
        """List this card's mouse actions in a popup that closes on the next click outside it."""
        if self._help_closed_by_button:
            self._help_closed_by_button = False
            return
        popup = MouseHelpPopup(self)
        popup.setTextFormat(Qt.TextFormat.RichText)
        popup.setText(mouse_help_html(CARD_TITLES.get(self.help_topic or "", "Card"), self.help_topic))
        popup.setMargin(8)
        popup.setMaximumWidth(560)
        popup.setWordWrap(True)
        popup.setFrameShape(QFrame.Shape.Box)
        popup.adjustSize()
        corner = self.help_button.mapToGlobal(self.help_button.rect().bottomRight())
        popup.move(corner - QPoint(popup.width(), -2))
        popup.show()
        self._help_popup = popup

    def changeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().changeEvent(event)
        if event.type() in {QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange} and hasattr(self, "help_button"):
            self.close_button.setIcon(plot_control_icon(self, "close"))
            self._show_controls_button_state()
            self.help_button.setIcon(plot_control_icon(self, "help"))
            self.offset_button.setIcon(plot_control_icon(self, self._offset_icon_name))
            for style, button in getattr(self, "draw_style_buttons", {}).items():
                button.setIcon(plot_control_icon(self, f"draw-{style}"))

    @property
    def time_offset(self) -> float:
        """Seconds added to this card's time to show it on the shared time axis."""
        return self._time_offset

    def set_time_offset(self, seconds: float) -> None:
        """Move what this card shows in time; its data is unchanged."""
        seconds = float(seconds)
        previous = self.time_offset
        if abs(seconds - previous) < 0.0005:
            return
        self._time_offset = seconds
        self._show_time_offset()
        self.time_offset_changed.emit(self, previous)

    def reset_time_offset(self) -> None:
        self.set_time_offset(0.0)

    # The Offset button, its editor and a Plot navigator bar drag change the edited offset:
    # the card's own, or in cards that offset series separately, the selected series'.
    def edited_time_offset(self) -> float:
        return self.time_offset

    def set_edited_time_offset(self, seconds: float) -> None:
        self.set_time_offset(seconds)

    def edited_time_offset_name(self) -> str | None:
        """The series whose offset the Offset button edits, or None for the whole card."""
        return None

    def series_time_offset(self, entry: tuple[Path, str]) -> float:
        """Seconds one series is shown later than the rest of the card; 0 unless the card offsets series."""
        return 0.0

    def has_time_offset(self) -> bool:
        """Whether anything in the card is shown at a time offset."""
        return abs(self.time_offset) >= 0.0005

    def _show_time_offset(self) -> None:
        """The Offset button is highlighted while an offset is set, and names what it edits."""
        offset = self.edited_time_offset()
        name = self.edited_time_offset_name()
        self._offset_icon_name = "time-offset-set" if self.has_time_offset() else "time-offset"
        self.offset_button.setIcon(plot_control_icon(self, self._offset_icon_name))
        label = f"Time offset of {name}" if name else "Time offset"
        self.offset_button.setToolTip(f"{label}: {offset:+.3f} s" if abs(offset) >= 0.0005 else label)
        self.offset_editor.setToolTip(label)
        if abs(self.offset_editor.value() - offset) >= 0.0005:
            self.offset_editor.blockSignals(True)
            self.offset_editor.setValue(offset)
            self.offset_editor.blockSignals(False)

    def show_time_offset_editor(self) -> None:
        self.offset_editor.blockSignals(True)
        self.offset_editor.setValue(self.edited_time_offset())
        self.offset_editor.blockSignals(False)
        self.offset_button.hide()
        self.offset_editor.show()
        self._layout_header_controls()
        self.offset_editor.setFocus(Qt.FocusReason.OtherFocusReason)
        self.offset_editor.selectAll()

    def hide_time_offset_editor(self) -> None:
        if self.offset_editor.isHidden():
            return
        self.offset_editor.hide()
        self.offset_button.setVisible(self.supports_time_offset)
        self._layout_header_controls()

    def header_widgets(self) -> list[QWidget]:
        """The Close and Card controls buttons and every other control in the Drag strip's row."""
        return [self.close_button, self.controls_button, *self.header_controls, *self.header_left_controls]

    @property
    def header_controls_shown(self) -> bool:
        return self._header_controls_shown

    def _controls_button_clicked(self) -> None:
        """Open this card's controls and make it the active card, or close them."""
        if self._header_controls_shown:
            self.set_header_controls_shown(False)
            return
        self.activated.emit(self)
        self.set_header_controls_shown(True)

    def _show_controls_button_state(self) -> None:
        shown = getattr(self, "_header_controls_shown", False)
        self.controls_button.setChecked(shown)
        self.controls_button.setIcon(plot_control_icon(self, "card-controls-shown" if shown else "card-controls-hidden"))
        tip = "Hide card controls" if shown else "Show card controls"
        self.controls_button.setToolTip(tip)

    def set_header_controls_shown(self, shown: bool) -> None:
        """Show or hide the header controls, including Offset and Help; Close and Card controls stay."""
        shown = bool(shown)
        if shown == getattr(self, "_header_controls_shown", False):
            self._show_controls_button_state()
            return
        if not shown:
            self.hide_time_offset_editor()
            if self._help_popup is not None:
                self._help_popup.close()
        self._header_controls_shown = shown
        self._show_controls_button_state()
        if shown:
            for control in self._suppressed_header_controls:
                control.show()
            self._suppressed_header_controls = []
            self._restore_header_control_states()
        self._layout_header_controls()

    def _restore_header_control_states(self) -> None:
        """Hide again the controls that are shown only in some states, after the header returns."""
        self.offset_editor.hide()
        self.offset_button.setVisible(self.supports_time_offset)

    def _layout_header_controls(self) -> None:
        if not getattr(self, "_header_controls_shown", False):
            # Controls a card shows while the header is hidden are hidden too, and return with it.
            for control in self.header_widgets():
                if control not in (self.close_button, self.controls_button) and not control.isHidden():
                    control.hide()
                    if control not in self._suppressed_header_controls:
                        self._suppressed_header_controls.append(control)
        button_size = self.close_button.width()
        # Right to left from the Card controls button; a control that would cross the card's left
        # edge continues on a row below, from the right, as the 3D Map's control groups do.
        row_right = self.close_button.x() + button_size
        x, y = self.controls_button.x(), self.close_button.y()
        for control in self.header_controls:
            if control.isHidden():
                continue
            if x - control.width() - 2 < 8 and x < row_right:
                x, y = row_right + 2, y + button_size + 4
            x -= control.width() + 2
            control.move(x, y + (button_size - control.height()) // 2)
            control.raise_()
        x = 8
        for control in self.header_left_controls:
            if control.isHidden():
                continue
            control.move(x, self.close_button.y() + (button_size - control.height()) // 2)
            control.raise_()
            x += control.width() + 2

    def _create_draw_style_selector(self) -> None:
        """Three buttons, Auto, Lines and Points, side by side in the header row."""
        from src.gui.plots.theme import palette_overlay_stylesheet

        self.draw_style_selector = QWidget(self)
        self.draw_style_selector.setAccessibleName("Draw style")
        layout = QHBoxLayout(self.draw_style_selector)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(0)
        group = QButtonGroup(self.draw_style_selector)
        group.setExclusive(True)
        self.draw_style_buttons: dict[str, QToolButton] = {}
        for style, name in DRAW_STYLES.items():
            button = QToolButton(self.draw_style_selector)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setToolTip(name)
            button.setAccessibleName(f"Draw {style}")
            button.setFixedSize(self.close_button.height() - 2, self.close_button.height() - 2)
            button.setIconSize(QSize(18, 18))
            button.setIcon(plot_control_icon(self, f"draw-{style}"))
            button.clicked.connect(lambda _checked=False, style=style: self.set_draw_style(style))
            group.addButton(button)
            layout.addWidget(button)
            self.draw_style_buttons[style] = button
        self.draw_style_buttons[self.draw_style].setChecked(True)
        # The chosen style is the highlighted button.
        self.draw_style_selector.setStyleSheet(
            palette_overlay_stylesheet()
            + " QToolButton { border: none; border-radius: 3px; background: transparent; }"
            " QToolButton:hover { background: palette(midlight); }"
            " QToolButton:checked { background: rgba(33, 150, 243, 0.40); }")
        self.draw_style_selector.adjustSize()
        self.header_controls.append(self.draw_style_selector)

    def set_draw_style(self, style: str) -> None:
        """Draw the card's series as Auto, Lines or Points (`DRAW_STYLES`)."""
        if style not in DRAW_STYLES or not self.supports_draw_style:
            return
        self.draw_style_buttons[style].setChecked(True)
        if style == self.draw_style:
            return
        self.draw_style = style
        self.apply_draw_style(style)

    def apply_draw_style(self, style: str) -> None:
        """Hook: redraw the series in this style; cards with `supports_draw_style` implement it."""

    def _create_sample_limit_selector(self) -> None:
        choices = sorted(set(SAMPLE_LIMIT_CHOICES) | {self.default_sample_limit},
                         key=lambda limit: (limit <= 0, limit))
        self.sample_limit_combo = QComboBox(self)
        self.sample_limit_combo.setAccessibleName("Sample limit")
        self._available_points: list[int] = []
        self._sample_limit_choices = choices
        self._set_sample_limit_items([(limit, _limit_text(limit)) for limit in choices])
        self.sample_limit_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.sample_limit_combo.setFixedHeight(self.close_button.height() - 2)
        self.sample_limit_combo.adjustSize()
        self.sample_limit_combo.currentIndexChanged.connect(self._sample_limit_selected)
        self.header_controls.append(self.sample_limit_combo)
        self._update_sample_limit_tooltip(None)

    def update_sample_coverage(self, results) -> None:  # type: ignore[no-untyped-def]
        """Show which share of the samples in view each limit draws, after a load."""
        if not hasattr(self, "sample_limit_combo"):
            return
        counts = [count for count in (sample_counts(result) for result in results) if count is not None]
        self._available_points = [available for _, available in counts]
        shown = sum(count for count, _ in counts)
        available = sum(self._available_points)
        combo = self.sample_limit_combo
        if not counts:
            items = [(limit, _limit_text(limit)) for limit in self._sample_limit_choices]
        else:
            items = []
            complete_shown = False
            for limit in self._sample_limit_choices:
                # The selected limit reports what was drawn; others estimate what they would draw.
                drawn = shown if limit == self.sample_limit else estimated_shown(self._available_points, limit)
                complete = drawn >= available
                # Limits above the first that draws everything add nothing; the current one stays.
                if complete and complete_shown and limit != self.sample_limit:
                    continue
                complete_shown = complete_shown or complete
                items.append((limit, f"{coverage_percent_text(drawn, available)} \u00b7 {_limit_text(limit)}"))
        self._set_sample_limit_items(items)
        partial = bool(counts) and shown < available
        font = combo.font()
        font.setBold(partial)
        combo.setFont(font)
        self._update_sample_limit_tooltip((shown, available) if counts else None)
        combo.adjustSize()
        self._layout_header_controls()

    def _set_sample_limit_items(self, items) -> None:  # type: ignore[no-untyped-def]
        """Replace the selector's choices, keeping the current limit selected."""
        combo = self.sample_limit_combo
        combo.blockSignals(True)
        try:
            combo.clear()
            if self.sample_limit not in [limit for limit, _text in items]:
                # A limit saved in a workspace that the choices lack is still shown as itself.
                items = sorted([*items, (self.sample_limit, _limit_text(self.sample_limit))],
                               key=lambda item: (item[0] <= SAMPLE_LIMIT_ALL, item[0]))
            for limit, text in items:
                combo.addItem(text, limit)
            combo.setCurrentIndex(max(0, combo.findData(self.sample_limit)))
        finally:
            combo.blockSignals(False)

    def _update_sample_limit_tooltip(self, coverage) -> None:  # type: ignore[no-untyped-def]
        noun = self.sample_limit_noun
        if coverage is None:
            summary = f"Maximum samples drawn per {noun}."
        elif coverage[0] >= coverage[1]:
            summary = f"Drawing all {coverage[1]:,} samples in the current time range."
        else:
            summary = (f"Drawing {coverage[0]:,} of {coverage[1]:,} samples in the current time range "
                       f"({coverage_percent_text(*coverage)}). Every Nth sample is kept, so short peaks "
                       "can be missed; choose a higher limit, All, or a shorter time range for more detail.")
        self.sample_limit_combo.setToolTip(f"{summary}\nHigher limits draw more slowly.")

    def _sample_limit_selected(self, index: int) -> None:
        limit = self.sample_limit_combo.itemData(index)
        if limit is not None and limit != self.sample_limit:
            self.sample_limit = int(limit)
            self.on_sample_limit_changed()

    def on_sample_limit_changed(self) -> None:
        """Reload card data after the Sample limit selector changes."""
        self.on_series_changed()

    def add_series(self, h5_path: Path, series_name: str) -> None:
        self.insert_series(h5_path, series_name, len(self.series))

    def insert_series(self, h5_path: Path, series_name: str, index: int) -> None:
        """Insert a new source in both the plot and its legend order."""
        entry = (Path(h5_path), str(series_name))
        if entry not in self.series:
            self.series.insert(max(0, min(index, len(self.series))), entry)
            self.on_series_changed()

    def remove_series_at(self, index: int) -> None:
        """Remove one assigned series and close this card when it becomes empty."""
        if 0 <= index < len(self.series):
            del self.series[index]
            self.on_series_changed()
            if not self.series:
                self.close_requested.emit(self)

    def reorder_series(self, source: int, boundary: int) -> None:
        """Move a legend source to an insertion boundary without removing it."""
        if not 0 <= source < len(self.series):
            return
        order = list(range(len(self.series)))
        boundary = max(0, min(boundary, len(order)))
        order.pop(source)
        order.insert(boundary - int(boundary > source), source)
        if order != list(range(len(self.series))):
            self._apply_series_order(order)
            self.legend_changed.emit(self)

    def _apply_series_order(self, order: list[int]) -> None:
        self.series = [self.series[index] for index in order]
        self.on_series_changed()

    def on_series_changed(self) -> None:
        """Hook for concrete plot types to refresh their displayed data."""

    def workspace_state(self) -> dict:
        """What a workspace (.umw) file saves for this card; see `restore_workspace_state`.

        Card types extend both methods with their own colors, views and settings.
        """
        state: dict = {}
        if hasattr(self, "grouped_series"):
            state["groups"] = [{
                "axes": [None if axis is None else {**entry_state((axis.path, axis.name)), "component": axis.component}
                         for axis in group.axes],
                "color": group.color,
            } for group in self.grouped_series.groups]
        else:
            state["series"] = [entry_state(entry) for entry in self.series]
        if self.default_sample_limit is not None:
            state["sample_limit"] = self.sample_limit
        if self.supports_draw_style and self.draw_style != DRAW_AUTO:
            state["draw_style"] = self.draw_style
        if abs(self.time_offset) >= 0.0005:
            state["time_offset"] = self.time_offset
        representation = getattr(self, "time_axis_representation", None)
        if callable(representation):
            # Real date/time or time from log start, on cards with a time axis.
            state["time_axis_representation"] = representation()
        return state

    def restore_workspace_state(self, state: dict) -> None:
        """Show the saved sources, replacing those the card was created with."""
        if self.supports_time_offset and isinstance(state.get("time_offset"), (int, float)):
            self.set_time_offset(float(state["time_offset"]))
        set_representation = getattr(self, "set_time_axis_representation", None)
        if callable(set_representation) and isinstance(state.get("time_axis_representation"), str):
            set_representation(state["time_axis_representation"])
        if self.supports_draw_style:
            style = state.get("draw_style", DRAW_AUTO)
            self.set_draw_style(style if style in DRAW_STYLES else DRAW_AUTO)
        if self.default_sample_limit is not None and isinstance(state.get("sample_limit"), int):
            self.sample_limit = state["sample_limit"]
            self._set_sample_limit_items([(combo_limit, self.sample_limit_combo.itemText(index))
                                          for index in range(self.sample_limit_combo.count())
                                          if (combo_limit := self.sample_limit_combo.itemData(index)) is not None])
        if "groups" in state and hasattr(self, "grouped_series"):
            from dataclasses import replace
            from src.data.xyz import AxisSource
            from src.gui.plots.legend_groups import SeriesGroup
            groups = []
            for group in state["groups"]:
                axes = tuple(None if axis is None else AxisSource(*state_entry(axis), axis.get("component", "value"))
                             for axis in group["axes"])
                sources = tuple(dict.fromkeys(replace(axis, component="value") for axis in axes if axis is not None))
                groups.append(SeriesGroup(axes, str(group["color"]), sources))
            # Some cards keep a reference to this list, so replace its contents.
            self.grouped_series.groups[:] = groups
            self.grouped_series.changed.emit()
        elif "series" in state:
            self.series = [state_entry(entry) for entry in state["series"]]
            self.on_series_changed()

    def configure_legend_groups(self, roles, *, title="Graph", allow_time=False):
        from src.gui.plots.legend_groups import LegendGroups
        self.grouped_series = LegendGroups(self, roles, title=title, allow_time=allow_time)
        self.accepts_axis_drop = True
        self.accepts_time_axis_drop = allow_time
        self.grouped_series.changed.connect(self._legend_groups_changed)

    def _legend_groups_changed(self):
        self.series = self.grouped_series.entries()
        if not self.grouped_series.groups:
            self.close_requested.emit(self)
            return
        self.on_series_changed()
        self.legend_changed.emit(self)

    def replace_legend_series(self, key, entry, component="value"):
        from src.data.xyz import AxisSource
        self.grouped_series.replace(key, AxisSource(*entry, component) if entry else None)

    def swap_legend_series(self, source, target):
        self.grouped_series.swap(source, target)

    def add_legend_group(self, axes, index=None, color=None):
        self.grouped_series.insert(axes, index, color)

    def set_active(self, active: bool) -> None:
        self.setProperty("activePlot", bool(active))
        self._apply_active_style()
        if not active:
            # A card shows its controls only while it is the active card.
            self.set_header_controls_shown(False)

    def _apply_active_style(self) -> None:
        insertion_style = ""
        if hasattr(self, "_drop_after"):
            insertion_style = (
                "border-bottom: 4px solid palette(highlight);"
                if self._drop_after
                else "border-top: 4px solid palette(highlight);"
            )
        if bool(self.property("activePlot")):
            self.setStyleSheet(
                "QFrame#lodgazillaPlot { border: 2px solid palette(highlight); "
                f"background: palette(alternate-base); {insertion_style} }}"
            )
        else:
            self.setStyleSheet(
                # Keep the frame width equal to an active card. Otherwise
                # activating one plot shifts its plot area and X-axis by one
                # pixel relative to every inactive line plot.
                "QFrame#lodgazillaPlot { border: 2px solid palette(mid); "
                f"background: palette(base); {insertion_style} }}"
            )

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.activated.emit(self)
        super().mousePressEvent(event)

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.wheel_scrolled.emit(event.angleDelta().y())
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if (
            self._drag_start is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.position().toPoint() - self._drag_start).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self.start_plot_reorder_drag()
            self._drag_start = None
            return
        super().mouseMoveEvent(event)

    def start_plot_reorder_drag(self) -> None:
        mime_data = QMimeData()
        mime_data.setData(PLOT_MIME_TYPE, b"plot")
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        self.reorder_drag_started.emit(self)
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self.reorder_drag_finished.emit()

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if (self.accepts_series_drop and series_from_mime(event.mimeData()) is not None
                and getattr(event.source(), "source_plot", None) is not self
                or isinstance(event.source(), PlotBaseWidget)):
            if isinstance(event.source(), PlotBaseWidget) and event.source() is not self:
                self._drop_after = event.position().y() >= self.height() / 2
                self._apply_active_style()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if hasattr(self, "_drop_after"):
            del self._drop_after
            self._apply_active_style()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        series = series_from_mime(event.mimeData())
        if series is not None:
            # A legend drag is a move. Reject a drop back on its source card,
            # otherwise the source would remove the series without moving it.
            if not self.accepts_series_drop or getattr(event.source(), "source_plot", None) is self:
                event.ignore()
                return
            self.series_dropped.emit(self, *series)
            event.setDropAction(Qt.DropAction.MoveAction)
            event.acceptProposedAction()
        elif isinstance(event.source(), PlotBaseWidget) and event.source() is not self:
            self.plot_dropped.emit(event.source(), self, bool(getattr(self, "_drop_after", False)))
            if hasattr(self, "_drop_after"):
                del self._drop_after
                self._apply_active_style()
            event.acceptProposedAction()


class PlotHeightHandle(QFrame):
    """Bottom edge resizer, including for a lone plot card."""

    dragged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._last_y: int | None = None
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        # The cursor communicates the resize affordance without painting over
        # the plot card's active/inactive frame border.
        self.setStyleSheet("background: transparent;")

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_y = event.globalPosition().toPoint().y()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._last_y is not None and event.buttons() & Qt.MouseButton.LeftButton:
            current_y = event.globalPosition().toPoint().y()
            self.dragged.emit(current_y - self._last_y)
            self._last_y = current_y
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._last_y = None
        event.accept()


class PlotDragHandle(QFrame):
    """Full-width title strip that starts a parent plot-card reorder drag."""

    def __init__(self, plot: PlotBaseWidget) -> None:
        super().__init__(plot)
        self.plot = plot
        self._press_position: QPoint | None = None
        self.hovered = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def enterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        if self.hovered:
            # A faint highlight tint shows that the strip moves the card.
            tint = QColor(self.palette().color(QPalette.ColorRole.Highlight))
            tint.setAlpha(40)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(tint)
            painter.drawRoundedRect(QRectF(self.rect()), 3, 3)

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_position = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.plot.activated.emit(self.plot)
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if (
            self._press_position is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.position().toPoint() - self._press_position).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._press_position = None
            self.plot.start_plot_reorder_drag()
            return
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._press_position = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        event.accept()


def series_from_mime(mime_data: QMimeData) -> tuple[Path, str] | None:
    if not mime_data.hasFormat(SERIES_MIME_TYPE):
        return None
    try:
        payload = json.loads(bytes(mime_data.data(SERIES_MIME_TYPE)).decode("utf-8"))
        h5_path = Path(str(payload["h5_path"]))
        series_name = str(payload["series_name"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return (h5_path, series_name) if series_name else None
