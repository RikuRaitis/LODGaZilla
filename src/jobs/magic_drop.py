"""Run one Magic drop in the background: copy, let the AI agent write the parser, import, describe.

The agent is Claude or Codex (``src.ai.providers``). Each step reports to the AI panel
through ``event``; ``finished`` reports the result. The agent and the trial imports run
as child processes (``AgentRun``); Stop or the time limit ends the agent's whole process
tree. A failed or stopped Magic drop removes the folder it created in the data folder (only
copies were in it) and keeps the draft for checking.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.ai import magic_drop
from src.ai.providers import CLAUDE, AgentProvider
from src.jobs.agent_run import AgentRun

# A trial import's error for a file the parser does not accept (``select_importer``).
REJECTED = "No configured importer accepts"


class MagicDropCancelled(Exception):
    pass


class MagicDropJob(QObject):
    # kind ("status", "text", "tool", "result" or "error") and text.
    event = Signal(str, str)
    # Succeeded, a closing message, and the Magic drop folder (None when removed).
    finished = Signal(bool, str, object)

    def __init__(self, executable: Path, files: list[Path], data_folder: Path, *, hints: str = "",
                 max_budget_usd: float = 2.0, time_limit_seconds: float = 900.0,
                 trial_timeout_seconds: float = 600.0, drafts_root: Path | None = None,
                 provider: AgentProvider = CLAUDE, web: bool = False, installed_importers=(),
                 parent: QObject | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.provider = provider
        # (id, title, folder) of the installed importer plugins, for the AI's suggestion.
        self.installed_importers = tuple(installed_importers)
        # Whether the agent may read web pages, such as the format's specification.
        self.web = web
        self.executable = Path(executable)
        self.files = [Path(path) for path in files]
        self.data_folder = Path(data_folder)
        self.hints = hints
        self.max_budget_usd = max_budget_usd
        self.time_limit_seconds = time_limit_seconds
        self.trial_timeout_seconds = trial_timeout_seconds
        self.drafts_root = drafts_root
        self.folder: Path | None = None
        self.draft: Path | None = None
        self._cancelled = threading.Event()
        self._agent = AgentRun(provider, self.event.emit, time_limit_seconds=time_limit_seconds)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="magic-drop", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancelled.set()
        self._stop_agent()

    def wait(self, timeout: float | None = None) -> bool:
        if self._thread is not None:
            self._thread.join(timeout)
        return self._thread is None or not self._thread.is_alive()

    def _run(self) -> None:
        try:
            self.folder = magic_drop.magic_drop_folder(self.data_folder, self.files)
            copies = magic_drop.copy_sources(self.files, self.folder)
            self.event.emit("status", f"Copied {len(copies)} file(s) to {self.folder}.")
            self.draft = magic_drop.create_draft(copies, root=self.drafts_root,
                                                 installed_importers=self.installed_importers,
                                                 instructions_file=self.provider.instructions_file)
            self._run_agent(copies)
            imported, rejected, pairs = 0, [], []
            covered = set()
            for copy in copies:
                if copy.name in covered:
                    continue
                self._check_cancelled()
                self.event.emit("status", f"Importing {copy.name}…")
                try:
                    plan = magic_drop.run_trial_import(self.draft, copy, copy.with_suffix(".h5"),
                                                       timeout=self.trial_timeout_seconds,
                                                       cancelled=self._cancelled, plan_only=True)
                    covered.update(plan["inputs"])
                    covered.update(plan.get("dependencies", ()))
                    summary = magic_drop.run_trial_import(self.draft, copy, copy.with_suffix(".h5"),
                                                          timeout=self.trial_timeout_seconds,
                                                          cancelled=self._cancelled)
                except RuntimeError as error:
                    # A file the parser does not accept, such as the format's description
                    # dropped with the data, stays as it is; any other failure ends the drop.
                    if REJECTED not in str(error):
                        raise
                    rejected.append((copy, error))
                    continue
                imported += 1
                pairs.append((copy, Path(summary["output"])))
                self.event.emit("result", f"{Path(summary['output']).name}\n{magic_drop.summary_text(summary)}")
            if not imported:
                raise rejected[0][1]
            for copy, _error in rejected:
                self.event.emit("status", f"{copy.name}: not imported; the parser does not read it.")
            metadata = magic_drop.read_metadata(self.draft)
            magic_drop.write_folder_info(self.folder, metadata, self.draft, pairs)
            self.finished.emit(True, f"Created {self.folder.name} with {imported} H5 file(s).", self.folder)
        except MagicDropCancelled:
            self._remove_folder()
            self.finished.emit(False, "Magic drop stopped.", None)
        except Exception as error:  # noqa: BLE001 - any failure ends this drop with its reason.
            self._remove_folder()
            # Stopping kills the agent, so writing the prompt or reading its output can then
            # fail; that error is the stop, not a failure.
            message = "Magic drop stopped." if self._cancelled.is_set() else f"Magic drop failed: {error}"
            self.finished.emit(False, message, None)
        finally:
            self._stop_agent()

    def _check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise MagicDropCancelled()

    def _run_agent(self, copies: list[Path]) -> None:
        self._check_cancelled()
        self.event.emit("status", f"{self.provider.name} is reading the file(s) and writing a parser…")
        # The Magic drop folder holds only copies; the agent reads them there.
        command = self.provider.magic_drop_command(self.executable, [self.folder],
                                                   max_budget_usd=self.max_budget_usd, web=self.web)
        prompt = magic_drop.agent_prompt(copies, self.hints, self.provider.instructions_file)
        self._agent.run(command, self.draft, prompt, self._check_cancelled)

    @property
    def _process(self) -> subprocess.Popen | None:
        return self._agent.process

    def _stop_agent(self) -> None:
        self._agent.stop()

    def _remove_folder(self) -> None:
        """Remove the folder this drop created; it holds only copies and partial H5 files."""
        if self.folder is not None and self.folder.is_dir():
            shutil.rmtree(self.folder, ignore_errors=True)
            self.folder = None


class SignInCheck(QObject):
    """Ask an agent's CLI in the background whether it is signed in; ``checked`` gives the answer."""

    checked = Signal(bool)

    def __init__(self, provider: AgentProvider = CLAUDE, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.provider = provider
        self._running = False
        self._detached = False

    def detach(self) -> None:
        """The window is closing: a check still running reports nothing."""
        self._detached = True

    def start(self, configured: str | Path | None = None) -> None:
        """Find the CLI (the configured path, PATH, VS Code extensions) and ask it, off the GUI thread."""
        if self._running:
            return
        self._running = True

        def run() -> None:
            try:
                executable = self.provider.find(configured or None)
                signed_in = executable is not None and self.provider.is_signed_in(executable)
            except OSError:
                signed_in = False
            self._running = False
            if self._detached:
                return
            try:
                self.checked.emit(signed_in)
            except RuntimeError:
                pass  # Deleted with its window while the CLI answered.

        threading.Thread(target=run, name=f"{self.provider.name.lower()}-sign-in-check", daemon=True).start()


class PluginDropJob(QObject):
    """Magic drop's first step, without AI: can an enabled importer plugin read every file?

    If so, the files are copied to a new folder of the data folder, as Magic drop does, and
    ``finished`` gives that folder and the plugins' (id, name) pairs; otherwise ``None`` and
    no pairs, and nothing is copied.
    """

    finished = Signal(object, list)

    def __init__(self, files: list[Path], data_folder: Path, enabled_plugins: dict[str, bool],
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.files = [Path(path) for path in files]
        self.data_folder = Path(data_folder)
        self.enabled_plugins = dict(enabled_plugins)
        self.folder: Path | None = None
        self._thread: threading.Thread | None = None
        self._detached = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="magic-drop-plugins", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Nothing long runs here; a stopped check reports nothing."""
        self._detached.set()

    def wait(self, timeout: float | None = None) -> bool:
        if self._thread is not None:
            self._thread.join(timeout)
        return self._thread is None or not self._thread.is_alive()

    def _run(self) -> None:
        from src.app.settings import load_hdf5_settings
        from src.jobs.import_data import load_import_configurations, select_importer
        from src.data.import_sets import describe_import_set
        plugins: list[tuple[str, str]] = []
        folder = None
        try:
            configurations = load_import_configurations(enabled_plugins=self.enabled_plugins,
                                                        hdf5_settings=load_hdf5_settings())
            copies = {}
            dependencies = set()
            deferred = []
            for path in self.files:
                try:
                    configuration, importer = select_importer(path, configurations)
                except ValueError:
                    deferred.append(path.resolve())
                    continue
                group = describe_import_set(importer, path)
                required = [path]
                if group:
                    group.check_ready(path.parent)
                    required = [path.parent / name for name in (*group.inputs, *group.dependencies)]
                    dependencies.update((path.parent / name).resolve() for name in group.dependencies)
                for member in required:
                    previous = copies.get(member.name.casefold())
                    if previous is not None and previous.resolve() != member.resolve():
                        raise ValueError(f"Different dropped inputs have the same name: {member.name}")
                    copies[member.name.casefold()] = member
                pair = (configuration.path.parent.name, configuration.name)
                if pair not in plugins:
                    plugins.append(pair)
            if not plugins or any(path not in dependencies for path in deferred):
                raise ValueError("Some dropped files have no compatible importer")
            if self._detached.is_set():
                return  # Stopped: copy nothing.
            folder = magic_drop.magic_drop_folder(self.data_folder, self.files)
            magic_drop.copy_sources(list(copies.values()), folder)
        except (OSError, ValueError):
            # Some file no enabled plugin reads (or no plugin is enabled): the AI's turn.
            folder, plugins = None, []
        self.folder = folder
        if self._detached.is_set():
            return
        try:
            self.finished.emit(folder, plugins)
        except RuntimeError:
            pass  # Deleted with its window meanwhile.


class DraftImportJob(QObject):
    """Import one file again with the Magic drop parser that first imported it.

    The parser is code the AI wrote, so it runs in its own process, as in the drop
    (``magic_drop.run_trial_import``). Its Source data record is updated when done.
    """

    # Succeeded and a closing message.
    finished = Signal(bool, str)

    def __init__(self, draft: Path, source: Path, output: Path, *, timeout_seconds: float = 600.0,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.draft, self.source, self.output = Path(draft), Path(source), Path(output)
        self.timeout_seconds = timeout_seconds
        self._cancelled = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="magic-drop-reimport", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancelled.set()

    def wait(self, timeout: float | None = None) -> bool:
        if self._thread is not None:
            self._thread.join(timeout)
        return self._thread is None or not self._thread.is_alive()

    def _run(self) -> None:
        try:
            summary = magic_drop.run_trial_import(self.draft, self.source, self.output, timeout=self.timeout_seconds,
                                                  cancelled=self._cancelled)
            magic_drop.record_draft_import(self.source.parent, self.source, Path(summary["output"]), self.draft)
            succeeded, message = True, f"Imported {self.source.name} again with its Magic drop parser."
        except Exception as error:  # noqa: BLE001 - reported in the Internal log panel.
            succeeded, message = False, f"Could not import {self.source.name} again: {error}"
        if self._cancelled.is_set():
            return
        try:
            self.finished.emit(succeeded, message)
        except RuntimeError:
            pass  # Deleted with its window meanwhile.
