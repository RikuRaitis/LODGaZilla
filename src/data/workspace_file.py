"""UMW (LODGaZilla workspace) files: a saved Navigator and Plot workspace.

A ``.umw`` file is UTF-8 JSON::

    {"format": "UMW", "version": 1, "application": "LODGaZilla", "application_version": "1.0.0",
     "saved": "...",
     "navigator": {...}, "workspace": {"rows": [...], ...}}

Files from 1.0.0-beta.2 and earlier also have a "plot_navigator" section with the Plot
navigator buttons; it is ignored, because those buttons are the user's preferences.

Values under a ``path`` or ``folder`` key are file locations. They are stored
relative to the workspace file when possible, so a workspace saved beside its
data still opens after the folder is moved or opened from another computer.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.version import APP_VERSION

UMW_FORMAT = "UMW"
UMW_VERSION = 1
UMW_SUFFIX = ".umw"
UMW_FILE_FILTER = "LODGaZilla workspace (*.umw)"
RECENT_WORKSPACE_LIMIT = 10
PATH_KEYS = frozenset({"path", "folder"})


class WorkspaceFileError(ValueError):
    """The file is not a readable UMW workspace."""


def _convert_paths(value: Any, convert) -> Any:  # type: ignore[no-untyped-def]
    if isinstance(value, dict):
        return {key: (convert(item) if key in PATH_KEYS and isinstance(item, str) and item
                      else _convert_paths(item, convert))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_convert_paths(item, convert) for item in value]
    return value


def _relative_path(path: str, base: Path) -> str:
    try:
        return Path(os.path.relpath(Path(path).resolve(), base)).as_posix()
    except ValueError:
        # Windows cannot make a relative path across drive letters.
        return Path(path).resolve().as_posix()


def _absolute_path(path: str, base: Path) -> str:
    return str((base / path).resolve())


def write_workspace_file(path: str | Path, state: dict[str, Any]) -> Path:
    """Write ``state`` atomically; an existing file is replaced only once the new one is complete."""
    path = Path(path)
    if path.suffix.lower() != UMW_SUFFIX:
        path = path.with_name(path.name + UMW_SUFFIX)
    base = path.parent.resolve()
    payload = {
        "format": UMW_FORMAT,
        "version": UMW_VERSION,
        "application": "LODGaZilla",
        # The version that saved the file, for support; reading ignores it.
        "application_version": APP_VERSION,
        "saved": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **_convert_paths(state, lambda item: _relative_path(item, base)),
    }
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=base)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, allow_nan=False)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def read_workspace_file(path: str | Path) -> dict[str, Any]:
    """Read a workspace, with every stored location made absolute again."""
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkspaceFileError(f"Cannot read {path.name}: {error}") from error
    if not isinstance(payload, dict) or payload.get("format") != UMW_FORMAT:
        raise WorkspaceFileError(f"{path.name} is not a LODGaZilla workspace file")
    version = payload.get("version")
    if not isinstance(version, int) or version > UMW_VERSION:
        saved_by = payload.get("application_version")
        newer = f"LODGaZilla {saved_by}" if isinstance(saved_by, str) and saved_by else "a newer version"
        raise WorkspaceFileError(f"{path.name} was saved by {newer} (UMW {version}); this is {APP_VERSION}")
    return _convert_paths(payload, lambda item: _absolute_path(item, path.parent.resolve()))


def updated_recent_workspaces(recent: list[str], path: str | Path) -> list[str]:
    """Put ``path`` first, without duplicates, keeping the ten most recent files."""
    first = str(Path(path).resolve())
    others = [item for item in recent if Path(item).resolve() != Path(first)]
    return [first, *others][:RECENT_WORKSPACE_LIMIT]
