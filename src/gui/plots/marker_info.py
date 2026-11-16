"""Shared movable, copyable marker-value popup for all plot types."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import math

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtWidgets import QLabel, QTextBrowser

from src.gui.plots.theme import palette_popup_stylesheet


@dataclass(frozen=True)
class MarkerInfoEntry:
    name: str
    color: str
    value: str
    details: tuple[str, ...]
    # The series' whole name, such as "run1/Speed", for the values box details; empty
    # when the box's series names it (see `value_boxes.full_series_name`).
    full_name: str = ""


class MarkerInfoBox(QTextBrowser):
    """Expandable per-series marker data, with ordinary text selection/copy."""

    HEADER_HEIGHT = 22

    def __init__(self, parent=None, *, title: str = "Values") -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._entries: list[MarkerInfoEntry] = []
        self._expanded: set[int] = set()
        self._selection_duration: float | None = None
        self._drag_origin: QPoint | None = None
        self._drag_box_origin: QPoint | None = None
        self.header = QLabel(title, self)
        # Deliberately visible while the drag behavior is being refined: this
        # is the only row from which the floating box can be moved.
        self.header.setStyleSheet(
            "font-weight: bold; padding: 2px 5px; "
            "background: rgba(0, 120, 215, 90); border-bottom: 1px solid rgba(255, 255, 255, 100);"
        )
        self.header.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setViewportMargins(0, self.HEADER_HEIGHT, 0, 0)
        self.header.installEventFilter(self)
        self.viewport().installEventFilter(self)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(palette_popup_stylesheet())
        self.hide()

    def set_entries(self, entries: list[MarkerInfoEntry]) -> None:
        self._entries = entries
        # Compact values are the default; users expand only the series whose
        # full time/original-value details they need.
        self._expanded = set()
        self._render()

    def _toggle_entry(self, url) -> None:  # type: ignore[no-untyped-def]
        try:
            index = int(url.toString().removeprefix("toggle:"))
        except ValueError:
            return
        if index in self._expanded:
            self._expanded.remove(index)
        else:
            self._expanded.add(index)
        self._render()

    def set_selection_duration(self, seconds: float | None) -> None:
        self._selection_duration = seconds if seconds is not None and math.isfinite(seconds) else None
        self._render()

    def _render(self) -> None:
        sections: list[str] = []
        if self._selection_duration is not None:
            sections.append(f"<b>Selection Δt: {format_duration(self._selection_duration)}</b>")
        for index, entry in enumerate(self._entries):
            arrow = "▾" if index in self._expanded else "▸"
            title = (
                f'<a href="toggle:{index}" style="color:{entry.color}; text-decoration:none">'
                f"<b>{arrow} {escape(entry.name)}: {escape(entry.value)}</b></a>"
            )
            details = (
                "<br>".join(escape(detail).replace("\n", "<br>") for detail in entry.details)
                if index in self._expanded else ""
            )
            sections.append(title + (f"<br>{details}" if details else ""))
        # Keep the compact header and all series rows adjacent; expanded
        # details already contain their own line breaks.
        self.setHtml("<br>".join(sections))
        self._fit()

    def _fit(self) -> None:
        self.document().adjustSize()
        size = self.document().size()
        self.setFixedSize(
            max(230, min(500, int(size.width()) + 20)),
            max(self.HEADER_HEIGHT + 20, min(480, int(size.height()) + 14 + self.HEADER_HEIGHT)),
        )
        self.header.setGeometry(0, 0, self.width(), self.HEADER_HEIGHT)
        self.header.raise_()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self.header.setGeometry(0, 0, self.width(), self.HEADER_HEIGHT)
        self.header.raise_()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        # QTextBrowser may receive a palette event from its base constructor
        # before this subclass has created the movable header.
        if watched is getattr(self, "header", None):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_origin = event.globalPosition().toPoint()
                self._drag_box_origin = self.pos()
                return True
            if event.type() == QEvent.Type.MouseMove and self._drag_origin is not None:
                if event.buttons() & Qt.MouseButton.LeftButton:
                    self.move(self._drag_box_origin + event.globalPosition().toPoint() - self._drag_origin)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_origin = self._drag_box_origin = None
                return True
        if watched is self.viewport():
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                anchor = self.anchorAt(event.position().toPoint())
                if anchor:
                    # Consume title clicks before QTextBrowser starts a text
                    # selection; this makes every row toggle reliably.
                    self._toggle_entry(_MarkerUrl(anchor))
                    return True
        return super().eventFilter(watched, event)


def format_duration(seconds: float) -> str:
    """Format a duration in s, ms, µs or ns, whichever reads naturally."""
    seconds = abs(seconds)
    scale, unit = (1, "s")
    if 0 < seconds < 1e-6:
        scale, unit = (1e9, "ns")
    elif 0 < seconds < 1e-3:
        scale, unit = (1e6, "µs")
    elif 0 < seconds < 1:
        scale, unit = (1e3, "ms")
    return f"{seconds * scale:.9g} {unit}"


class _MarkerUrl:
    """Small QUrl-compatible adapter to keep toggle parsing self-contained."""

    def __init__(self, value: str) -> None:
        self.value = value

    def toString(self) -> str:  # noqa: N802
        return self.value


def marker_entries_from_samples(samples, colors: list[str] | tuple[str, ...]) -> list[MarkerInfoEntry]:  # type: ignore[no-untyped-def]
    entries: list[MarkerInfoEntry] = []
    for index, sample in enumerate(samples):
        # Series without a sample at the marker are omitted, so use the
        # requested series position rather than the result position.
        series_index = getattr(sample, "series_index", None)
        color_index = index if series_index is None else series_index
        unit = f" {sample.unit}" if sample.unit else ""
        original = f"{sample.original_value:.9g}" if sample.original_value is not None else "Unavailable"
        entries.append(MarkerInfoEntry(
            str(sample.series_name), colors[color_index % len(colors)], f"{sample.converted_value:.9g}{unit}",
            (
                f"Date/time: {sample.date_time}",
                f"Log start: {sample.log_elapsed_ms:.9g} ms",
                f"Device time: {sample.sample_time:.9g} {sample.device_time_unit}",
                f"Converted: {sample.converted_value:.9g}{unit}",
                f"Original: {original}",
            ),
        ))
    return entries
