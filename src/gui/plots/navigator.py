"""Qt-only overview and time-range navigator for the plot workspace."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QMenu, QToolButton, QVBoxLayout, QWidget,
)

from src.gui.plots.icons import plot_control_icon, plugin_icon
from src.gui.plots.markers import SELECTION_BRUSH, sync_marker_color, marker_color, marker_width


VIEW_EDGE_MODES = ("view_left", "view_right")
# A press inside the view box pans it once dragged; released in place it places the first marker.
VIEW_PAN_PENDING, VIEW_PAN = "view_pan_pending", "view_pan"
# Top of the first card bar; the strip above it inside the view box is its pan handle.
BARS_TOP = 9


@dataclass
class PlotNavigatorEntry:
    plot: object
    start: float
    stop: float
    source: str
    plot_type: str
    full_start: float | None = None
    full_stop: float | None = None
    # The card's display time offset, in seconds; the bar already includes it.
    offset: float = 0.0
    # Whether the card or any of its series is shown at a time offset.
    offset_set: bool = False

    @property
    def trimmed(self) -> bool:
        """Whether the card's range was narrowed from its full range."""
        full_start = self.start if self.full_start is None else self.full_start
        full_stop = self.stop if self.full_stop is None else self.full_stop
        return self.start > full_start + 1e-9 or self.stop < full_stop - 1e-9


