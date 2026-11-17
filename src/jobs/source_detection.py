"""Find each folder's Source data rows: what was imported or exported, and what could be.

Runs in the Navigator's background snapshot job. Recorded rows come from the folder's
``_info.json`` (``FolderInfoData.sources``); files without a record are matched to an
H5 file LODGaZilla wrote from them, or else offered to the enabled importer plugins'
``can_parse()``. Export operations with ``inputs`` patterns get a row when a file
matches them; without patterns, when the folder has H5 files.
"""
from __future__ import annotations

import fnmatch
import logging
import threading
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Callable, Iterable

from src.data.folder_info import (
    EXPORT_SOURCE_NAME,
    SOURCE_EXPORT,
    SOURCE_IMPORT,
    FolderInfoData,
    attachment_path,
    is_info_file,
)
from src.data.media import AUDIO_SUFFIX
from src.jobs.cache_files import is_cache_output

# A source this much newer than its output makes the output outdated; file systems
# such as FAT store modification times in 2-second steps.
OUTDATED_TOLERANCE_NS = 2_000_000_000
CACHE_TOOL_NAME = "universal import and plotting tool"  # src.jobs.import_data.CACHE_TOOL_NAME

STATE_NEW = "new"            # can be imported or exported; not done yet
STATE_CURRENT = "current"    # done, and the output is newer than its source
STATE_OUTDATED = "outdated"  # done, but a source changed after the output was written
STATE_MISSING = "missing"    # recorded, but the output file is gone

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExportSpec:
    """An enabled plugin's export operation offered in the Source data group."""
    plugin_id: str
    plugin: str
    operation: str
    inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceDetection:
    """What the snapshot job needs to find Source data rows; built on the GUI thread."""
    enabled_plugins: tuple[str, ...] = ()
    exports: tuple[ExportSpec, ...] = ()


@dataclass(frozen=True)
class SourceRow:
    """One row of a folder's Source data group."""
    kind: str                  # SOURCE_IMPORT or SOURCE_EXPORT
    source: str                # the Source column: a file name, or "LODGaZilla" for an export
    output: str                # the output file name; empty until done
    plugin: str                # the Plugin column
    plugin_id: str
    state: str
    operation: str = ""
    inputs: tuple[str, ...] = ()  # an export's source files, as names in the folder
    draft: str = ""            # a Magic drop's parser folder
    recorded: bool = False     # whether _info.json has it

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.kind, self.plugin_id, self.source.casefold(), self.output.casefold()


def folder_files(folder: Path) -> dict[str, int]:
    """The folder's own files (no subfolders or hidden files), name -> modification time (ns)."""
    files: dict[str, int] = {}
    try:
        entries = list(folder.iterdir())
    except OSError:
        return files
    for path in entries:
        if path.name.startswith("."):
            continue
        try:
            if path.is_file():
                files[path.name] = path.stat().st_mtime_ns
        except OSError:
            continue
    return files


def _state(output_time: int | None, source_times: Iterable[int | None]) -> str:
    if output_time is None:
        return STATE_MISSING
    newest = max((time for time in source_times if time is not None), default=None)
    if newest is not None and newest > output_time + OUTDATED_TOLERANCE_NS:
        return STATE_OUTDATED
    return STATE_CURRENT


def _name_key(names: Iterable[str]) -> dict[str, str]:
    return {name.casefold(): name for name in names}


