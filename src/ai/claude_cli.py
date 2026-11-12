"""The installed Claude Code CLI, driven as a background process. No Qt.

LODGaZilla never handles credentials: the CLI signs in with its own browser flow
and keeps its own login. LODGaZilla only finds the CLI, asks whether it is signed in,
starts its sign-in, and runs it non-interactively with a fixed set of allowed tools.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

# Shared with the Codex CLI; re-exported for existing callers.
from src.ai.agent_common import NO_WINDOW, AgentEvent, agent_environment  # noqa: F401

EXECUTABLE = "claude.exe" if os.name == "nt" else "claude"
# Where the VS Code extension ships the CLI when it is not on PATH.
VSCODE_EXTENSIONS = Path.home() / ".vscode" / "extensions"

# Tools a Magic drop agent may use. Reading is allowed anywhere it is given access;
# writing only in its working folder (the draft). Python runs its inspection and the
# trial import; on Windows the CLI's shell tool is PowerShell, elsewhere Bash. Every
# other tool and command is denied, because a -p run cannot ask.
MAGIC_DROP_TOOLS = ("Read", "Glob", "Grep", "Write(./**)", "Edit(./**)", "Bash(python:*)", "PowerShell(python:*)")
# Create plugin also runs Git in the plugin's own repository.
PLUGIN_TOOLS = MAGIC_DROP_TOOLS + ("Bash(git:*)", "PowerShell(git:*)")
# Reading web pages, such as file format specifications; allowed unless switched off in Settings.
WEB_TOOLS = ("WebFetch", "WebSearch")


def find_claude(configured: str | Path | None = None) -> Path | None:
    """The Claude Code CLI: the configured path, then PATH, then the newest VS Code extension."""
    if configured and Path(configured).is_file():
        return Path(configured)
    on_path = shutil.which("claude")
    if on_path:
        return Path(on_path)
    candidates = sorted(VSCODE_EXTENSIONS.glob(f"anthropic.claude-code-*/resources/native-binary/{EXECUTABLE}"),
                        key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def is_signed_in(executable: Path, *, timeout: float = 30.0) -> bool:
    """Whether the CLI reports a login; any failure counts as not signed in."""
    try:
        completed = subprocess.run([str(executable), "auth", "status", "--json"], capture_output=True, text=True,
                                   timeout=timeout, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
        return bool(json.loads(completed.stdout or "{}").get("loggedIn"))
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return False


def start_sign_in(executable: Path) -> subprocess.Popen:
    """Open the CLI's own sign-in in a visible console; it opens the browser."""
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return subprocess.Popen([str(executable), "auth", "login"], creationflags=flags)


def agent_command(executable: Path, folders: list[Path], *, tools: tuple[str, ...], max_budget_usd: float,
                  web: bool = False, resume: str = "") -> list[str]:
    """A non-interactive run in the current folder; the prompt is sent on stdin.

    ``resume`` continues an earlier session, so the agent remembers the conversation.
    """
    command = [str(executable), "-p", "--output-format", "stream-json", "--verbose",
               # dontAsk: a tool that is not allowed below is denied, never asked about.
               "--permission-mode", "dontAsk", "--strict-mcp-config",
               "--max-budget-usd", f"{max_budget_usd:g}", "--allowedTools", *tools, *(WEB_TOOLS if web else ())]
    if resume:
        command += ["--resume", resume]
    for folder in folders:
        command += ["--add-dir", str(folder)]
    return command


def magic_drop_command(executable: Path, read_folders: list[Path], *, max_budget_usd: float,
                       web: bool = False) -> list[str]:
    """Magic drop: read, write in the draft and run Python (and read web pages when allowed)."""
    return agent_command(executable, read_folders, tools=MAGIC_DROP_TOOLS, max_budget_usd=max_budget_usd, web=web)


def plugin_command(executable: Path, read_folders: list[Path], *, max_budget_usd: float, web: bool = False,
                   resume: str = "") -> list[str]:
    """Create plugin: as Magic drop, plus Git in the plugin's own repository."""
    return agent_command(executable, read_folders, tools=PLUGIN_TOOLS, max_budget_usd=max_budget_usd, web=web,
                         resume=resume)


def parse_stream_line(line: str) -> list[AgentEvent]:
    """Read one stream-json line; unknown or malformed lines give no events."""
    try:
        record = json.loads(line)
    except ValueError:
        return []
    if not isinstance(record, dict):
        return []
    kind = record.get("type")
    if kind == "assistant":
        events = []
        for block in (record.get("message") or {}).get("content") or []:
            if block.get("type") == "text" and block.get("text", "").strip():
                events.append(AgentEvent("text", block["text"].strip()))
            elif block.get("type") == "tool_use":
                events.append(AgentEvent("tool", describe_tool(block.get("name", ""), block.get("input") or {})))
        return events
    if kind == "result":
        failed = bool(record.get("is_error")) or record.get("subtype") not in (None, "success")
        text = str(record.get("result") or record.get("subtype") or "")
        return [AgentEvent("error" if failed else "result", text, str(record.get("session_id") or ""),
                           record.get("total_cost_usd"))]
    return []


def describe_tool(name: str, arguments: dict) -> str:
    """A one-line summary of a tool call for the AI conversation."""
    if name in ("Read", "Write", "Edit"):
        return f"{name} {Path(str(arguments.get('file_path', ''))).name}"
    if name in ("Bash", "PowerShell"):
        command = str(arguments.get("description") or arguments.get("command") or "")
        return f"Ran: {command.splitlines()[0][:120] if command else ''}"
    if name == "WebFetch":
        return f"Fetched {str(arguments.get('url', ''))[:120]}"
    if name == "WebSearch":
        return f"Searched the web: {str(arguments.get('query', ''))[:120]}"
    return name
