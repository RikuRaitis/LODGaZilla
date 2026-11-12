"""Framework-free scheduling policy for background H5 and plugin jobs."""

from __future__ import annotations

import multiprocessing
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from src.contracts.jobs import PluginJobRequest


@dataclass(frozen=True)
class ImportRequest:
    folder: Path
    recursive: bool
    process_slots: int


@dataclass
class ActiveImport:
    folder: Path
    recursive: bool
    process_slots: int
    process: multiprocessing.Process
    events: multiprocessing.Queue
    operation: str = "H5 cache"
    # A multiprocessing Event that asks the coordinator to stop cooperatively.
    cancel_event: Any = None
    process_tree: Any = None
    plugin_id: str = ""


JobRequest = ImportRequest | PluginJobRequest


def path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def import_scopes_overlap(first_folder: Path, first_recursive: bool, second_folder: Path, second_recursive: bool) -> bool:
    first, second = first_folder.resolve(), second_folder.resolve()
    return first == second or (first_recursive and path_is_relative_to(second, first)) or (
        second_recursive and path_is_relative_to(first, second)
    )


def request_operation(request: JobRequest) -> str:
    # Plugin and audio jobs name their operation; folder imports create H5 caches.
    return getattr(request, "operation", "H5 cache")


def request_conflicts_with_active(request: JobRequest, active: ActiveImport) -> bool:
    """Only equal output types conflict when their folder scopes overlap."""
    return (
        request_operation(request) == active.operation
        and getattr(request, "plugin_id", "") == active.plugin_id
        and import_scopes_overlap(request.folder, request.recursive, active.folder, active.recursive)
    )


class ImportJobCoordinator:
    """Own queue/slot/conflict decisions independently of Qt and job progress UI."""

    def __init__(self, maximum_processes: int) -> None:
        self.maximum_processes = max(1, int(maximum_processes))
        self.active_imports: dict[int, ActiveImport] = {}
        self.queue: deque[JobRequest] = deque()
        self._next_import_id = 1

    def set_maximum_processes(self, maximum_processes: int) -> None:
        self.maximum_processes = max(1, int(maximum_processes))

    def reserve_id(self) -> int:
        import_id = self._next_import_id
        self._next_import_id += 1
        return import_id

    def register_active(self, import_id: int, active: ActiveImport) -> None:
        self.active_imports[import_id] = active

    def finish(self, import_id: int) -> ActiveImport | None:
        return self.active_imports.pop(import_id, None)

    def available_slots(self) -> int:
        return max(0, self.maximum_processes - sum(job.process_slots for job in self.active_imports.values()))

    def has_conflict(self, request: JobRequest) -> bool:
        return any(request_conflicts_with_active(request, active) for active in self.active_imports.values())

    def can_start(self, request: JobRequest) -> bool:
        # A request queued before the maximum was lowered still starts, with what is there.
        return not self.has_conflict(request) and self.available_slots() >= min(request.process_slots,
                                                                                self.maximum_processes)

    def enqueue(self, request: JobRequest) -> None:
        self.queue.append(request)

    def pop_next_runnable(self) -> JobRequest | None:
        """Return the first runnable request without starving independent work."""
        for index, request in enumerate(self.queue):
            if self.can_start(request):
                del self.queue[index]
                return request
        return None

    def clear_queue(self) -> None:
        self.queue.clear()
