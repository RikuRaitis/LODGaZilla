"""Spawned worker for network and Git operations; no Qt or plugin imports."""
import os
from pathlib import Path


def repository_job(start_event, results, operation, payload, root):
    if os.name != "nt":
        os.setsid()
    start_event.wait()
    try:
        from src.plugins.installation import candidate_status, stage_install
        from src.plugins.repositories import Candidate, search_sources
        root = Path(root)
        if operation == "check":
            candidates, errors = search_sources(payload)
            rows = [(candidate, *candidate_status(candidate, root)) for candidate in candidates]
            results.put((True, (rows, errors)))
        else:
            stage_install(Candidate(**payload), root)
            results.put((True, payload["plugin_id"]))
    except Exception as error:
        results.put((False, str(error)))
