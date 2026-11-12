"""The installed OpenAI Codex CLI, driven as a background process. No Qt.

Like the Claude Code CLI, Codex signs in with its own browser flow and keeps its own
login; LODGaZilla only finds it, asks whether it is signed in, starts its sign-in, and
runs it non-interactively (``codex exec``).

Codex works through shell commands, so it has no tool list to restrict. Its sandbox
does the limiting instead: it may write only in its working folder (the draft) and the
folders given with ``--add-dir`` (and the temporary folder), and it has no network
access. The instructions ask it to run only Python.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from src.ai.agent_common import NO_WINDOW, AgentEvent

EXECUTABLE = "codex.exe" if os.name == "nt" else "codex"
# Where the OpenAI (ChatGPT/Codex) VS Code extension ships the CLI when it is not on PATH.
VSCODE_EXTENSIONS = Path.home() / ".vscode" / "extensions"
# Codex reads the instructions in its working folder from this file.
INSTRUCTIONS_FILE = "AGENTS.md"


def find_codex(configured: str | Path | None = None) -> Path | None:
    """The Codex CLI: the configured path, then PATH, then the newest VS Code extension."""
    if configured and Path(configured).is_file():
        return Path(configured)
    on_path = shutil.which("codex")
    if on_path:
        return Path(on_path)
    candidates = sorted(VSCODE_EXTENSIONS.glob(f"openai.chatgpt-*/bin/*/{EXECUTABLE}"),
                        key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def is_signed_in(executable: Path, *, timeout: float = 30.0) -> bool:
    """Whether ``codex login status`` reports a login; any failure counts as not signed in."""
    try:
        completed = subprocess.run([str(executable), "login", "status"], capture_output=True, text=True,
                                   timeout=timeout, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and "not logged in" not in (completed.stdout + completed.stderr).lower()


def start_sign_in(executable: Path) -> subprocess.Popen:
    """Open the CLI's own sign-in in a visible console; it opens the browser."""
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return subprocess.Popen([str(executable), "login"], creationflags=flags)


def agent_command(executable: Path, write_folders: list[Path], *, max_budget_usd: float | None = None,
                  web: bool = False, resume: str = "") -> list[str]:
    """A non-interactive run in the current folder; the prompt is read from stdin.

    Codex has no cost limit, so ``max_budget_usd`` is not used; the Magic drop time
    limit still ends the run. The user's own Codex configuration is kept, because on
    Windows it holds the sandbox setup (``[windows] sandbox``) without which Codex
    rejects every command; the sandbox, network and MCP servers are set here instead.
    The workspace-write sandbox also lets Codex write in the temporary folder.

    ``web`` turns on Codex's own web search, which reads pages such as format
    specifications; its shell commands still have no network. ``resume`` continues an
    earlier session (``codex exec resume``), so the agent remembers the conversation.
    """
    command = [str(executable), "exec", "--json", "--color", "never", "--skip-git-repo-check",
               "--sandbox", "workspace-write", "-c", "sandbox_workspace_write.network_access=false",
               "-c", "mcp_servers={}", "-c", f'web_search="{"live" if web else "disabled"}"']
    for folder in write_folders:
        command += ["--add-dir", str(folder)]
    if resume:
        command += ["resume", resume]
    return command + ["-"]


def magic_drop_command(executable: Path, write_folders: list[Path], *, max_budget_usd: float | None = None,
                       web: bool = False) -> list[str]:
    return agent_command(executable, write_folders, web=web)


def plugin_command(executable: Path, read_folders: list[Path], *, max_budget_usd: float | None = None,
                   web: bool = False, resume: str = "") -> list[str]:
    """Create plugin works in the plugin's folder; Codex reads the host's files without being given them."""
    return agent_command(executable, [], web=web, resume=resume)


def parse_stream_line(line: str) -> list[AgentEvent]:
    """Read one ``codex exec --json`` line; unknown or malformed lines give no events."""
    try:
        record = json.loads(line)
    except ValueError:
        return []
    if not isinstance(record, dict):
        return []
    kind = record.get("type")
    if kind == "thread.started":
        return [AgentEvent("status", "", str(record.get("thread_id") or ""))]
    if kind in ("item.started", "item.completed"):
        item = record.get("item") or {}
        item_type = item.get("type")
        if kind == "item.completed" and item_type == "agent_message" and str(item.get("text", "")).strip():
            return [AgentEvent("text", str(item["text"]).strip())]
        if kind == "item.started" and item_type == "command_execution":
            return [AgentEvent("tool", f"Ran: {shell_command(str(item.get('command', '')))[:120]}")]
        if kind == "item.started" and item_type == "web_search":
            return [AgentEvent("tool", f"Searched the web: {str(item.get('query', ''))[:120]}")]
        if kind == "item.completed" and item_type == "file_change":
            names = [Path(str(change.get("path", ""))).name for change in item.get("changes") or []]
            return [AgentEvent("tool", f"Changed {', '.join(name for name in names if name)}")] if names else []
        return []
    if kind == "turn.completed":
        return [AgentEvent("result")]
    if kind == "turn.failed":
        error = record.get("error") or {}
        return [AgentEvent("error", str(error.get("message") or "the turn failed"))]
    if kind == "error":
        return [AgentEvent("error", str(record.get("message") or "error"))]
    return []


def shell_command(command: str) -> str:
    """The command Codex ran, without the shell that wraps it (``powershell.exe -Command ...``)."""
    match = re.match(r'^\S*?(?:powershell(?:\.exe)?|pwsh(?:\.exe)?|bash|sh)"?\s+(?:-lc|-c|-Command)\s+(.*)$',
                     command.strip(), re.IGNORECASE | re.DOTALL)
    inner = match.group(1).strip() if match else command.strip()
    if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in "'\"":
        quote, inner = inner[0], inner[1:-1]
        # Undo the shell's quoting of the same quote character inside.
        inner = inner.replace('\\"', '"') if quote == '"' else inner.replace("''", "'")
    return inner.splitlines()[0] if inner else ""
