"""Shared resource limits for host and plugin background work."""
import os

MAX_IMPORT_PROCESSES = 8
PROGRESS_TOTAL = 1000


def configured_import_worker_count(job_count: int, process_limit: int = MAX_IMPORT_PROCESSES) -> int:
    if job_count <= 0:
        return 0
    return max(1, min(job_count, os.cpu_count() or 1, max(1, process_limit), MAX_IMPORT_PROCESSES))
