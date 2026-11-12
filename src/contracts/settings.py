"""JSON settings helpers for plugins; local values stay outside source control."""
import json
import os
from pathlib import Path
import tempfile


def load_plugin_settings(folder: Path, defaults: dict) -> dict:
    folder = Path(folder)
    path = folder / "settings.local.json"
    if not path.exists():
        path = folder / "settings.json"
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(defaults)
    return {**defaults, **values} if isinstance(values, dict) else dict(defaults)


def save_plugin_settings(folder: Path, values: dict) -> None:
    folder = Path(folder)
    descriptor, name = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=folder)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(values, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, folder / "settings.local.json")
    finally:
        temporary.unlink(missing_ok=True)
