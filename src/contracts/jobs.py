"""Serializable plugin job requests and shared worker/progress helpers.

Workers receive (request, events, cancel_event). Events use the host's existing
message, process_started/progress/eta/finished, and finished tuple protocol.
Plugin-specific events use ('plugin_event', name, payload).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.jobs.policy import MAX_IMPORT_PROCESSES, PROGRESS_TOTAL, configured_import_worker_count
from src.jobs.parallel_jobs import run_process_jobs


@dataclass(frozen=True)
class PluginJobRequest:
    folder: Path
    recursive: bool
    process_slots: int
    plugin_id: str = ""
    operation: str = "Plugin operation"
    entry_point: str = ""
    function: str = "run"
    parameters: dict[str, Any] = field(default_factory=dict)
