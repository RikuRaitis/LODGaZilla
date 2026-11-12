"""One run of an AI agent's CLI (Claude or Codex) in a background thread's process.

Shared by Magic drop and Create plugin. The prompt goes on stdin; the CLI's stream is
turned into steps and replies (``emit(kind, text)``). Stop or the time limit ends the
agent's whole process tree. The run keeps the session ID, so a next run can continue it.
"""
from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from src.ai.agent_common import NO_WINDOW, agent_environment
from src.ai.providers import AgentProvider
from src.jobs.process_tree import WindowsProcessTree, kill_process_tree, popen_in_tree


class AgentRun:
    def __init__(self, provider: AgentProvider, emit: Callable[[str, str], None], *,
                 time_limit_seconds: float) -> None:
        self.provider = provider
        self.emit = emit
        self.time_limit_seconds = time_limit_seconds
        self.process: subprocess.Popen | None = None
        self.session_id = ""
        self._tree: WindowsProcessTree | None = None
        self._stop_requested = False

    def run(self, command: list[str], cwd: Path, prompt: str, check_cancelled: Callable[[], None]) -> None:
        """Run to the end; raise RuntimeError with the reason when the agent fails.

        ``check_cancelled`` raises when the user stopped the run, which ends the process
        too; it is called before a failure is reported, so a stop is not a failure.
        """
        name = self.provider.name
        check_cancelled()
        # Started suspended in its process tree, so nothing the agent starts can escape Stop.
        self.process, self._tree = popen_in_tree(
            command, cwd=cwd, env=agent_environment(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW)
        if self._stop_requested:
            # Stopped while the process was starting.
            self.stop()
        # The time limit ends the whole run, including tools the agent started.
        timer = threading.Timer(self.time_limit_seconds, self.stop)
        timer.start()
        started = time.monotonic()
        outcome = None
        last_text = ""
        # Lines that are not stream events, such as CLI errors; the last ones explain a failure.
        other_lines: list[str] = []
        try:
            self.process.stdin.write(prompt)
            self.process.stdin.close()
            for line in self.process.stdout:
                if not line.lstrip().startswith("{"):
                    other_lines = (other_lines + [line.strip()])[-5:] if line.strip() else other_lines
                for event in self.provider.parse_stream_line(line):
                    if event.session_id:
                        self.session_id = event.session_id
                    if event.kind in ("result", "error"):
                        outcome = event
                    elif event.text:
                        last_text = event.text if event.kind == "text" else last_text
                        self.emit(event.kind, event.text)
            self.process.wait()
        finally:
            timer.cancel()
        check_cancelled()
        if time.monotonic() - started >= self.time_limit_seconds:
            raise RuntimeError(f"{name} did not finish within {self.time_limit_seconds / 60:g} minutes.")
        if outcome is None:
            raise RuntimeError(other_lines[-1] if other_lines else f"{name} ended with exit code {self.process.returncode}.")
        cost = f" (cost ${outcome.cost_usd:.2f})" if outcome.cost_usd else ""
        if outcome.kind == "error":
            raise RuntimeError(f"{name} stopped: {outcome.text}{cost}")
        # Claude's result repeats its last message; show it only when it is new. Codex's has no text.
        if outcome.text.strip() and outcome.text.strip() != last_text:
            self.emit("text", outcome.text + cost)
        elif cost:
            self.emit("status", f"{name} finished{cost}.")

    def stop(self) -> None:
        self._stop_requested = True
        process, tree, self._tree = self.process, self._tree, None
        if process is not None:
            kill_process_tree(process, tree)
