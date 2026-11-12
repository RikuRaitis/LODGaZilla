"""The AI agents Magic drop can use, chosen with the AI panel's Provider selector. No Qt."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.ai import claude_cli, codex_cli


@dataclass(frozen=True)
class AgentProvider:
    name: str  # As in the Provider selector, such as "Claude".
    vendor: str  # Who receives the files, for the consent question.
    product: str  # The CLI to install, for messages.
    instructions_file: str  # The file in the draft the CLI reads its instructions from.
    path_setting: str  # Its CLI path in the "ai" settings section.
    consent_setting: str  # Its Magic drop consent in the "ai" settings section.
    web_setting: str  # Whether it may read web pages, in the "ai" settings section.
    has_cost_limit: bool
    find: Callable[[Any], Path | None]
    is_signed_in: Callable[[Path], bool]
    start_sign_in: Callable[[Path], Any]
    magic_drop_command: Callable[..., list[str]]
    plugin_command: Callable[..., list[str]]
    parse_stream_line: Callable[[str], list]


CLAUDE = AgentProvider(
    name="Claude", vendor="Anthropic", product="Claude Code", instructions_file="CLAUDE.md",
    path_setting="claude_path", consent_setting="magic_drop_consent", web_setting="claude_web_access",
    has_cost_limit=True,
    find=claude_cli.find_claude, is_signed_in=claude_cli.is_signed_in, start_sign_in=claude_cli.start_sign_in,
    magic_drop_command=claude_cli.magic_drop_command, plugin_command=claude_cli.plugin_command,
    parse_stream_line=claude_cli.parse_stream_line,
)
CODEX = AgentProvider(
    name="Codex", vendor="OpenAI", product="Codex", instructions_file=codex_cli.INSTRUCTIONS_FILE,
    path_setting="codex_path", consent_setting="codex_magic_drop_consent", web_setting="codex_web_access",
    has_cost_limit=False,
    find=codex_cli.find_codex, is_signed_in=codex_cli.is_signed_in, start_sign_in=codex_cli.start_sign_in,
    magic_drop_command=codex_cli.magic_drop_command, plugin_command=codex_cli.plugin_command,
    parse_stream_line=codex_cli.parse_stream_line,
)
PROVIDERS = {provider.name: provider for provider in (CLAUDE, CODEX)}


def provider_named(name: str) -> AgentProvider:
    """The provider of that name; Claude for an unknown one."""
    return PROVIDERS.get(name, CLAUDE)
