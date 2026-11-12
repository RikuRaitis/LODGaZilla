"""Spawn-safe entry point for operations registered by installed plugins."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys


def run_plugin_process(request, events, cancel_event=None, start_event=None) -> None:
    if start_event is not None and not start_event.wait(30):
        return
    try:
        path = Path(request.entry_point).resolve()
        # A real importable package name lets nested spawned pool workers import
        # plugin functions. Child processes inherit this search path.
        sys.path.insert(0, str(path.parent.parent))
        module = importlib.import_module(f"{path.parent.name}.{path.stem}")
        if Path(module.__file__).resolve() != path:
            raise ImportError("Plugin worker module resolves to a different installation")
        getattr(module, request.function)(request, events, cancel_event)
    except Exception as error:
        events.put(("finished", 0, 0, str(error)))
