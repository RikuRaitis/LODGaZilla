"""Read a 3D model attachment off the GUI thread: its placement, meshes and decoded images."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from src.data.model3d import Model, ModelError, ModelPlacement, load_model, model_folder_files


@dataclass
class LoadedModel:
    folder: Path
    placement: ModelPlacement
    model: Model
    # Each embedded image as H x W x 4 RGBA bytes, first row at the image's top; None when unreadable.
    images: list[np.ndarray | None]


def decode_image(data: bytes) -> np.ndarray | None:
    """An embedded JPEG or PNG as RGBA rows, top row first."""
    image = QImage.fromData(data)
    if image.isNull():
        return None
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = image.width(), image.height()
    rows = np.frombuffer(image.constBits(), dtype=np.uint8, count=image.sizeInBytes()).reshape(height, -1)
    # A copy: the rows belong to the QImage, which is freed when this function returns.
    return rows[:, :width * 4].reshape(height, width, 4).copy()


def read_model_folder(folder: str | Path) -> LoadedModel:
    placement, model_path = model_folder_files(folder)
    model = load_model(model_path)
    return LoadedModel(Path(folder), placement, model, [decode_image(data) for data in model.images])


class _Signals(QObject):
    completed = Signal(int, object)


class _Load(QRunnable):
    def __init__(self, revision: int, folder: Path) -> None:
        super().__init__()
        self.revision, self.folder = revision, folder
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result: LoadedModel | str = read_model_folder(self.folder)
        except ModelError as error:
            result = str(error)
        except Exception as error:  # noqa: BLE001 - any unreadable file is reported on the card.
            result = f"Cannot read the 3D model in {self.folder.name}: {error}"
        self.signals.completed.emit(self.revision, result)


class ModelLoadCoordinator(QObject):
    """One model load per card; the newest request wins. ``loaded`` sends a LoadedModel or a reason."""

    loaded = Signal(object)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.revision = 0
        self.closed = False
        self._workers: dict[int, _Load] = {}

    def request(self, folder: Path) -> None:
        if self.closed:
            return
        self.revision += 1
        worker = _Load(self.revision, Path(folder))
        worker.signals.completed.connect(self._completed)
        self._workers[self.revision] = worker
        QThreadPool.globalInstance().start(worker)

    def cancel(self) -> None:
        """Ignore the loads already running."""
        self.revision += 1

    def _completed(self, revision: int, result: object) -> None:
        self._workers.pop(revision, None)
        if not self.closed and revision == self.revision:
            self.loaded.emit(result)

    def shutdown(self) -> None:
        self.closed = True
