"""Manifest-only discovery shared by menus, imports and dependency setup."""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib.metadata
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
from typing import Callable


from src.contracts.plugin import PLUGIN_API_VERSION

PLUGIN_ROOT = Path(os.environ.get("LODGAZILLA_PLUGIN_ROOT", Path(__file__).resolve().parents[2] / "plugin"))
# Set by `python -m src.app.lodgazilla --no-plugins`: the application loads no plugin.
NO_PLUGINS_ENV = "LODGAZILLA_NO_PLUGINS"


def plugins_disabled() -> bool:
    return os.environ.get(NO_PLUGINS_ENV) == "1"


# Operation kinds and scopes a manifest may declare; the host builds its menus from them.
OPERATION_KINDS = ("import", "export")
OPERATION_SCOPES = ("folder", "batch")


@dataclass(frozen=True)
class PluginOperation:
    """A registered background job and where the host offers it.

    ``kind`` and ``scopes`` are empty for the plain ``"worker.py:run"`` form, which
    the plugin starts itself; the host offers only operations that declare them.
    ``folder`` is one Navigator folder; ``batch`` several folders or the data folder.
    """
    name: str
    worker: str
    kind: str = ""
    title: str = ""
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PluginLauncher:
    """A plugin window the user opens from its icon in the Plot navigator."""
    title: str
    icon: Path | None = None


@dataclass(frozen=True)
class PluginDescriptor:
    plugin_id: str
    title: str
    path: Path
    entry_point: Path
    capabilities: tuple[str, ...]
    version: str
    # Operation name -> "worker.py:function", for submitting jobs.
    operations: dict[str, str] = field(default_factory=dict)
    error: str = ""
    plugin_class: type | None = None
    operation_specs: tuple[PluginOperation, ...] = ()
    launcher: PluginLauncher | None = None


def local_file(folder: Path, name: str) -> Path:
    path = (folder / name).resolve()
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        raise ValueError(f"Missing or invalid plugin file: {name}")
    return path


def requirement_errors(path: Path) -> list[str]:
    from packaging.requirements import Requirement
    errors = []
    if not path.is_file():
        return errors
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.url:
            raise ValueError("Plugin dependencies must use named package requirements")
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            version = importlib.metadata.version(requirement.name)
        except importlib.metadata.PackageNotFoundError:
            errors.append(f"Missing dependency: {requirement}")
        else:
            if version not in requirement.specifier:
                errors.append(f"{requirement} required; installed {version}")
    return errors


def validate_manifest(manifest: dict) -> str:
    """Validate local and remote metadata without executing plugin code.

    Malformed manifests raise ValueError; a valid but unsupported API returns a
    compatibility message so the UI can explain why installation is unavailable.
    """
    if not isinstance(manifest, dict):
        raise ValueError("Plugin manifest must be an object")
    plugin_id = manifest.get("id")
    if not isinstance(plugin_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", plugin_id):
        raise ValueError("Plugin id must use lowercase letters, digits and underscores")
    title = manifest.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Plugin title must not be empty")
    capabilities = manifest.get("capabilities")
    if (not isinstance(capabilities, list) or not capabilities
            or any(cap not in ("importer", "ui") for cap in capabilities)):
        raise ValueError("Unknown plugin capability")
    def filename(value):
        if (not isinstance(value, str) or not value or "\\" in value or ":" in value
                or PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts):
            raise ValueError(f"Missing or invalid plugin file: {value}")
    filename(manifest.get("entry_point"))
    operations = manifest.get("operations", {})
    if not isinstance(operations, dict):
        raise ValueError("operations must be a mapping")
    for name, value in operations.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Invalid operation entry point")
        if isinstance(value, dict):
            if value.get("kind") not in OPERATION_KINDS:
                raise ValueError(f"Operation {name}: kind must be one of {', '.join(OPERATION_KINDS)}")
            scopes = value.get("scopes")
            if (not isinstance(scopes, list) or not scopes
                    or any(scope not in OPERATION_SCOPES for scope in scopes)):
                raise ValueError(f"Operation {name}: scopes must list {' and/or '.join(OPERATION_SCOPES)}")
            title = value.get("title", name)
            if not isinstance(title, str) or not title.strip():
                raise ValueError(f"Operation {name}: title must not be empty")
            value = value.get("worker")
        if not isinstance(value, str) or value.count(":") != 1:
            raise ValueError("Invalid operation entry point")
        file, function = value.split(":")
        filename(file)
        if PurePosixPath(file).name != file or not file.endswith(".py") or not function.isidentifier():
            raise ValueError("Invalid worker entry point")
    launcher = manifest.get("launcher")
    if launcher is not None:
        if not isinstance(launcher, dict):
            raise ValueError("launcher must be an object")
        title = launcher.get("title", manifest.get("title"))
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Launcher title must not be empty")
        if "icon" in launcher:
            filename(launcher["icon"])
            if not str(launcher["icon"]).lower().endswith(".svg"):
                raise ValueError("Launcher icon must be an SVG file")
    if manifest.get("api_version") != PLUGIN_API_VERSION:
        return f"Unsupported plugin API {manifest.get('api_version')}; host supports {PLUGIN_API_VERSION}"
    return ""


def parse_operations(operations: dict) -> tuple[PluginOperation, ...]:
    """Read a validated ``operations`` mapping; the plain string form declares only a worker."""
    specs = []
    for name, value in operations.items():
        if isinstance(value, dict):
            specs.append(PluginOperation(name, value["worker"], value["kind"], str(value.get("title", name)),
                                         tuple(value["scopes"])))
        else:
            specs.append(PluginOperation(name, value))
    return tuple(specs)


def discover_plugins(plugin_root: str | Path = PLUGIN_ROOT, *, log: Callable[[str], None] | None = None,
                     check_dependencies: bool = True) -> list[PluginDescriptor]:
    descriptors = []
    known = set()
    for path in sorted(Path(plugin_root).glob("*/plugin.json")):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            compatibility = validate_manifest(manifest)
            plugin_id, title = manifest["id"], manifest["title"]
            if plugin_id in known:
                raise ValueError(f"Duplicate plugin id: {plugin_id}")
            capabilities = tuple(manifest["capabilities"])
            entry = local_file(path.parent, manifest["entry_point"])
            errors = [compatibility] if compatibility else []
            if "importer" in capabilities:
                local_file(path.parent, "importer.json")
            specs = parse_operations(manifest.get("operations", {}))
            for spec in specs:
                filename, function = spec.worker.split(":")
                worker = local_file(path.parent, filename)
                if worker.parent != path.parent.resolve() or worker.suffix != ".py" or not function.isidentifier():
                    raise ValueError(f"Invalid worker entry point for {spec.name}")
            launcher = None
            if "launcher" in manifest:
                icon = manifest["launcher"].get("icon")
                launcher = PluginLauncher(str(manifest["launcher"].get("title", title)),
                                          local_file(path.parent, icon) if icon else None)
            if check_dependencies:
                errors.extend(requirement_errors(path.parent / "requirements.txt"))
            descriptor = PluginDescriptor(plugin_id, title, path, entry, capabilities,
                                          str(manifest.get("version", "0.1.0")),
                                          {spec.name: spec.worker for spec in specs}, "; ".join(errors),
                                          operation_specs=specs, launcher=launcher)
            descriptors.append(descriptor)
            known.add(plugin_id)
            if descriptor.error and log:
                log(f"Plugin {title} unavailable: {descriptor.error}")
        except (OSError, ValueError, KeyError, TypeError) as error:
            if log:
                log(f"Plugin manifest failed for {path}: {type(error).__name__}: {error}")
    return descriptors