class PlotNavigatorWidget(QWidget):
    """Compact, paint-based overview of plot cards and their time spans."""

    plot_activated = Signal(object)
    range_changed = Signal(object, float, float)
    # A card bar dragged sideways: the card and the seconds to add to its time offset.
    offset_dragged = Signal(object, float)
    offset_reset_requested = Signal(object)
    range_reset_requested = Signal(object)
    # The linked time cards' shared zoom, changed by dragging the view box's edges.
    view_range_changed = Signal(float, float)
    reorder_requested = Signal(object, object, bool)
    marker_placed = Signal(float)
    marker_cleared = Signal()
    second_marker_placed = Signal(float)
    second_marker_cleared = Signal()
    expanded_changed = Signal(bool)
    help_requested = Signal()
    close_all_requested = Signal()
    # A Plugins group icon clicked: the plugin's id.
    plugin_launch_requested = Signal(str)
    sample_loss_visibility_changed = Signal(bool)
    clipping_visibility_changed = Signal(bool)
    marker_info_display_changed = Signal(str)
    time_axis_display_changed = Signal(str)
    snap_to_nearest_changed = Signal(bool)
    marker_link_changed = Signal(bool)
    plot_views_link_changed = Signal(bool)
    legends_visibility_changed = Signal(bool)
    # The overview's width changed; its plot area margins are measured again from the cards.
    canvas_resized = Signal()

    HEADER_HEIGHT = 28
    # A half-bar-height gap keeps dense plot stacks compact while retaining a
    # clear individual hit target.
    ROW_HEIGHT = 11
    BAR_HEIGHT = 7
    EDGE_GRAB_PIXELS = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.entries: list[PlotNavigatorEntry] = []
        self.colors = {
            "line": "#2196f3", "multi": "#00acc1", "event": "#ff9800", "xy": "#9c27b0",
            "xyz": "#00897b",
            "gnss": "#20c56b", "video": "#e53935", "frequency": "#7c4dff", "interval": "#8d6e63",
            "other": "#9e9e9e",
        }
        self._expanded = True
        self._show_time_row = True
        self.icon_size = 25
        self._plot_left_margin = 26
        self._plot_right_margin = 26
        # The workspace's active card; its bar is highlighted and takes precedence over the view box.
        self._active_plot: object | None = None
        self._marker: float | None = None
        self._second_marker: float | None = None
        self._sync_marker: float | None = None
        # Visible time range of the linked time cards; None when views are unlinked.
        self._view_range: tuple[float, float] | None = None
        self._drag_mode: str | None = None
        self._drag_entry: PlotNavigatorEntry | None = None
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._drag_range: tuple[float, float] | None = None
        self._drop_index: int | None = None
        # While a bar is dragged sideways: its original span and the seconds moved so far.
        self._offset_drag: tuple[float, float, float, float] | None = None
        self._offset_delta = 0.0
        # Where the pointer is during a sideways drag; the change is shown beside it.
        self._offset_cursor: QPointF | None = None
        # The overview range at the start of a sideways drag, so the scale stays put.
        self._frozen_range: tuple[float, float] | None = None

        self.action_buttons: list[QToolButton] = []
        self._info_group_gaps: list[QWidget] = []
        self._toggle_icons: dict[QToolButton, tuple[str, str]] = {}
        self._toggle_tooltips: dict[QToolButton, tuple[str, str]] = {}
        self.header_widget = QWidget(self)
        self.header_widget.setFixedHeight(self.HEADER_HEIGHT)
        header = QHBoxLayout(self.header_widget)
        header.setContentsMargins(6, 0, 6, 0)
        # This invisible balance matches the right-edge Plugins group, Settings, Help and
        # Close all plots buttons and keeps the toolbar itself centered.
        self._header_balance = QWidget(self)
        header.addWidget(self._header_balance)
        header.addStretch(1)
        self.action_toolbar = QWidget(self)
        toolbar_layout = QHBoxLayout(self.action_toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(0)
        self._build_action_buttons(toolbar_layout)
        header.addWidget(self.action_toolbar)
        header.addStretch(1)
        # The Plugins group: one icon per enabled plugin with a launcher, then a separator.
        self.plugin_bar = QWidget(self)
        self._plugin_layout = QHBoxLayout(self.plugin_bar)
        self._plugin_layout.setContentsMargins(0, 0, 0, 0)
        self._plugin_layout.setSpacing(0)
        self.plugin_buttons: dict[str, QToolButton] = {}
        self._plugin_icons: dict[QToolButton, object] = {}
        self.plugin_bar.hide()
        header.addWidget(self.plugin_bar)
        header.addWidget(self.help_button)
        header.addWidget(self.close_button)
        self.canvas = _NavigatorCanvas(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header_widget)
        layout.addWidget(self.canvas)
        self.set_icon_size(self.icon_size)
        self._apply_expanded_state()

    def _new_action_button(self, tooltip: str, *, checkable: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setAutoRaise(True)
        button.setCheckable(checkable)
        button.setToolTip(tooltip)
        button.setFixedSize(self.icon_size, self.icon_size)
        button.setIconSize(QSize(max(12, round(self.icon_size * 0.8)), max(12, round(self.icon_size * 0.8))))
        self.action_buttons.append(button)
        return button

    def _add_toggle_button(
        self,
        layout: QHBoxLayout,
        checked_icon: str,
        unchecked_icon: str,
        checked_tooltip: str,
        unchecked_tooltip: str,
        *,
        checked: bool = False,
    ) -> QToolButton:
        button = self._new_action_button(unchecked_tooltip, checkable=True)
        # Match the info radio buttons: the unselected state is frameless and
        # only an enabled/selected state receives the standard button frame.
        button.setAutoRaise(True)
        self._toggle_icons[button] = (checked_icon, unchecked_icon)
        self._toggle_tooltips[button] = (checked_tooltip, unchecked_tooltip)
        button.setChecked(checked)
        button.toggled.connect(
            lambda is_checked, target=button: self._update_toggle_button(target, is_checked)
        )
        self._update_toggle_button(button, checked)
        layout.addWidget(button)
        return button

    def _update_toggle_button(self, button: QToolButton, checked: bool) -> None:
        checked_icon, unchecked_icon = self._toggle_icons[button]
        checked_tooltip, unchecked_tooltip = self._toggle_tooltips[button]
        button.setIcon(plot_control_icon(self, checked_icon if checked else unchecked_icon))
        button.setToolTip(checked_tooltip if checked else unchecked_tooltip)

    def _separator(self) -> QFrame:
        separator = QFrame(self)
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        separator.setFixedHeight(18)
        return separator

    def _info_group_gap(self) -> QWidget:
        gap = QWidget(self)
        gap.setFixedWidth(max(1, self.icon_size // 2))
        self._info_group_gaps.append(gap)
        return gap

    def _build_action_buttons(self, layout: QHBoxLayout) -> None:
        """Create the plot toolbar and emit actions for its wired controls."""
        # Left-to-right order is the reverse of the requested right-to-left
        # order, so expand/collapse remains the outermost right control.
        self._static_icons: dict[QToolButton, str] = {}
        time_axis_group = QButtonGroup(self)
        time_axis_group.setExclusive(True)
        self._time_axis_buttons: dict[str, QToolButton] = {}
        for mode, icon_name, tooltip in (
            ("all", "time-axis-all", "Show the time axis on every card"),
            ("bottom", "time-axis-bottom", "Show the time axis only on the bottom card"),
            ("none", "time-axis-none", "Hide the time axis on every card"),
        ):
            button = self._new_action_button(tooltip, checkable=True)
            self._static_icons[button] = icon_name
            button.setIcon(plot_control_icon(self, icon_name))
            button.setChecked(mode == "all")
            time_axis_group.addButton(button)
            self._time_axis_buttons[mode] = button
            button.toggled.connect(
                lambda checked, value=mode: self.time_axis_display_changed.emit(value) if checked else None
            )
            layout.addWidget(button)
        layout.addWidget(self._info_group_gap())
        layout.addWidget(self._separator())
        layout.addWidget(self._info_group_gap())
        self.marker_link_button = self._add_toggle_button(
            layout, "markers-linked", "markers-unlinked", "Markers linked", "Markers unlinked", checked=True
        )
        self.marker_link_button.toggled.connect(self.marker_link_changed.emit)
        self.snap_to_nearest_button = self._add_toggle_button(
            layout, "snap-nearest-timestamp", "free-position", "Snap to nearest timestamp", "Marker placement is free", checked=False
        )
        self.snap_to_nearest_button.toggled.connect(self.snap_to_nearest_changed.emit)
        self.plot_views_link_button = self._add_toggle_button(
            layout, "views-linked", "views-unlinked", "Plot views linked", "Plot views unlinked", checked=True
        )
        self.plot_views_link_button.toggled.connect(self.plot_views_link_changed.emit)

        layout.addWidget(self._info_group_gap())
        layout.addWidget(self._separator())
        layout.addWidget(self._info_group_gap())
        info_group = QButtonGroup(self)
        info_group.setExclusive(True)
        self._marker_info_buttons: dict[str, QToolButton] = {}
        for icon_name, tooltip, is_default in (
            ("info-clicked", "Show series values only in plot where placed", True),
            ("info-all", "Show series values in all plots", False),
            ("info-off", "Hide series values", False),
        ):
            button = self._new_action_button(tooltip, checkable=True)
            self._static_icons[button] = icon_name
            button.setIcon(plot_control_icon(self, icon_name))
            button.setChecked(is_default)
            info_group.addButton(button)
            mode = {"info-clicked": "active", "info-all": "all", "info-off": "off"}[icon_name]
            self._marker_info_buttons[mode] = button
            button.toggled.connect(
                lambda checked, value=mode: self.marker_info_display_changed.emit(value) if checked else None
            )
            layout.addWidget(button)
        layout.addWidget(self._info_group_gap())
        layout.addWidget(self._separator())
        layout.addWidget(self._info_group_gap())

        self.sample_loss_button = self._add_toggle_button(
            layout, "show-sample-loss", "hide-sample-loss", "Sample-loss areas shown", "Sample-loss areas hidden", checked=True
        )
        self.sample_loss_button.toggled.connect(self.sample_loss_visibility_changed.emit)
        self.clipping_button = self._add_toggle_button(
            layout, "show-clipping", "hide-clipping", "Clipping areas shown", "Clipping areas hidden", checked=True
        )
        self.clipping_button.toggled.connect(self.clipping_visibility_changed.emit)
        layout.addWidget(self._info_group_gap())
        layout.addWidget(self._separator())
        layout.addWidget(self._info_group_gap())
        self.legends_button = self._add_toggle_button(
            layout, "legend-shown", "legend-hidden", "Legends shown", "Legends hidden", checked=True
        )
        self.legends_button.toggled.connect(self.legends_visibility_changed.emit)
        layout.addWidget(self._info_group_gap())
        layout.addWidget(self._separator())
        layout.addWidget(self._info_group_gap())
        # Shows or hides the overview below the toolbar; the toolbar itself stays.
        self.navigator_button = self._add_toggle_button(
            layout, "plot-navigator-shown", "plot-navigator-hidden", "Plot navigator shown", "Plot navigator hidden",
            checked=True,
        )
        self.navigator_button.toggled.connect(self.set_expanded)
        self.help_button = self._new_action_button("About LODGaZilla")
        self._static_icons[self.help_button] = "help"
        self.help_button.setIcon(plot_control_icon(self, "help"))
        self.help_button.clicked.connect(self.help_requested.emit)
        self.close_button = self._new_action_button("Close all plots")
        self._static_icons[self.close_button] = "close-all-plots"
        self.close_button.setIcon(plot_control_icon(self, "close-all-plots"))
        self.close_button.clicked.connect(self.close_all_requested.emit)

    def set_icon_size(self, size: int) -> None:
        self.icon_size = max(16, min(48, int(size)))
        for button in self.action_buttons:
            button.setFixedSize(self.icon_size, self.icon_size)
            edge = max(12, round(self.icon_size * 0.8))
            button.setIconSize(QSize(edge, edge))
        for gap in self._info_group_gaps:
            gap.setFixedWidth(max(1, self.icon_size // 2))
        self.action_toolbar.adjustSize()
        self._update_header_balance()
        self.updateGeometry()

    def _update_header_balance(self) -> None:
        plugins = self.plugin_bar.sizeHint().width() if self.plugin_buttons else 0
        self._header_balance.setFixedWidth(3 * self.icon_size + plugins)

    def set_plugin_launchers(self, launchers: list[tuple[str, str, object]]) -> None:
        """Show one icon per (plugin id, title, SVG path or None); hide the group when there are none."""
        for button in self.plugin_buttons.values():
            self.action_buttons.remove(button)
            self._plugin_icons.pop(button, None)
        while self._plugin_layout.count():
            widget = self._plugin_layout.takeAt(0).widget()
            if widget is not None:
                if widget in self._info_group_gaps:
                    self._info_group_gaps.remove(widget)
                widget.deleteLater()
        self.plugin_buttons = {}
        for plugin_id, title, icon_path in launchers:
            button = self._new_action_button(title)
            self._plugin_icons[button] = icon_path
            button.setIcon(plugin_icon(self, icon_path))
            button.clicked.connect(lambda _checked=False, value=plugin_id: self.plugin_launch_requested.emit(value))
            self._plugin_layout.addWidget(button)
            self.plugin_buttons[plugin_id] = button
        if launchers:
            self._plugin_layout.addWidget(self._info_group_gap())
            self._plugin_layout.addWidget(self._separator())
            self._plugin_layout.addWidget(self._info_group_gap())
        self.plugin_bar.setVisible(bool(launchers))
        self.set_icon_size(self.icon_size)

    def set_diagnostic_area_visibility(self, show_sample_loss: bool, show_clipping: bool) -> None:
        """Synchronize the toolbar state without feeding settings changes back."""
        for button, visible in (
            (self.sample_loss_button, show_sample_loss),
            (self.clipping_button, show_clipping),
        ):
            was_blocked = button.blockSignals(True)
            button.setChecked(bool(visible))
            button.blockSignals(was_blocked)
            self._refresh_action_icons()

    def set_legends_shown(self, shown: bool) -> None:
        self._set_toggle_quietly(self.legends_button, shown)

    def _set_toggle_quietly(self, button: QToolButton, checked: bool) -> None:
        """Show a saved state without emitting it back as a change."""
        was_blocked = button.blockSignals(True)
        button.setChecked(bool(checked))
        button.blockSignals(was_blocked)
        self._update_toggle_button(button, bool(checked))

    def set_time_axis_display(self, mode: str) -> None:
        button = self._time_axis_buttons.get(mode, self._time_axis_buttons["all"])
        was_blocked = button.blockSignals(True)
        button.setChecked(True)
        button.blockSignals(was_blocked)

    def set_marker_info_display(self, mode: str) -> None:
        button = self._marker_info_buttons.get(mode, self._marker_info_buttons["active"])
        was_blocked = button.blockSignals(True)
        button.setChecked(True)
        button.blockSignals(was_blocked)

    def set_snap_to_nearest(self, enabled: bool) -> None:
        was_blocked = self.snap_to_nearest_button.blockSignals(True)
        self.snap_to_nearest_button.setChecked(bool(enabled))
        self.snap_to_nearest_button.blockSignals(was_blocked)
        self._refresh_action_icons()

    def set_markers_linked(self, enabled: bool) -> None:
        was_blocked = self.marker_link_button.blockSignals(True)
        self.marker_link_button.setChecked(bool(enabled))
        self.marker_link_button.blockSignals(was_blocked)
        self._refresh_action_icons()

    def set_plot_views_linked(self, enabled: bool) -> None:
        was_blocked = self.plot_views_link_button.blockSignals(True)
        self.plot_views_link_button.setChecked(bool(enabled))
        self.plot_views_link_button.blockSignals(was_blocked)
        self._refresh_action_icons()

    def _refresh_action_icons(self) -> None:
        for button in self._toggle_icons:
            self._update_toggle_button(button, button.isChecked())
        for button, icon_name in getattr(self, "_static_icons", {}).items():
            button.setIcon(plot_control_icon(self, icon_name))
        for button, icon_path in getattr(self, "_plugin_icons", {}).items():
            button.setIcon(plugin_icon(self, icon_path))

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in {QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange}:
            # Do not change the navigator geometry while Qt is propagating a
            # palette update.  That can re-enter layout/paint processing.
            self._refresh_action_icons()

    def set_entries(self, entries: list[PlotNavigatorEntry]) -> None:
        self.entries = [entry for entry in entries if entry.stop >= entry.start]
        self._update_height()
        self.canvas.update()

    @property
    def _active_entry(self) -> PlotNavigatorEntry | None:
        return next((entry for entry in self.entries if entry.plot is self._active_plot), None)

    def set_active_plot(self, plot: object | None) -> None:
        """Highlight the active card's bar; None when no card is active."""
        if plot is not self._active_plot:
            self._active_plot = plot
            self.canvas.update()

    def set_colors(self, colors: dict[str, str]) -> None:
        self.colors.update({key: value for key, value in colors.items() if value})
        self.canvas.update()

    def set_marker(self, timestamp: float | None) -> None:
        self._marker = None if timestamp is None else float(timestamp)
        self.canvas.update()

    def set_sync_marker(self, timestamp: float | None) -> None:
        self._sync_marker = None if timestamp is None else float(timestamp)
        self.canvas.update()

    def set_second_marker(self, timestamp: float | None) -> None:
        self._second_marker = None if timestamp is None else float(timestamp)
        self.canvas.update()

    def set_view_range(self, time_range: tuple[float, float] | None) -> None:
        """Show the linked cards' visible time range as the view box, or hide it."""
        if time_range is not None:
            start, stop = sorted(float(value) for value in time_range)
            time_range = (start, stop) if stop > start else None
        self._view_range = time_range
        self.canvas.update()

    def set_show_time_row(self, show: bool) -> None:
        self._show_time_row = bool(show)
        self._update_height()
        self.canvas.update()

    def set_plot_area_margins(self, left: int, right: int) -> None:
        """Align navigator bars with the current PyQtGraph ViewBox edges."""
        left = max(0, int(left))
        right = max(0, int(right))
        if (left, right) == (self._plot_left_margin, self._plot_right_margin):
            return
        self._plot_left_margin, self._plot_right_margin = left, right
        self.canvas.update()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self._set_toggle_quietly(self.navigator_button, self._expanded)
        self._apply_expanded_state()
        self.expanded_changed.emit(self._expanded)

    def _apply_expanded_state(self) -> None:
        self.canvas.setVisible(self._expanded)
        self._update_height()

    def _update_height(self) -> None:
        canvas_height = 0 if not self._expanded else self.canvas.required_height()
        self.canvas.setFixedHeight(canvas_height)
        self.setFixedHeight(self.HEADER_HEIGHT + canvas_height)

    def global_range(self) -> tuple[float, float] | None:
        if self._frozen_range is not None:
            return self._frozen_range
        if not self.entries:
            return None
        return (
            min(entry.full_start if entry.full_start is not None else entry.start for entry in self.entries),
            max(entry.full_stop if entry.full_stop is not None else entry.stop for entry in self.entries),
        )

    def time_for_x(self, x: float) -> float | None:
        time_range = self.global_range()
        if time_range is None:
            return None
        left, right = self.canvas.margins()
        width = max(1, self.canvas.width() - left - right)
        ratio = max(0.0, min(1.0, (x - left) / width))
        return time_range[0] + ratio * (time_range[1] - time_range[0] or 1.0)

    def x_for_time(self, timestamp: float) -> float:
        time_range = self.global_range()
        left, right = self.canvas.margins()
        if time_range is None or time_range[1] <= time_range[0]:
            return float(left)
        ratio = (timestamp - time_range[0]) / (time_range[1] - time_range[0])
        return left + max(0.0, min(1.0, ratio)) * max(1, self.canvas.width() - left - right)


class _NavigatorCanvas(QWidget):
    def __init__(self, navigator: PlotNavigatorWidget) -> None:
        super().__init__(navigator)
        self.navigator = navigator
        self.setMouseTracking(True)

    def margins(self) -> tuple[int, int]:
        return self.navigator._plot_left_margin, self.navigator._plot_right_margin

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self.navigator.canvas_resized.emit()

    def bars_bottom_y(self) -> int:
        return 9 + max(0, len(self.navigator.entries) - 1) * self.navigator.ROW_HEIGHT + self.navigator.BAR_HEIGHT

    def required_height(self) -> int:
        # 9 px top + bars + 5 px bottom when timestamps are hidden.  The
        # optional text row needs one font height plus a small baseline gap.
        bars_bottom = self.bars_bottom_y()
        if not self.navigator._show_time_row:
            return max(24, bars_bottom + 5)
        return max(43, bars_bottom + self.fontMetrics().height() + 12)

    def _stroke_rect(self, index: int, entry: PlotNavigatorEntry):
        from PySide6.QtCore import QRectF
        y = 9 + index * self.navigator.ROW_HEIGHT
        x1 = self.navigator.x_for_time(entry.full_start if entry.full_start is not None else entry.start)
        x2 = self.navigator.x_for_time(entry.full_stop if entry.full_stop is not None else entry.stop)
        return QRectF(x1, y, max(2.0, x2 - x1), self.navigator.BAR_HEIGHT)

    def _fill_rect(self, index: int, entry: PlotNavigatorEntry):
        from PySide6.QtCore import QRectF
        y = 9 + index * self.navigator.ROW_HEIGHT
        x1, x2 = self.navigator.x_for_time(entry.start), self.navigator.x_for_time(entry.stop)
        return QRectF(x1, y, max(2.0, x2 - x1), self.navigator.BAR_HEIGHT)

    def _view_box_edges(self) -> tuple[float, float] | None:
        """The view box's left and right x, clamped to the overview."""
        view_range = self.navigator._view_range
        if view_range is None or self.navigator.global_range() is None:
            return None
        return self.navigator.x_for_time(view_range[0]), self.navigator.x_for_time(view_range[1])

    def _on_active_bar(self, position) -> bool:  # type: ignore[no-untyped-def]
        """Whether the position is on the active card's bar, which takes precedence over the view box."""
        active = self.navigator._active_entry
        if active is None:
            return False
        index = self.navigator.entries.index(active)
        return self._stroke_rect(index, active).adjusted(-2, -4, 2, 4).contains(position)

    def _view_edge_at(self, position) -> str | None:  # type: ignore[no-untyped-def]
        """The view box edge within grabbing distance; it takes precedence over bar edges
        other than the active card's."""
        edges = self._view_box_edges()
        if edges is None or not 0 <= position.y() <= self.bars_bottom_y() + 3 or self._on_active_bar(position):
            return None
        distances = [abs(position.x() - edge) for edge in edges]
        nearest = min(range(2), key=distances.__getitem__)
        if distances[nearest] > self.navigator.EDGE_GRAB_PIXELS:
            return None
        return VIEW_EDGE_MODES[nearest]

    def _view_pan_at(self, position) -> bool:  # type: ignore[no-untyped-def]
        """Whether a press here drags the view box: its top strip, or empty space inside it."""
        edges = self._view_box_edges()
        if edges is None or not edges[0] < position.x() < edges[1] or position.y() > self.bars_bottom_y() + 3:
            return False
        return position.y() < BARS_TOP or self._entry_at(position) is None

    def _entry_at(self, position) -> tuple[int, PlotNavigatorEntry, object] | None:  # type: ignore[no-untyped-def]
        # Neighbouring rows' grab areas overlap slightly; the active card's bar wins.
        active = self.navigator._active_entry
        if active is not None and self._on_active_bar(position):
            index = self.navigator.entries.index(active)
            return index, active, self._stroke_rect(index, active).adjusted(-2, -4, 2, 4)
        for index, entry in enumerate(self.navigator.entries):
            rect = self._stroke_rect(index, entry).adjusted(-2, -4, 2, 4)
            if rect.contains(position):
                return index, entry, rect
        return None

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.color(self.backgroundRole()))
        time_range = self.navigator.global_range()
        if time_range is None:
            painter.setPen(palette.color(self.foregroundRole()))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No time-based plots")
            return
        left, right = self.margins()
        bottom_y = self.bars_bottom_y() + 3
        line_pen = QPen(palette.color(self.foregroundRole()), 1)
        painter.setPen(line_pen)
        painter.drawLine(left - 5, 4, left - 5, bottom_y)
        painter.drawLine(self.width() - right + 5, 4, self.width() - right + 5, bottom_y)
        for index, entry in enumerate(self.navigator.entries):
            stroke_rect = self._stroke_rect(index, entry)
            fill_rect = self._fill_rect(index, entry)
            color = QColor(self.navigator.colors.get(entry.plot_type, self.navigator.colors["other"]))
            path = QPainterPath()
            path.addRoundedRect(fill_rect, 2, 2)
            painter.fillPath(path, color)
            stroke_path = QPainterPath()
            stroke_path.addRoundedRect(stroke_rect, 2, 2)
            painter.setPen(QPen(
                palette.color(palette.ColorRole.Highlight) if entry is self.navigator._active_entry else color,
                2 if entry is self.navigator._active_entry else 1,
            ))
            painter.drawPath(stroke_path)
            if self.navigator._drop_index == index:
                painter.setPen(QPen(palette.color(palette.ColorRole.Highlight), 2))
                painter.drawLine(left, stroke_rect.top() - 4, self.width() - right, stroke_rect.top() - 4)
        if self.navigator._drop_index == len(self.navigator.entries):
            painter.setPen(QPen(palette.color(palette.ColorRole.Highlight), 2))
            painter.drawLine(left, bottom_y - 2, self.width() - right, bottom_y - 2)
        view_edges = self._view_box_edges()
        if view_edges is not None:
            # The linked cards' visible range, framing every row; its shaded top strip is the pan handle.
            view_left, view_right = view_edges
            handle = QColor(palette.color(palette.ColorRole.Highlight))
            handle.setAlpha(110)
            painter.fillRect(QRectF(view_left, 2, max(2.0, view_right - view_left), BARS_TOP - 4), handle)
            painter.setPen(QPen(palette.color(palette.ColorRole.Highlight), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(view_left, 2, max(2.0, view_right - view_left), bottom_y - 1), 2, 2)
        markers = (self.navigator._marker, self.navigator._second_marker)
        if None not in markers:
            # x_for_time clamps, so a selection partly outside the range still shows its visible part.
            left_x, right_x = sorted(self.navigator.x_for_time(value) for value in markers)
            painter.fillRect(QRectF(left_x, 2, max(1.0, right_x - left_x), bottom_y - 2), QColor(*SELECTION_BRUSH))
        sync = self.navigator._sync_marker
        if sync is not None and time_range[0] <= sync <= time_range[1]:
            sync_x = int(self.navigator.x_for_time(sync))
            painter.setPen(QPen(QColor(sync_marker_color()), 2, Qt.PenStyle.DashLine))
            painter.drawLine(sync_x, 2, sync_x, bottom_y)
        for index, value in enumerate(markers):
            if value is not None and time_range[0] <= value <= time_range[1]:
                marker_x = self.navigator.x_for_time(value)
                painter.setPen(QPen(marker_color(index), marker_width()))
                painter.drawLine(int(marker_x), 2, int(marker_x), bottom_y)
        if self.navigator._show_time_row:
            painter.setPen(palette.color(self.foregroundRole()))
            font_metrics = painter.fontMetrics()
            start_text = format_datetime(time_range[0])
            stop_text = format_datetime(time_range[1])
            duration_text = format_duration(time_range[1] - time_range[0])
            text_y = bottom_y + font_metrics.height() + 5
            painter.drawText(left - 5, text_y, start_text)
            painter.drawText(self.width() - right + 5 - font_metrics.horizontalAdvance(stop_text), text_y, stop_text)
            painter.drawText((self.width() - font_metrics.horizontalAdvance(duration_text)) // 2, text_y, duration_text)
        if self.navigator._drag_mode == "offset" and self.navigator._offset_cursor is not None:
            self._paint_offset_delta(painter)

    def offset_delta_text(self) -> str:
        return f"Δ {self.navigator._offset_delta:+.3f} s"

    def _paint_offset_delta(self, painter: QPainter) -> None:
        """The time offset change so far, in a small label beside the pointer."""
        palette = self.palette()
        text = self.offset_delta_text()
        metrics = painter.fontMetrics()
        width, height = metrics.horizontalAdvance(text) + 10, metrics.height() + 4
        cursor = self.navigator._offset_cursor
        # Right of and above the pointer, kept inside the overview.
        x = cursor.x() + 14
        if x + width > self.width() - 2:
            x = cursor.x() - 14 - width
        y = min(max(2.0, cursor.y() - height - 4), max(2.0, self.height() - height - 2))
        rect = QRectF(max(2.0, x), y, width, height)
        painter.setPen(QPen(palette.color(palette.ColorRole.Highlight), 1))
        painter.setBrush(palette.color(palette.ColorRole.ToolTipBase))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(palette.color(palette.ColorRole.ToolTipText))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        navigator = self.navigator
        if navigator._drag_mode in (VIEW_PAN_PENDING, VIEW_PAN) and navigator._drag_range is not None:
            moved = event.position().x() - navigator._drag_start_x
            if navigator._drag_mode == VIEW_PAN_PENDING and abs(moved) < QApplication.startDragDistance():
                return
            navigator._drag_mode = VIEW_PAN
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            time_range = navigator.global_range()
            left, right = self.margins()
            if time_range is not None:
                start, stop = navigator._drag_range
                offset = moved * (time_range[1] - time_range[0]) / max(1, self.width() - left - right)
                # Keep the box within the overview, or within its own range when that is wider.
                offset = max(min(time_range[0], start) - start, min(max(time_range[1], stop) - stop, offset))
                if (start + offset, stop + offset) != navigator._view_range:
                    navigator._view_range = (start + offset, stop + offset)
                    navigator.view_range_changed.emit(start + offset, stop + offset)
                    self.update()
            return
        if navigator._drag_mode in VIEW_EDGE_MODES and navigator._view_range is not None:
            value = navigator.time_for_x(event.position().x())
            if value is not None:
                start, stop = navigator._view_range
                minimum = max(1e-6, (stop - start) * 1e-6)
                if navigator._drag_mode == "view_left":
                    start = min(value, stop - minimum)
                else:
                    stop = max(value, start + minimum)
                if (start, stop) != navigator._view_range:
                    navigator._view_range = (start, stop)
                    navigator.view_range_changed.emit(start, stop)
                    self.update()
            return
        view_edge = self._view_edge_at(event.position()) if navigator._drag_mode is None else None
        if view_edge is not None:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            self.setToolTip("Visible range")
            return
        if navigator._drag_mode is None and self._view_pan_at(event.position()):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.setToolTip("Visible range")
            return
        entry_data = self._entry_at(event.position())
        if navigator._drag_mode in {"left", "right"} and navigator._drag_entry is not None:
            value = navigator.time_for_x(event.position().x())
            if value is not None and navigator._drag_range is not None:
                start, stop = navigator._drag_range
                full_start = navigator._drag_entry.full_start if navigator._drag_entry.full_start is not None else start
                full_stop = navigator._drag_entry.full_stop if navigator._drag_entry.full_stop is not None else stop
                if navigator._drag_mode == "left":
                    navigator._drag_entry.start = max(full_start, min(value, stop - 0.001))
                else:
                    navigator._drag_entry.stop = min(full_stop, max(value, start + 0.001))
                self.update()
            return
        if navigator._drag_mode == "move" and navigator._drag_entry is not None:
            # The first clear movement decides: sideways sets the time offset, up or down reorders.
            dx = event.position().x() - navigator._drag_start_x
            dy = event.position().y() - navigator._drag_start_y
            if max(abs(dx), abs(dy)) < QApplication.startDragDistance():
                return
            if abs(dx) > abs(dy):
                self._begin_offset_drag()
            else:
                navigator._drag_mode = "reorder"
        if navigator._drag_mode == "offset" and navigator._drag_entry is not None:
            self._move_offset_drag(event.position())
            return
        if navigator._drag_mode == "reorder" and navigator._drag_entry is not None:
            index = max(0, min(len(navigator.entries), int((event.position().y() - 5) / navigator.ROW_HEIGHT)))
            navigator._drop_index = index
            self.update()
            return
        if entry_data is not None:
            _index, entry, rect = entry_data
            fill_rect = self._fill_rect(_index, entry)
            if abs(event.position().x() - fill_rect.left()) <= navigator.EDGE_GRAB_PIXELS or abs(event.position().x() - fill_rect.right()) <= navigator.EDGE_GRAB_PIXELS:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(entry.source + (f"\nTime offset: {entry.offset:+.3f} s" if abs(entry.offset) >= 0.0005 else ""))
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.setToolTip("")

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        navigator = self.navigator
        if event.button() == Qt.MouseButton.RightButton:
            entry_data = self._entry_at(event.position())
            if entry_data is not None:
                self._show_entry_menu(entry_data[1], event.globalPosition().toPoint())
                return
            value = navigator.time_for_x(event.position().x())
            if value is not None:
                navigator.second_marker_placed.emit(value)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        view_edge = self._view_edge_at(event.position())
        if view_edge is not None:
            navigator._drag_mode = view_edge
            return
        navigator._drag_start_x = int(event.position().x())
        navigator._drag_start_y = int(event.position().y())
        if self._view_pan_at(event.position()):
            navigator._drag_mode = VIEW_PAN_PENDING
            navigator._drag_range = navigator._view_range
            return
        entry_data = self._entry_at(event.position())
        if entry_data is None:
            value = navigator.time_for_x(event.position().x())
            if value is not None:
                navigator.marker_placed.emit(value)
            return
        index, entry, _rect = entry_data
        navigator.set_active_plot(entry.plot)
        navigator.plot_activated.emit(entry.plot)
        navigator._drag_entry = entry
        navigator._drag_range = (entry.start, entry.stop)
        fill_rect = self._fill_rect(index, entry)
        if abs(event.position().x() - fill_rect.left()) <= navigator.EDGE_GRAB_PIXELS:
            navigator._drag_mode = "left"
        elif abs(event.position().x() - fill_rect.right()) <= navigator.EDGE_GRAB_PIXELS:
            navigator._drag_mode = "right"
        else:
            navigator._drag_mode = "move"
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        navigator = self.navigator
        entry = navigator._drag_entry
        mode = navigator._drag_mode
        if mode == VIEW_PAN_PENDING:
            # Pressed and released in place: an ordinary click that places the first marker.
            value = navigator.time_for_x(navigator._drag_start_x)
            if value is not None:
                navigator.marker_placed.emit(value)
        if mode == VIEW_PAN:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        if entry is not None and mode in {"left", "right"}:
            navigator.range_changed.emit(entry.plot, entry.start, entry.stop)
        elif entry is not None and mode == "offset":
            delta = navigator._offset_delta
            navigator._offset_drag = None
            navigator._offset_delta = 0.0
            navigator._offset_cursor = None
            navigator._frozen_range = None
            if abs(delta) >= 0.0005:
                navigator.offset_dragged.emit(entry.plot, delta)
        elif entry is not None and mode == "reorder" and navigator._drop_index is not None and entry in navigator.entries:
            # The insertion line for drop index d is drawn above row d (or
            # below the last row). Indexes d and d + 1 around the card's own
            # row leave it in place; a downward move goes after row d - 1.
            drop_index = min(len(navigator.entries), navigator._drop_index)
            own_index = navigator.entries.index(entry)
            if drop_index < own_index:
                navigator.reorder_requested.emit(entry.plot, navigator.entries[drop_index].plot, False)
            elif drop_index > own_index + 1:
                navigator.reorder_requested.emit(entry.plot, navigator.entries[drop_index - 1].plot, True)
        navigator._drag_mode = None
        navigator._drag_entry = None
        navigator._drag_range = None
        navigator._drop_index = None
        self.update()
        super().mouseReleaseEvent(event)

    def _begin_offset_drag(self) -> None:
        navigator = self.navigator
        entry = navigator._drag_entry
        navigator._frozen_range = navigator.global_range()
        navigator._drag_mode = "offset"
        navigator._offset_delta = 0.0
        navigator._offset_drag = (
            entry.start, entry.stop,
            entry.start if entry.full_start is None else entry.full_start,
            entry.stop if entry.full_stop is None else entry.full_stop,
        )
        self.setCursor(Qt.CursorShape.SizeHorCursor)

    def _move_offset_drag(self, position) -> None:  # type: ignore[no-untyped-def]
        """Preview the card's bar at its new time; the card moves when the drag ends."""
        navigator = self.navigator
        x = position.x()
        navigator._offset_cursor = QPointF(position)
        time_range = navigator.global_range()
        if time_range is None or navigator._offset_drag is None:
            return
        left, right = self.margins()
        seconds_per_pixel = (time_range[1] - time_range[0]) / max(1, self.width() - left - right)
        delta = (x - navigator._drag_start_x) * seconds_per_pixel
        entry = navigator._drag_entry
        start, stop, full_start, full_stop = navigator._offset_drag
        entry.start, entry.stop, entry.full_start, entry.full_stop = (
            start + delta, stop + delta, full_start + delta, full_stop + delta)
        navigator._offset_delta = delta
        self.update()

    def _show_entry_menu(self, entry: PlotNavigatorEntry, position) -> None:  # type: ignore[no-untyped-def]
        menu = QMenu(self)
        reset_offset = menu.addAction("Reset offset")
        reset_offset.setEnabled(entry.offset_set or abs(entry.offset) >= 0.0005 or entry.plot_type == "video")
        reset_range = menu.addAction("Reset selection")
        reset_range.setEnabled(entry.trimmed)
        chosen = menu.exec(position)
        if chosen is reset_offset:
            self.navigator.offset_reset_requested.emit(entry.plot)
        elif chosen is reset_range:
            self.navigator.range_reset_requested.emit(entry.plot)
        # Otherwise each right-click would leave a menu alive for as long as the Plot navigator.
        menu.deleteLater()

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._entry_at(event.position()) is not None or self._view_edge_at(event.position()) is not None:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.navigator.marker_cleared.emit()
        elif event.button() == Qt.MouseButton.RightButton:
            self.navigator.second_marker_cleared.emit()


def format_datetime(timestamp: float) -> str:
    if timestamp < 1_000_000_000:
        return f"{timestamp:.3f} s"
    return datetime.fromtimestamp(timestamp).astimezone().strftime("%d.%m.%Y %H:%M:%S")


def format_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds >= 86_400:
        days, remainder = divmod(int(seconds), 86_400)
        hours = remainder // 3_600
        return f"{days} d {hours} h" if hours else f"{days} d"
    if seconds >= 3_600:
        hours, remainder = divmod(int(seconds), 3_600)
        minutes = remainder // 60
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    if seconds >= 60:
        return f"{int(seconds // 60)} min"
    return f"{seconds:.1f} s"
