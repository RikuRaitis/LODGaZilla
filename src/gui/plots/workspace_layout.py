"""Vertical plot rows, optional horizontal groups, and card drop previews."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QFrame, QSplitter, QSplitterHandle, QWidget

from src.gui.plots.base import PLOT_MIME_TYPE, PlotBaseWidget


def side_drop_position(size, local) -> str | None:
    """Return "left"/"right" when a point is in a card's side-by-side drop zone."""
    edge_y = min(40, size.height() // 5)
    if not edge_y < local.y() < size.height() - edge_y:
        return None
    edge_x = min(80, size.width() // 4)
    if 0 <= local.x() < edge_x:
        return "left"
    if size.width() - edge_x <= local.x() < size.width():
        return "right"
    return None


def _minimum_side_width(card) -> int:
    return max(180, card.minimumWidth(), card.minimumSizeHint().width())



class PlotStackHandle(QSplitterHandle):
    """Resize the row above a divider without shrinking the next row."""

    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self._last_y = None
        self.setCursor(Qt.CursorShape.SizeVerCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_y = event.globalPosition().toPoint().y()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._last_y is not None and event.buttons() & Qt.MouseButton.LeftButton:
            current_y = event.globalPosition().toPoint().y()
            for index in range(1, self.parent().count()):
                if self.parent().handle(index) is self:
                    self.parent().resize_plot_requested.emit(index - 1, current_y - self._last_y)
                    break
            self._last_y = current_y
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._last_y = None
        event.accept()


class PlotRow(QSplitter):
    def __init__(self, parent):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setChildrenCollapsible(False)
        self.setHandleWidth(6)

    def plots(self):
        return [self.widget(index) for index in range(self.count())]


class PlotStackSplitter(QSplitter):
    """Each item is either a full-width card or a horizontal row of media cards."""

    resize_plot_requested = Signal(int, int)
    layout_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Vertical, parent)
        self.setChildrenCollapsible(False)
        self._standalone_heights: dict[PlotBaseWidget, int] = {}

    def createHandle(self):
        return PlotStackHandle(self.orientation(), self)

    def plots(self) -> list[PlotBaseWidget]:
        result = []
        for index in range(self.count()):
            row = self.widget(index)
            result.extend(row.plots() if isinstance(row, PlotRow) else [row])
        return result

    def row_for(self, plot):
        if self.indexOf(plot) >= 0:
            return plot
        parent = plot.parentWidget()
        if isinstance(parent, PlotRow) and self.indexOf(parent) >= 0:
            return parent
        return None

    @staticmethod
    def minimum_row_height(row):
        if isinstance(row, PlotRow):
            return max((max(card.minimumHeight(), card.minimumSizeHint().height())
                        for card in row.plots()), default=0)
        return row.minimumHeight()

    def set_row_sizes(self, sizes):
        normalized = [max(self.minimum_row_height(self.widget(index)), int(size))
                      for index, size in enumerate(sizes)]
        height = sum(normalized) + max(0, self.count() - 1) * self.handleWidth()
        self.setFixedHeight(max(1, height))
        self.setSizes(normalized)

    def _heights(self):
        return {self.widget(index): height for index, height in enumerate(self.sizes())}

    def _apply_heights(self, heights):
        self.set_row_sizes([heights[self.widget(index)] for index in range(self.count())])
        self.layout_changed.emit()

    def add_card(self, plot, height):
        heights = self._heights()
        self.addWidget(plot)
        self._standalone_heights[plot] = height
        heights[plot] = height
        plot.show()
        self._apply_heights(heights)

    def _collapse_rows(self, heights):
        for row in [self.widget(index) for index in range(self.count())]:
            if not isinstance(row, PlotRow) or row.count() > 1:
                continue
            if row.count() == 1:
                survivor = row.widget(0)
                self.insertWidget(self.indexOf(row), survivor)
                heights[survivor] = heights[row]
                survivor.show()
            row.hide()
            # Keep every reparenting step inside the same top-level window.
            row.setParent(self.parentWidget())
            row.deleteLater()

    def place_in_row(self, cards, widths=None):
        """Put standalone cards side by side in one row, where the first of them is."""
        heights = self._heights()
        row = PlotRow(self)
        self.insertWidget(self.indexOf(cards[0]), row)
        heights[row] = heights[cards[0]]
        for card in cards:
            self._standalone_heights[card] = heights.pop(card)
            row.addWidget(card)
            card.show()
        row.splitterMoved.connect(lambda *_: self.layout_changed.emit())
        row.show()
        if widths and len(widths) == len(cards):
            row.setSizes([max(1, int(width)) for width in widths])
        self._apply_heights(heights)

    def remove_card(self, plot):
        if self.row_for(plot) is None:
            return
        heights = self._heights()
        plot.hide()
        plot.setParent(self.parentWidget())
        self._standalone_heights.pop(plot, None)
        self._collapse_rows(heights)
        self._apply_heights(heights)

    def can_place_beside(self, source, target):
        if source is target or not source.supports_horizontal_layout or not target.supports_horizontal_layout:
            return False
        source_row, target_row = self.row_for(source), self.row_for(target)
        if source_row is None or target_row is None:
            return False
        if source_row is target_row:
            return True
        # Split the target card's current space. Do not squeeze controls below
        # their usable width or introduce horizontal workspace scrolling.
        return target.width() >= _minimum_side_width(source) + _minimum_side_width(target) + 6

    def can_add_beside(self, target) -> bool:
        """Whether a new side-by-side card (at least 180 px wide) fits next to target."""
        if not target.supports_horizontal_layout or self.row_for(target) is None:
            return False
        return target.width() >= 180 + _minimum_side_width(target) + 6

    def move_card(self, source, target, position) -> bool:
        source_row, target_row = self.row_for(source), self.row_for(target)
        if source_row is None or target_row is None or position not in {"left", "right", "above", "below"}:
            return False
        horizontal = position in {"left", "right"}
        if horizontal and not self.can_place_beside(source, target):
            return False
        if source is target and not (isinstance(source_row, PlotRow) and not horizontal):
            return False
        heights = self._heights()
        if source_row is source:
            self._standalone_heights[source] = heights[source]
        if horizontal:
            if not isinstance(target_row, PlotRow):
                self._standalone_heights[target] = heights[target]
                index = self.indexOf(target)
                row = PlotRow(self)
                self.insertWidget(index, row)
                row.addWidget(target)
                heights[row] = heights[target]
                row.splitterMoved.connect(lambda *_: self.layout_changed.emit())
                target.show()
                target_row = row
            widths = dict(zip(target_row.plots(), target_row.sizes()))
            target_index = target_row.indexOf(target)
            source_index = target_row.indexOf(source)
            insert_index = target_index + (position == "right")
            if 0 <= source_index < insert_index:
                insert_index -= 1
            target_row.insertWidget(insert_index, source)
            if source_index < 0:
                half = max(1, widths[target] // 2)
                widths[target] = widths[source] = half
            source.show()
            target_row.show()
            target_row.setSizes([widths[card] for card in target_row.plots()])
        else:
            insert_index = self.indexOf(target_row) + (position == "below")
            source_index = self.indexOf(source)
            if 0 <= source_index < insert_index:
                insert_index -= 1
            self.insertWidget(insert_index, source)
            heights[source] = self._standalone_heights[source]
            source.show()
        self._collapse_rows(heights)
        self._apply_heights(heights)
        return True


class PlotDropController(QObject):
    """Intercept card drags over all child surfaces, including WebEngine views."""

    drop_requested = Signal(object, object, str)

    def __init__(self, workspace, stack):
        super().__init__(workspace)
        self.workspace = workspace
        self.stack = stack
        self.source = None
        self.preview = QFrame(workspace.viewport())
        self.preview.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.preview.hide()
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(60)
        self._scroll_timer.timeout.connect(self._auto_scroll)

    def start(self, source):
        self.finish()
        self.source = source
        QApplication.instance().installEventFilter(self)
        self._scroll_timer.start()

    def finish(self):
        self._scroll_timer.stop()
        QApplication.instance().removeEventFilter(self)
        self.preview.hide()
        self.source = None

    def destination(self, point):
        """Return (card, placement, preview rectangle) in stack coordinates."""
        if self.source not in self.stack.plots():
            return None
        cards = self.stack.plots()
        if not cards:
            return None
        target = None
        for card in cards:
            rect = QRect(card.mapTo(self.stack, QPoint()), card.size())
            if rect.contains(point):
                target = card
                break
        legend = getattr(target, "legend", None)
        if (target is not None and target is not self.source and getattr(target, "accepts_filmstrip_drop", False)
                and hasattr(self.source, "filmstrip_source") and legend is not None and legend.isVisible()
                and legend.rect().contains(legend.mapFrom(self.stack, point))):
            return target, "filmstrip", QRect(legend.mapTo(self.stack, QPoint()), legend.size())
        if target is None:
            # Gaps and empty space below the last row still offer vertical drops.
            target = min(cards, key=lambda card: abs(
                card.mapTo(self.stack, card.rect().center()).y() - point.y()))
        row = self.stack.row_for(target)
        rect = QRect(target.mapTo(self.stack, QPoint()), target.size())
        row_rect = QRect(row.mapTo(self.stack, QPoint()), row.size())
        position = "above" if point.y() < row_rect.center().y() else "below"
        if rect.contains(point) and self.stack.can_place_beside(self.source, target):
            position = side_drop_position(rect.size(), point - rect.topLeft()) or position
        if target is self.source and not (isinstance(row, PlotRow) and position in {"above", "below"}):
            return None
        if position in {"left", "right"}:
            preview = QRect(rect)
            preview.setWidth(rect.width() // 2)
            if position == "right":
                preview.moveRight(rect.right())
        else:
            y = row_rect.top() if position == "above" else row_rect.bottom() - 11
            preview = QRect(row_rect.left(), y, row_rect.width(), 12)
        return target, position, preview

    def update_preview(self, viewport_point):
        viewport = self.workspace.viewport()
        if not viewport.rect().contains(viewport_point):
            self.preview.hide()
            return None
        destination = self.destination(self.stack.mapFrom(viewport, viewport_point))
        if destination is None:
            self.preview.hide()
            return None
        rect = destination[2]
        rect = QRect(self.stack.mapTo(viewport, rect.topLeft()), rect.size())
        color = viewport.palette().highlight().color()
        self.preview.setStyleSheet(
            f"background: rgba({color.red()}, {color.green()}, {color.blue()}, 65); "
            f"border: 2px solid {color.name()};"
        )
        self.preview.setGeometry(rect.intersected(viewport.rect()))
        self.preview.show()
        self.preview.raise_()
        return destination

    def _auto_scroll(self):
        if self.source is None:
            return
        viewport = self.workspace.viewport()
        point = viewport.mapFromGlobal(QCursor.pos())
        if viewport.rect().contains(point):
            delta = -18 if point.y() < 30 else 18 if point.y() > viewport.height() - 30 else 0
            if delta:
                bar = self.workspace.verticalScrollBar()
                bar.setValue(bar.value() + delta)
        self.update_preview(point)

    def eventFilter(self, watched, event):
        if self.source is None:
            return False
        kind = event.type()
        if kind == QEvent.Type.DragLeave:
            self.preview.hide()
            return False
        if kind not in {QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop}:
            return False
        if event.source() is not self.source or not event.mimeData().hasFormat(PLOT_MIME_TYPE):
            return False
        if not isinstance(watched, QWidget):
            return False
        viewport = self.workspace.viewport()
        if watched is not viewport and not viewport.isAncestorOf(watched):
            self.preview.hide()
            event.ignore()
            return True
        point = viewport.mapFromGlobal(watched.mapToGlobal(event.position().toPoint()))
        destination = self.update_preview(point)
        # Accept entry even over the source so Qt continues delivering moves.
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        if kind == QEvent.Type.Drop:
            source = self.source
            self.finish()
            if destination is None:
                event.ignore()
            else:
                self.drop_requested.emit(source, destination[0], destination[1])
        return True