def folder_source_rows(
    folder: Path,
    info: FolderInfoData | None,
    files: dict[str, int],
    *,
    detect_importer: Callable[[Path], tuple[str, str] | None],
    h5_origin: Callable[[Path], dict[str, str]],
    plugin_titles: Callable[[str, str], tuple[str, str]] = lambda config_file, config_name: ("", config_name),
    exports: Iterable[ExportSpec] = (),
) -> list[SourceRow]:
    """The Source data rows of one folder.

    ``files`` is ``folder_files(folder)``. ``detect_importer(path)`` returns the plugin
    ID and title of the importer that reads a file, or None. ``h5_origin(h5_path)`` gives
    an H5 file's origin attributes; ``plugin_titles(config_file, config_name)`` turns them
    into a plugin ID and title.
    """
    rows: list[SourceRow] = []
    by_name = _name_key(files)

    def mtime(name: str) -> int | None:
        actual = by_name.get(name.casefold())
        return files[actual] if actual is not None else None

    recorded = list(info.sources) if info is not None else []
    h5_names = [name for name in files if Path(name).suffix.lower() in {".h5", ".hdf5"}]
    # Attachments and their converted audio belong to the Media group; recorded export
    # outputs are LODGaZilla's own files, not data to import.
    excluded: set[str] = set()
    if info is not None:
        for entry in [*info.videos, *info.images, *info.audio]:
            for key in ("path", "source"):
                if entry.get(key):
                    try:
                        linked = attachment_path(folder, {"path": entry[key]})
                    except (OSError, ValueError):
                        continue
                    if linked.parent == folder.resolve():
                        excluded.add(linked.name.casefold())
    excluded.update(entry.get("output", "").casefold() for entry in recorded if entry.get("kind") == SOURCE_EXPORT)

    # Recorded imports.
    imported_sources: set[str] = set()
    for entry in recorded:
        if entry.get("kind") != SOURCE_IMPORT:
            continue
        source, output = entry.get("source", ""), entry.get("output", "")
        imported_sources.add(source.casefold())
        rows.append(SourceRow(SOURCE_IMPORT, source, output, entry.get("plugin", ""), entry.get("plugin_id", ""),
                              _state(mtime(output), [mtime(source)]), draft=entry.get("draft", ""),
                              recorded=True))

    # Files without a record: an H5 file LODGaZilla wrote from them, or an importer that reads them.
    origins_by_source: dict[str, tuple[str, dict[str, str]]] = {}
    for h5_name in h5_names:
        origin = h5_origin(folder / h5_name)
        if origin.get("tool") == CACHE_TOOL_NAME and origin.get("source_file"):
            origins_by_source[PureWindowsPath(origin["source_file"]).name.casefold()] = (h5_name, origin)
    for name in sorted(files, key=str.casefold):
        key = name.casefold()
        path = folder / name
        if (key in imported_sources or key in excluded or name.endswith(AUDIO_SUFFIX)
                or is_cache_output(path) or is_info_file(path) or _is_info_work_file(folder, name)):
            continue
        if key in origins_by_source:
            h5_name, origin = origins_by_source[key]
            plugin_id, title = plugin_titles(origin.get("config_file", ""), origin.get("config_name", ""))
            rows.append(SourceRow(SOURCE_IMPORT, name, h5_name, title, plugin_id,
                                  _state(files[h5_name], [files[name]])))
            continue
        importer = detect_importer(path)
        if importer is not None:
            rows.append(SourceRow(SOURCE_IMPORT, name, "", importer[1], importer[0], STATE_NEW))

    # Exports: recorded ones, and a new row for each enabled export with input files no record covers.
    exported_inputs: dict[tuple[str, str], set[str]] = {}
    for entry in recorded:
        if entry.get("kind") != SOURCE_EXPORT:
            continue
        inputs = tuple(name for name in entry.get("inputs", "").split("|") if name)
        output = entry.get("output", "")
        source_times = [mtime(name) for name in inputs] if inputs else [files[name] for name in h5_names]
        plugin_id, operation = entry.get("plugin_id", ""), entry.get("operation", "")
        exported_inputs.setdefault((plugin_id, operation), set()).update(name.casefold() for name in inputs)
        rows.append(SourceRow(SOURCE_EXPORT, EXPORT_SOURCE_NAME, output, entry.get("plugin", ""), plugin_id,
                              _state(mtime(output), source_times), operation=operation, inputs=inputs,
                              recorded=True))
    for spec in exports:
        covered = exported_inputs.get((spec.plugin_id, spec.operation))
        if spec.inputs:
            matched = [name for name in sorted(files, key=str.casefold)
                       if name.casefold() not in excluded
                       and any(fnmatch.fnmatch(name.casefold(), pattern.casefold()) for pattern in spec.inputs)]
            # An older record without inputs covers the whole folder.
            if covered is not None and not covered:
                continue
            pending = tuple(name for name in matched if covered is None or name.casefold() not in covered)
            if pending:
                rows.append(SourceRow(SOURCE_EXPORT, EXPORT_SOURCE_NAME, "", spec.plugin, spec.plugin_id, STATE_NEW,
                                      operation=spec.operation, inputs=pending))
        elif h5_names and covered is None:
            rows.append(SourceRow(SOURCE_EXPORT, EXPORT_SOURCE_NAME, "", spec.plugin, spec.plugin_id, STATE_NEW,
                                  operation=spec.operation))
    return rows


def _is_info_work_file(folder: Path, name: str) -> bool:
    # The info file's lock and atomic-write temp files (is_cache_output covers most of them).
    return name.startswith(f"{folder.name}_info.json")


class ImporterDetector:
    """Ask the enabled importer plugins which one reads a file; answers are remembered.

    Loading the importers imports plugin code, so it happens once per set of enabled
    plugins, in the background job that first needs it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._enabled: tuple[str, ...] | None = None
        self._configurations: list = []
        self._identities: dict[str, tuple[str, str]] = {}
        self._answers: dict[tuple[str, int, int], tuple[str, str] | None] = {}

    def _load(self, enabled: tuple[str, ...]) -> None:
        if self._enabled == enabled:
            return
        self._enabled, self._configurations, self._identities, self._answers = enabled, [], {}, {}
        if not enabled:
            return
        from src.jobs.import_data import DEFAULT_CONFIG_DIR, importer_plugin_identity, load_import_configurations
        try:
            self._configurations = load_import_configurations(
                DEFAULT_CONFIG_DIR, enabled_plugins={plugin_id: True for plugin_id in enabled}, hdf5_settings={})
        except Exception as error:  # noqa: BLE001 - detection is optional; the Navigator still works.
            _log.warning("Could not load the importer plugins for Source data: %s", error)
            self._configurations = []
        self._identities = {str(configuration.path): importer_plugin_identity(configuration.path)
                            for configuration in self._configurations}

    def importer_for(self, path: Path, enabled: tuple[str, ...]) -> tuple[str, str] | None:
        """The (plugin ID, title) of the importer that reads ``path``, or None."""
        try:
            stat = path.stat()
        except OSError:
            return None
        with self._lock:
            self._load(enabled)
            key = (str(path), stat.st_size, stat.st_mtime_ns)
            if key in self._answers:
                return self._answers[key]
            from src.jobs.import_data import select_importer
            try:
                configuration, _parser = select_importer(path, self._configurations)
            except (OSError, ValueError):
                answer = None
            except Exception:  # noqa: BLE001 - a plugin's can_parse() must not stop the scan.
                answer = None
            else:
                answer = self._identities.get(str(configuration.path))
            self._answers[key] = answer
            return answer


IMPORTER_DETECTOR = ImporterDetector()


def plugin_identity_from_origin(config_file: str, config_name: str) -> tuple[str, str]:
    """An H5 file's importer plugin (ID, title) from its ``config_file`` attribute."""
    if config_file:
        from src.jobs.import_data import importer_plugin_identity
        path = Path(config_file)
        if (path.parent / "plugin.json").is_file():
            return importer_plugin_identity(path)
    return "", config_name
