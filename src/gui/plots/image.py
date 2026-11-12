"""Still-image cards with aspect-preserving rendering in the plot workspace."""
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QGuiApplication, QImage, QImageIOHandler, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

from src.gui.plots.base import PlotBaseWidget
from src.gui.plots.theme import canvas_background


# Fallback longest decoded side when no screen is available (offscreen tests).
DEFAULT_DECODE_SIDE = 2560


def decode_image(image_path: Path, max_side: int) -> tuple[QImage, QSize, str]:
    """Decode an image no larger than ``max_side``; safe outside the GUI thread.

    Returns the decoded image, the full-resolution size after EXIF rotation,
    and an error message (empty on success).
    """
    reader = QImageReader(str(image_path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and reader.transformation() & QImageIOHandler.Transformation.TransformationRotate90:
        size.transpose()
    if size.isValid() and max(size.width(), size.height()) > max_side:
        # setScaledSize applies before the EXIF transform, so scale the stored size.
        stored = reader.size()
        reader.setScaledSize(stored.scaled(QSize(max_side, max_side), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        return image, QSize(), f"Could not load image: {reader.errorString()}"
    return image, size if size.isValid() else image.size(), ""


class _DecodeSignals(QObject):
    finished = Signal(int, object, object, str)


class _DecodeImage(QRunnable):
    def __init__(self, generation: int, image_path: Path, max_side: int) -> None:
        super().__init__()
        self.generation, self.image_path, self.max_side = generation, image_path, max_side
        self.signals = _DecodeSignals()

    def run(self) -> None:
        image, size, error = decode_image(self.image_path, self.max_side)
        self.signals.finished.emit(self.generation, image, size, error)


class ImageCanvas(QWidget):
    """Paint a decoded image; decoding runs in the global thread pool."""

    loaded = Signal(str)

    def __init__(self, image_path: Path, parent: QWidget) -> None:
        super().__init__(parent)
        self.setMinimumSize(80, 60)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.image_path = Path(image_path)
        self.image = QImage()
        self.source_size = QSize()
        self.error = ""
        self.loading = True
        self._generation = 0
        self._pixmap: QPixmap | None = None
        self._start_decode()

    def _start_decode(self) -> None:
        self._generation += 1
        task = _DecodeImage(self._generation, self.image_path, _decode_side())
        task.signals.finished.connect(self._decoded)
        self._task = task
        QThreadPool.globalInstance().start(task)

    def _decoded(self, generation: int, image: QImage, size: QSize, error: str) -> None:
        if generation != self._generation:
            return
        self._task = None
        self.loading = False
        self.image, self.source_size, self.error = image, size, error
        self._pixmap = None
        self.update()
        self.loaded.emit(error)

    def dispose(self) -> None:
        """Ignore a decode that finishes after the card closes."""
        self._generation += 1

    def image_rect(self) -> QRect:
        if self.image.isNull():
            return QRect()
        source = self.source_size if self.source_size.isValid() else self.image.size()
        size = source.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRect((self.width() - size.width()) // 2,
                     (self.height() - size.height()) // 2, size.width(), size.height())

    def _scaled_pixmap(self, target: QSize) -> QPixmap:
        """Rescale only when the displayed size changes, not on every repaint."""
        ratio = self.devicePixelRatioF()
        pixels = QSize(max(1, round(target.width() * ratio)), max(1, round(target.height() * ratio)))
        if self._pixmap is None or self._pixmap.size() != pixels:
            self._pixmap = QPixmap.fromImage(self.image.scaled(
                pixels, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self._pixmap.setDevicePixelRatio(ratio)
        return self._pixmap

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.fillRect(self.rect(), canvas_background(self))
        if self.image.isNull():
            painter.setPen(self.palette().text().color())
            painter.drawText(self.rect().adjusted(8, 8, -8, -8),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             "Loading image…" if self.loading else self.error)
        else:
            rect = self.image_rect()
            if rect.width() > 0 and rect.height() > 0:
                painter.drawPixmap(rect.topLeft(), self._scaled_pixmap(rect.size()))


def _decode_side() -> int:
    """Decode no larger than the largest screen; a card never shows more pixels."""
    screens = QGuiApplication.screens()
    sides = [round(max(screen.size().width(), screen.size().height()) * screen.devicePixelRatio())
             for screen in screens]
    return max([DEFAULT_DECODE_SIDE, *sides]) if sides else DEFAULT_DECODE_SIDE


class ImagePlotWidget(PlotBaseWidget):
    """A resizable image card using the shared activation, reorder and close controls."""
    help_topic = "image"

    supports_horizontal_layout = True
    supports_time_offset = False
    plot_status_changed = Signal(str)

    def __init__(self, folder: Path, image_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(folder, Path(image_path).name, parent)
        self.folder = Path(folder)
        self.image_path = Path(image_path)
        self.content_layout.setContentsMargins(4, 0, 4, 2)
        self.title_label = QLabel(self.image_path.name, self.drag_handle)
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setToolTip(str(self.image_path))
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.title_label.setStyleSheet("background: palette(base); color: palette(text);")
        self.canvas = ImageCanvas(self.image_path, self)
        self.canvas.setToolTip(str(self.image_path))
        self.canvas.loaded.connect(self._image_loaded)
        self.content_layout.addWidget(self.canvas, stretch=1)

    def workspace_state(self) -> dict:
        return {"folder": str(self.folder), "path": str(self.image_path)}

    def restore_workspace_state(self, state: dict) -> None:
        """The image is chosen when the card is created; it has no other state."""

    def _image_loaded(self, error: str) -> None:
        if error:
            self.plot_status_changed.emit(f"{self.image_path.name}: {error}")

    def dispose(self) -> None:
        self.canvas.dispose()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if hasattr(self, "title_label"):
            # The filename shares the drag strip without reserving canvas height.
            width = min(self.title_label.sizeHint().width(), max(0, self.drag_handle.width() - 40))
            self.title_label.setGeometry(6, 0, width, self.drag_handle.height())
