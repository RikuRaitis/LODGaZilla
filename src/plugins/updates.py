"""GUI-thread owner of the bounded repository worker and its cached results."""
from dataclasses import asdict
import json
import multiprocessing
import os
from pathlib import Path
import queue
import signal

from PySide6.QtCore import QObject, QTimer, Signal

from src.jobs.plugin_repository import repository_job
from src.jobs.process_tree import start_process_tree
from src.plugins.catalog import PLUGIN_ROOT
from src.plugins.installation import pending_requests, pending_status
from src.plugins.repositories import Candidate


class PluginUpdates(QObject):
    changed = Signal()

    def __init__(self, parent=None, root=PLUGIN_ROOT):
        super().__init__(parent)
        self.root = Path(root)
        self.rows = []
        self.message = ""
        self.errors = []
        self.busy = False
        self.process = self.tree = self.results = None
        self.operation = ""
        for request in pending_requests(self.root):
            try:
                candidate = Candidate(**json.loads(request.read_text(encoding="utf-8"))["candidate"])
                self.rows.append((candidate, "", pending_status(request)))
            except (OSError, ValueError, KeyError, TypeError):
                self.errors.append("Unreadable pending plugin installation: " + request.parent.name)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)

    def check(self, sources):
        if not sources:
            self.message = "Enter a GitHub account or Bitbucket workspace URL first."
            self.changed.emit()
            return
        self.start("check", sources)

    def install(self, candidate):
        self.start("install", asdict(candidate))

    def start(self, operation, payload):
        if self.busy:
            return
        self.operation = operation
        self.busy = True
        self.message = "Checking repositories..." if operation == "check" else "Downloading and validating plugin..."
        self.errors = []
        context = multiprocessing.get_context("spawn")
        gate = context.Event()
        self.results = context.Queue()
        self.process = context.Process(target=repository_job,
                                       args=(gate, self.results, operation, payload, str(self.root)))
        try:
            self.tree = start_process_tree(self.process, gate)
            self.timer.start()
        except (OSError, RuntimeError) as error:
            self.message = f"Could not start plugin operation: {error}"
            self.shutdown()
        self.changed.emit()

    def poll(self):
        try:
            success, result = self.results.get_nowait()
        except queue.Empty:
            if self.process.is_alive():
                return
            # A terminated worker cannot produce another result.
            success, result = False, "Plugin worker stopped before completing the operation."
        if not success:
            self.message = str(result)
        elif self.operation == "check":
            self.rows, self.errors = result
            available = sum(bool(action) for _, action, _ in self.rows)
            self.message = f"Check complete: {available} plugin(s) available to install or update."
            if self.errors:
                self.message += " Some repositories could not be checked."
        else:
            self.rows = [(c, "" if c.plugin_id == result else action,
                          "Restart required" if c.plugin_id == result else status)
                         for c, action, status in self.rows]
            self.message = "Plugin downloaded. Restart LODGaZilla to finish installation and check dependencies."
        self.shutdown()
        self.changed.emit()

    def shutdown(self):
        self.timer.stop()
        if self.tree is not None:
            self.tree.close()
            self.tree = None
        if self.process is not None and self.process.pid is not None:
            if os.name != "nt":
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(2)
            self.process.close()
        if self.results is not None:
            self.results.close()
            self.results.cancel_join_thread()
        self.process = self.results = None
        self.busy = False
