"""Event-loop waits for GUI tests that let background threads run.

PySide6's ``QTest.qWait`` keeps holding Python's global interpreter lock while it
waits, so worker threads (HDF5 reads, statistics) barely progress during the
wait and tests time out when the suite is busy. ``QEventLoop.exec`` releases it,
like the application's own ``app.exec``.
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer


def qt_wait(ms: int) -> None:
    """Process events for ``ms`` milliseconds, as ``QTest.qWait`` does."""
    loop = QEventLoop()
    QTimer.singleShot(max(0, int(ms)), loop.quit)
    loop.exec()
    # QTest.qWait also deletes objects scheduled with deleteLater.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def qt_wait_until(predicate: Callable[[], object], timeout_ms: int = 5000, step_ms: int = 10) -> bool:
    """Process events until ``predicate()`` is true or the timeout passes."""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return bool(predicate())
        qt_wait(step_ms)


def dispose_cards() -> None:
    """Dispose, close and delete every top-level card and series plot, now.

    Cards stop their timers and loader threads in ``dispose()``. Deleting them here,
    rather than leaving ``deleteLater`` to whichever later test pumps the event loop,
    keeps one test's widgets and threads from being torn down during another's.
    """
    from PySide6.QtWidgets import QApplication

    from src.gui.plots.base import PlotBaseWidget
    from src.gui.plots.series import SeriesPlotWidget

    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if isinstance(widget, (PlotBaseWidget, SeriesPlotWidget)):
            dispose = getattr(widget, "dispose", None)
            if callable(dispose):
                dispose()
            widget.close()
            widget.deleteLater()
    qt_wait(0)
