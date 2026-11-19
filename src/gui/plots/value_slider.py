"""A slider that shows its value over its middle, shared by the 3D Map and 3D plot controls."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import QSlider, QWidget


class ValueSlider(QSlider):
    """A horizontal slider that shows its value, read-only, over its middle."""

    def __init__(self, text_for_value, parent: QWidget | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._text_for_value = text_for_value
        self.valueChanged.connect(self.update)

    def value_text(self) -> str:
        return self._text_for_value(self.value())

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        painter = QPainter(self)
        font = painter.font()
        font.setPointSizeF(font.pointSizeF() * 0.85)
        painter.setFont(font)
        text = self.value_text()
        box = QRectF(painter.fontMetrics().boundingRect(text)).adjusted(-3, -1, 3, 1)
        box.moveCenter(QRectF(self.rect()).center())
        background = self.palette().color(QPalette.ColorRole.Window)
        background.setAlpha(210)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(box, 3, 3)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
