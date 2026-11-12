"""Create plugin: import the example files with the plugin being built, in the background.

Each file is imported by ``python -m src.ai.trial_import`` in its own process, as in
Magic drop, because the importer is code the AI wrote. The H5 file goes beside the
example, where the Navigator shows it. ``finished`` gives a short report for the AI panel.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.ai.magic_drop import run_trial_import, summary_text
from src.ai.plugin_builder import example_sources
from src.jobs.import_data import default_output_path

# Seconds one example file may take.
EXAMPLE_TIMEOUT_SECONDS = 300.0


class PluginExamplesJob(QObject):
    finished = Signal(str)

    def __init__(self, plugin_folder: Path, examples_folder: Path, *, timeout: float = EXAMPLE_TIMEOUT_SECONDS,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.plugin_folder = Path(plugin_folder)
        self.examples_folder = Path(examples_folder)
        self.timeout = timeout
        self._detached = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="plugin-examples", daemon=True).start()

    def detach(self) -> None:
        """The window is closing: report nothing more."""
        self._detached.set()

    def _run(self) -> None:
        lines = []
        for source in example_sources(self.examples_folder):
            try:
                summary = run_trial_import(self.plugin_folder, source, default_output_path(source),
                                           timeout=self.timeout)
            except subprocess.TimeoutExpired:
                lines.append(f"{source.name}: stopped after {self.timeout:g} s")
                continue
            except (OSError, RuntimeError, ValueError) as error:
                lines.append(f"{source.name}: not imported: {error}")
                continue
            series = summary_text(summary)
            lines.append(f"{source.name} → {default_output_path(source).name}" + (f"\n{series}" if series else ""))
        if self._detached.is_set():
            return
        try:
            self.finished.emit("\n".join(lines) or "No example files to import.")
        except RuntimeError:
            pass  # Deleted with its window meanwhile.
