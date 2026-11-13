"""Create plugin: one question to the AI agent, run in the background.

The first question creates ``plugin/<id>`` as a Git repository with the agent's
instructions (`src.ai.plugin_builder`); later ones continue the agent's session there.
After each answer LODGaZilla commits what the agent changed.
Attached files are copied into the plugin's work folder. ``event`` reports the agent's
steps and replies; ``finished`` reports whether the run worked, a closing note and the
session ID for the next question.
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.ai import plugin_builder
from src.ai.providers import CLAUDE, AgentProvider
from src.jobs.agent_run import AgentRun


class PluginChatCancelled(Exception):
    pass


class PluginChatJob(QObject):
    # kind ("status", "text", "tool", "result" or "error") and text.
    event = Signal(str, str)
    # Succeeded, a closing note ("" for none) and the session ID to continue.
    finished = Signal(bool, str, str)

    def __init__(self, executable: Path, plugin_id: str, text: str, files: list[Path], *,
                 session_id: str = "", provider: AgentProvider = CLAUDE, web: bool = False,
                 max_budget_usd: float = 2.0, time_limit_seconds: float = 900.0,
                 plugin_root: Path | None = None, examples_folder: Path | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.executable = Path(executable)
        self.plugin_id = plugin_id
        self.text = text
        self.files = [Path(path) for path in files]
        self.session_id = session_id
        self.provider = provider
        self.web = web
        self.max_budget_usd = max_budget_usd
        self.plugin_root = plugin_root or plugin_builder.PLUGIN_ROOT
        self.folder = self.plugin_root / plugin_id
        # The plugin's examples folder in the data folder, where the attached files are also
        # copied (off the GUI thread: they can be large); None without a data folder.
        self.examples_folder = Path(examples_folder) if examples_folder is not None else None
        self._cancelled = threading.Event()
        self._agent = AgentRun(provider, self.event.emit, time_limit_seconds=time_limit_seconds)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="create-plugin", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancelled.set()
        self._agent.stop()

    def wait(self, timeout: float | None = None) -> bool:
        if self._thread is not None:
            self._thread.join(timeout)
        return self._thread is None or not self._thread.is_alive()

    def _check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise PluginChatCancelled()

    def _run(self) -> None:
        instructions_file = self.provider.instructions_file
        try:
            # The first question to this provider writes its instructions file; Claude and
            # Codex read theirs from different files, so switching provider adds the other.
            if not self.folder.exists():
                plugin_builder.create_plugin_folder(self.plugin_id, instructions_file, root=self.plugin_root)
                self.event.emit("status", f"Created plugin/{self.plugin_id} as a new Git repository.")
            elif not (self.folder / instructions_file).exists():
                plugin_builder.add_instructions(self.folder, self.plugin_id, instructions_file)
            copies = plugin_builder.copy_inputs(self.folder, self.files)
            if self.files and self.examples_folder is not None:
                try:
                    plugin_builder.copy_examples(self.examples_folder, self.plugin_id, self.files)
                    self.event.emit("status", f"Copied to the folder {self.examples_folder.name} in the data folder.")
                except (OSError, ValueError) as error:
                    self.event.emit("status", f"Could not copy the files to {self.examples_folder}: {error}")
            had_manifest = plugin_builder.plugin_ready(self.folder)
            self._check_cancelled()
            self.event.emit("status", f"{self.provider.name} is working on the plugin…")
            command = self.provider.plugin_command(
                self.executable, [plugin_builder.REPOSITORY_ROOT], max_budget_usd=self.max_budget_usd,
                web=self.web, resume=self.session_id)
            prompt = plugin_builder.plugin_prompt(self.text, copies, instructions_file, first=not self.session_id)
            self._agent.run(command, self.folder, prompt, self._check_cancelled)
            committed = plugin_builder.commit_changes(self.folder, self.text)
            if committed:
                self.event.emit("status", committed)
            note = ""
            if plugin_builder.plugin_ready(self.folder) and not had_manifest:
                note = (f"plugin/{self.plugin_id} now has a plugin manifest. When it works, enable it in "
                        "Settings > Plugins; no restart is needed.")
            self.finished.emit(True, note, self._agent.session_id or self.session_id)
        except PluginChatCancelled:
            self.finished.emit(False, "Stopped.", self._agent.session_id or self.session_id)
        except Exception as error:  # noqa: BLE001 - any failure ends this question with its reason.
            message = "Stopped." if self._cancelled.is_set() else f"{self.provider.name} could not answer: {error}"
            self.finished.emit(False, message, self._agent.session_id or self.session_id)
        finally:
            self._agent.stop()
