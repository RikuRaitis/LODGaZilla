"""Plot card code loaded after the main window opens.

The card modules bring pyqtgraph, OpenGL, PyAV, Qt Multimedia and Qt WebEngine,
which together take as long to import as the rest of the application. The main
window opens without them: `CardModuleLoader` imports them on a worker thread
once the window is shown, and a `LazyAttribute` stands in for a card class or
helper until then. Using one (creating a card, for example) waits for the
worker and imports its module at once if needed. `isinstance` checks against a
stand-in never import anything: while its module is not loaded, no such card
can exist.
"""
from __future__ import annotations

import importlib
import sys
import threading
from typing import Any

from PySide6.QtCore import QObject, Signal

# Imported by the worker, in this order.
CARD_MODULES = (
    "src.gui.plots.series",
    "src.gui.plots.time_offset",
    "src.gui.plots.value_boxes",
    "src.gui.plots.line",
    "src.gui.plots.event_state",
    "src.gui.plots.frequency",
    "src.gui.plots.sample_interval",
    "src.gui.plots.multi",
    "src.gui.plots.xy",
    "src.gui.plots.image",
    "src.gui.plots.gnss",
    "src.gui.plots.xyz",
    "src.gui.plots.gnss_3d",
    "src.gui.plots.video",
    "src.gui.plots.video.widget",
)
# Qt bindings that start Qt libraries; imported on the GUI thread before the worker runs.
QT_BINDINGS = (
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtMultimedia",
    "PySide6.QtOpenGLWidgets",
)

_worker: threading.Thread | None = None


def wait_for_card_modules() -> None:
    """Let a running worker finish, so two threads never import the same modules at once."""
    worker = _worker
    if worker is not None and worker.is_alive() and threading.current_thread() is not worker:
        worker.join()


def card_modules_loaded() -> bool:
    return all(name in sys.modules for name in CARD_MODULES) and (_worker is None or not _worker.is_alive())


class LazyAttribute:
    """A class or function from a card module, imported on first use."""

    def __init__(self, module: str, name: str) -> None:
        self.module = module
        self.name = name
        self._value: Any = None

    def resolve(self) -> Any:
        if self._value is None:
            wait_for_card_modules()
            self._value = getattr(importlib.import_module(self.module), self.name)
        return self._value

    def loaded(self) -> Any:
        """The class or function if its module has been imported, else None; never imports."""
        if self._value is None:
            module = sys.modules.get(self.module)
            # A module the worker is still importing may not define the name yet.
            self._value = getattr(module, self.name, None) if module is not None else None
        return self._value

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.resolve()(*args, **kwargs)

    def __instancecheck__(self, instance: object) -> bool:
        cls = self.loaded()
        return cls is not None and isinstance(instance, cls)

    def __subclasscheck__(self, subclass: type) -> bool:
        cls = self.loaded()
        return cls is not None and issubclass(subclass, cls)

    def __getattr__(self, attribute: str) -> Any:
        if attribute.startswith("__"):
            raise AttributeError(attribute)
        return getattr(self.resolve(), attribute)

    def __repr__(self) -> str:
        return f"<lazy {self.module}.{self.name}>"


def loaded_class(value: Any) -> type | None:
    """A real class as is, or a stand-in's class if its module is loaded (for findChildren)."""
    return value if isinstance(value, type) else value.loaded()


class CardModuleLoader(QObject):
    """Import the card modules in the background; ``finished`` gives an error text or ""."""

    finished = Signal(str)

    def start(self) -> None:
        global _worker
        if _worker is not None:
            return
        for name in QT_BINDINGS:
            importlib.import_module(name)
        _worker = threading.Thread(target=self._run, name="card-modules", daemon=True)
        _worker.start()

    def _run(self) -> None:
        errors = []
        for name in CARD_MODULES:
            try:
                importlib.import_module(name)
            except Exception as error:  # noqa: BLE001 - reported; a card retries on first use.
                errors.append(f"{name}: {type(error).__name__}: {error}")
        self.finished.emit("; ".join(errors))
