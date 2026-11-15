"""Sign in to a plugin source's host, and check for a saved sign-in, off the GUI thread."""
from __future__ import annotations

import threading

from PySide6.QtCore import QCoreApplication, QObject, Signal

from src.plugins.repositories import authorization, sign_in


class _Relay(QObject):
    """Carries one result from the worker thread to the GUI thread.

    The application owns it, so the worker never emits on a deleted object; Qt drops the
    connection when the RepositorySignIn (and its Settings dialog) is deleted first.
    """

    done = Signal(object)


class RepositorySignIn(QObject):
    """``checked`` tells whether a sign-in is saved; ``finished`` gives "" or an error.

    Both run Git (and, for a sign-in, Git Credential Manager's window or browser page)
    in a daemon thread. A result arriving after the owner was deleted is dropped.
    """

    checked = Signal(bool)
    finished = Signal(str)

    def __init__(self, host: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.host = host
        self.busy = False

    def check(self) -> None:
        self._run(lambda: bool(authorization(self.host)), "checked")

    def sign_in(self, *, replace: bool = False) -> None:
        def work() -> str:
            try:
                sign_in(self.host, replace=replace)
            except (OSError, ValueError) as failure:
                return str(failure)
            return ""
        self._run(work, "finished")

    def _run(self, work, signal_name: str) -> None:
        if self.busy:
            return
        self.busy = True
        relay = _Relay(QCoreApplication.instance())
        relay.done.connect(self._deliver)

        def run() -> None:
            try:
                result = work()
            except Exception:  # noqa: BLE001 - the GUI must not wait for ever
                result = "Sign-in failed" if signal_name == "finished" else False
            relay.done.emit((relay, signal_name, result))

        threading.Thread(target=run, name=f"{self.host}-sign-in", daemon=True).start()

    def _deliver(self, message) -> None:
        relay, signal_name, result = message
        relay.deleteLater()
        self.busy = False
        getattr(self, signal_name).emit(result)
