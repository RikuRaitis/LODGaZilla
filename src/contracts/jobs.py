"""Serializable plugin job requests and shared worker/progress helpers.

Workers receive (request, events, cancel_event). Events use the host's existing
message, process_started/progress/eta/finished, and finished tuple protocol.
Plugin-specific events use ('plugin_event', name, payload). An export reports each
file it wrote with ``report_source_output``, for the folder's Source data group.

A run started from a Source data row passes ``parameters["source_files"]``: the
paths of the files to export. Without it, export what the folder holds.
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


SOURCE_FILES_PARAMETER = "source_files"
SOURCE_OUTPUT_EVENT = "source_output"


def report_source_output(events, output: str | Path, inputs=()) -> None:  # type: ignore[no-untyped-def]
    """Tell the host an export wrote ``output`` from ``inputs`` (the source files it read).

    The host records it in the folder's Source data group, where it is marked outdated
    when an input changes after the output was written.
    """
    events.put((SOURCE_OUTPUT_EVENT, str(output), [str(path) for path in inputs]))
