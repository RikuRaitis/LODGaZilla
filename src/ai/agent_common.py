"""What the AI agent CLIs share: their events and the environment they run in. No Qt."""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class AgentEvent:
    """One step of an agent run, independent of the CLI's stream format."""
    kind: str  # "text", "tool", "result" or "error"
    text: str = ""
    session_id: str = ""
    cost_usd: float | None = None


# Git hosting tokens the plugin updater reads; the agent has no use for them. The CLIs' own
# sign-in variables (such as ANTHROPIC_API_KEY or OPENAI_API_KEY) are kept.
WITHHELD_VARIABLES = ("GH_TOKEN", "GITHUB_TOKEN", "BITBUCKET_ACCESS_TOKEN", "GIT_ASKPASS", "SSH_ASKPASS")


def agent_environment() -> dict[str, str]:
    """``python`` in the agent's shell is this Python, and it can import LODGaZilla's ``src``."""
    environment = {name: value for name, value in os.environ.items()
                   if name.upper() not in WITHHELD_VARIABLES}
    root = str(Path(__file__).resolve().parents[2])
    environment["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), environment.get("PATH", "")])
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, [root, environment.get("PYTHONPATH", "")]))
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment
