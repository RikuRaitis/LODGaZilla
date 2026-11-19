"""Build the Navigator's disk-backed snapshot without blocking the GUI thread."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from src.data.folder_info import FolderInfoData, attachment_path, info_file_path, read_existing_info_file
from src.data.navigator_data import (
    H5NavigatorData,
    ensure_folder_info_files,
    read_h5_navigator_data,
    resolved_path,
    scan_navigator_folder,
)
from src.jobs.source_detection import (
    IMPORTER_DETECTOR,
    SourceDetection,
    SourceRow,
    folder_files,
    folder_source_rows,
    plugin_identity_from_origin,
)


@dataclass(frozen=True)
class FileSignature:
    modified_ns: int
    size: int


@dataclass(frozen=True)
class FolderLinks:
    """A folder's attachment paths, resolved in the background read.

    The Navigator lists them without asking the disk on the GUI thread (on Windows each
    path resolve reads the disk). ``info`` is the FolderInfoData they were resolved from:
    after an edit replaces it, the Navigator resolves that folder's links itself.
    """

    info: object
    videos: tuple[Path, ...]
    images: tuple[Path, ...]
    models: tuple[Path, ...]
    # Linked audio H5 files and whether each exists.
    audio: tuple[tuple[Path, bool], ...]


def folder_links(folder: Path, info: FolderInfoData) -> FolderLinks:
    """Resolve a folder's video, image, model and audio links (disk access)."""

    def resolve(entries) -> tuple[Path, ...]:  # type: ignore[no-untyped-def]
        paths = []
        for entry in entries:
            try:
                paths.append(attachment_path(folder, entry))
            except (OSError, ValueError):
                paths.append(Path(folder) / entry.get("path", ""))
        return tuple(paths)

    audio = resolve(info.audio)
    return FolderLinks(info, resolve(info.videos), resolve(info.images), resolve(info.models),
                       tuple((path, path.is_file()) for path in audio))


@dataclass
class NavigatorSourceSnapshot:
    """Disk-backed navigator data, reused for UI-only tree rebuilds."""

    root: Path
    folders: list[Path]
    h5_files: list[Path]
    folder_infos: dict[Path, FolderInfoData | None]
    info_signatures: dict[Path, FileSignature | None]
    h5_data: dict[Path, H5NavigatorData]
    h5_signatures: dict[Path, FileSignature | None]
    # Each folder's Source data rows, found with ``detection``; ``source_signatures``
    # (its filenames and modification times) decides when to look again.
    source_rows: dict[Path, list[SourceRow]] = field(default_factory=dict)
    source_signatures: dict[Path, FileSignature | None] = field(default_factory=dict)
    detection: SourceDetection = field(default_factory=SourceDetection)
    # A first read's part: the folders not read completely yet (their Source data is
    # found last). Empty in a finished read.
    loading: frozenset[Path] = field(default_factory=frozenset)
    # Each folder's attachment paths, resolved off the GUI thread.
    links: dict[Path, FolderLinks] = field(default_factory=dict)

    def copy(self) -> "NavigatorSourceSnapshot":
        """Return a copy whose containers the GUI thread can keep mutating."""
        return NavigatorSourceSnapshot(
            self.root,
            list(self.folders),
            list(self.h5_files),
            dict(self.folder_infos),
            dict(self.info_signatures),
            dict(self.h5_data),
            dict(self.h5_signatures),
            dict(self.source_rows),
            dict(self.source_signatures),
            self.detection,
            self.loading,
            dict(self.links),
        )


@dataclass
class NavigatorSnapshotResult:
    snapshot: NavigatorSourceSnapshot
    # False when every folder, file and parsed entry was reused unchanged.
    changed: bool
    read_errors: dict[Path, str] = field(default_factory=dict)
    read_ok: set[Path] = field(default_factory=set)


# The phases of a folder read, as ``progress(done, total, phase)`` reports them: finding the
# folders, reading their info and H5 files, and finding their Source data (the importer
# plugins look at every file, the slowest part).
PHASE_FINDING = "finding"
PHASE_DATA = "data"
PHASE_SOURCES = "sources"


class SnapshotCancelled(Exception):
    """Raised inside the builder when a newer request supersedes it."""


def file_signature(path: Path) -> FileSignature | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return FileSignature(stat.st_mtime_ns, stat.st_size)


def _resolved_within(paths: list[Path], root: Path, keep: Callable[[Path], bool]) -> list[Path]:
    """Resolve paths, dropping duplicates, links leading outside root, and vanished paths."""
    resolved: dict[Path, None] = {}
    for path in paths:
        try:
            candidate = path.resolve()
        except OSError:
            continue
        if candidate == root or not candidate.is_relative_to(root) or not keep(candidate):
            continue
        resolved.setdefault(candidate)
    return list(resolved)


def build_navigator_snapshot(
    root: Path,
    previous: NavigatorSourceSnapshot | None,
    *,
    rescan: bool,
    cancelled: Callable[[], bool] = lambda: False,
    detection: SourceDetection | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    partial: Callable[[NavigatorSourceSnapshot], None] | None = None,
) -> NavigatorSnapshotResult:
    """Scan folders, read changed ``_info.json`` files and inspect changed H5 files.

    A refresh without rescan reuses the previous folder list, but skips folders
    that no longer exist and never creates metadata files, so a deleted or
    renamed folder cannot be recreated on disk.

    The read runs in phases: the folders are found (``progress(0, 0, PHASE_FINDING)``),
    every folder's info is read, then each folder's H5 files (``progress(done, total,
    PHASE_DATA)`` after each folder), and last each folder's Source data rows
    (``PHASE_SOURCES``). On a first read (no ``previous``), ``partial(snapshot)`` receives
    every folder found once found, again once their info is read and when the H5 files
    are read, and in between at most every ``PARTIAL_INTERVAL_SECONDS``; its ``loading``
    names the folders whose Source data is not found yet, so the tree can be shown, and
    filled in, while the rest is read.
    """

    def check_cancelled() -> None:
        if cancelled():
            raise SnapshotCancelled()

    report = progress or (lambda _done, _total, _phase: None)
    root = root.resolve()
    if previous is not None and previous.root != root:
        previous = None
    if previous is None or rescan:
        # Folders may have been renamed or linked differently: forget remembered resolves
        # here, off the GUI thread, which then finds the new ones already remembered.
        resolved_path.cache_clear()
    errors: dict[Path, str] = {}
    if previous is None or rescan:
        report(0, 0, PHASE_FINDING)
        scan = scan_navigator_folder(root)
        check_cancelled()
        folders = _resolved_within(scan.folders, root, Path.is_dir)
        h5_files = _resolved_within(scan.h5_files, root, Path.is_file)
        ensure_folder_info_files(
            folders, on_error=lambda folder, error: errors.__setitem__(folder.resolve(), error)
        )
    else:
        folders = _resolved_within(previous.folders, root, Path.is_dir)
        h5_files = _resolved_within(previous.h5_files, root, Path.is_file)
    check_cancelled()

    changed = previous is None or folders != previous.folders or h5_files != previous.h5_files
    previous_infos = previous.folder_infos if previous is not None else {}
    previous_info_signatures = previous.info_signatures if previous is not None else {}
    previous_h5 = previous.h5_data if previous is not None else {}
    previous_h5_signatures = previous.h5_signatures if previous is not None else {}
    detection = detection if detection is not None else (
        previous.detection if previous is not None else SourceDetection())
    reuse_sources = previous is not None and not rescan and previous.detection == detection
    folder_infos: dict[Path, FolderInfoData | None] = {}
    info_signatures: dict[Path, FileSignature | None] = {}
    links: dict[Path, FolderLinks] = {}
    previous_links = previous.links if previous is not None else {}
    h5_data: dict[Path, H5NavigatorData] = {}
    h5_signatures: dict[Path, FileSignature | None] = {}
    source_rows: dict[Path, list[SourceRow]] = {}
    source_signatures: dict[Path, FileSignature | None] = {}
    read_ok: set[Path] = set()

    def read_h5(h5_path: Path) -> None:
        nonlocal changed
        signature = file_signature(h5_path)
        h5_signatures[h5_path] = signature
        if h5_path in previous_h5 and previous_h5_signatures.get(h5_path) == signature:
            h5_data[h5_path] = previous_h5[h5_path]
        else:
            changed = True
            h5_data[h5_path] = read_h5_navigator_data(h5_path)

    h5_by_folder: dict[Path, list[Path]] = {}
    for h5_path in h5_files:
        h5_by_folder.setdefault(h5_path.parent, []).append(h5_path)
    total = len(folders)
    send_partial = partial if previous is None else None
    read_h5_files: list[Path] = []
    last_partial = time.monotonic()

    def send(loading) -> None:  # type: ignore[no-untyped-def]
        nonlocal last_partial
        last_partial = time.monotonic()
        # Copies: the GUI keeps this while the rest is read into the originals.
        send_partial(NavigatorSourceSnapshot(
            root, list(folders), list(read_h5_files), dict(folder_infos), dict(info_signatures), dict(h5_data),
            dict(h5_signatures), dict(source_rows), dict(source_signatures), detection, frozenset(loading),
            dict(links)))

    def due() -> bool:
        return send_partial is not None and time.monotonic() - last_partial >= PARTIAL_INTERVAL_SECONDS

    # The folder list first: every folder is shown, still loading.
    report(0, total, PHASE_DATA)
    if send_partial is not None and total:
        send(folders)
    # Then each folder's _info.json: small files, read for all folders at once.
    for folder in folders:
        check_cancelled()
        signature = file_signature(info_file_path(folder))
        info_signatures[folder] = signature
        if folder in previous_info_signatures and previous_info_signatures[folder] == signature:
            folder_infos[folder] = previous_infos.get(folder)
        else:
            changed = True
            try:
                folder_infos[folder] = read_existing_info_file(folder)
            except (OSError, ValueError) as error:
                folder_infos[folder] = None
                errors[folder] = str(error)
            else:
                read_ok.add(folder)
        # The folder's attachment paths and the paths the GUI looks up, resolved here.
        resolved_path(folder)
        info = folder_infos[folder]
        if info is not None:
            known = previous_links.get(folder)
            links[folder] = known if known is not None and known.info is info else folder_links(folder, info)
    for h5_path in h5_files:
        resolved_path(h5_path)
    if send_partial is not None and total:
        send(folders)
    # Then each folder's H5 files: its series appear as each folder is read.
    for done, folder in enumerate(folders, start=1):
        for h5_path in h5_by_folder.pop(folder, []):
            check_cancelled()
            read_h5(h5_path)
            read_h5_files.append(h5_path)
        report(done, total, PHASE_DATA)
        if done < total and due():
            send(folders)

    # H5 files outside the scanned folders (in the data folder itself), and linked
    # audio stored elsewhere; inspected here too, off the GUI thread.
    scanned = set(h5_files)
    linked_audio = [
        path for path in dict.fromkeys(
            attachment_path(folder, audio) for folder, info in folder_infos.items() if info is not None for audio in info.audio
        ) if path not in scanned and path.is_file()
    ]
    for h5_path in [*(path for paths in h5_by_folder.values() for path in paths), *linked_audio]:
        check_cancelled()
        read_h5(h5_path)
    # Last each folder's Source data: the importer plugins look at every file.
    loading = set(folders)
    if send_partial is not None and total:
        send(loading)
    report(0, total, PHASE_SOURCES)
    for done, folder in enumerate(folders, start=1):
        check_cancelled()
        rows, source_signatures[folder] = _folder_source_rows(
            folder, folder_infos[folder], info_signatures[folder], h5_data, previous, detection,
            reuse=reuse_sources)
        source_rows[folder] = rows
        if previous is None or previous.source_rows.get(folder) != rows:
            changed = True
        loading.discard(folder)
        report(done, total, PHASE_SOURCES)
        if done < total and due():
            send(loading)
    if previous is not None and set(previous.source_rows) != set(source_rows):
        changed = True

    snapshot = NavigatorSourceSnapshot(
        root, folders, h5_files, folder_infos, info_signatures, h5_data, h5_signatures,
        source_rows, source_signatures, detection, links=links,
    )
    return NavigatorSnapshotResult(snapshot, changed, errors, read_ok)


def _folder_source_rows(folder, info, info_signature, h5_data, previous, detection, *,
                        reuse):  # type: ignore[no-untyped-def]
    """One folder's Source data rows and its signature; looked at again on a rescan, or
    when its files, its _info.json or the enabled plugins changed."""
    files = folder_files(folder)
    # Folder timestamps do not change when an existing member/reference is edited.
    signature = FileSignature(hash(tuple(sorted(files.items()))), len(files))
    if (reuse and folder in previous.source_rows and previous.source_signatures.get(folder) == signature
            and previous.info_signatures.get(folder) == info_signature):
        return previous.source_rows[folder], signature
    rows = folder_source_rows(
        folder, info, files,
        detect_importer=lambda path: IMPORTER_DETECTOR.importer_for(path, detection.enabled_plugins),
        h5_origin=lambda path: (h5_data.get(path.resolve()) or H5NavigatorData([], None, None)).origin,
        plugin_titles=plugin_identity_from_origin,
        exports=detection.exports,
        import_sets=IMPORTER_DETECTOR.sets_for(folder, detection.enabled_plugins),
    )
    return rows, signature


PROGRESS_INTERVAL_SECONDS = 0.1
# On a data folder's first read, the folders read so far are shown this often.
PARTIAL_INTERVAL_SECONDS = 1.0


class _SnapshotSignals(QObject):
    completed = Signal(object)
    # Folders read and found, and the phase (PHASE_*); (0, 0) while finding them.
    progress = Signal(int, int, str)
    # A first read's folders so far (NavigatorSourceSnapshot).
    partial = Signal(object)


class _SnapshotTask(QRunnable):
    def __init__(self, root, previous, rescan, stop_event, detection=None):  # type: ignore[no-untyped-def]
        super().__init__()
        self.root = root
        self.previous = previous
        self.rescan = rescan
        self.stop_event = stop_event
        self.detection = detection
        self._last_progress = 0.0
        self.signals = _SnapshotSignals()

    def _report_progress(self, done: int, total: int, phase: str) -> None:
        # At most a few updates a second: every one crosses to the GUI thread.
        now = time.monotonic()
        if done in {0, total} or now - self._last_progress >= PROGRESS_INTERVAL_SECONDS:
            self._last_progress = now
            self.signals.progress.emit(done, total, phase)

    def run(self) -> None:
        try:
            result: object = build_navigator_snapshot(
                self.root, self.previous, rescan=self.rescan, cancelled=self.stop_event.is_set,
                detection=self.detection, progress=self._report_progress,
                partial=self.signals.partial.emit,
            )
        except SnapshotCancelled:
            result = None
        except Exception as error:  # noqa: BLE001 - reported on the GUI thread
            result = error
        self.signals.completed.emit(result)


class NavigatorSnapshotJob(QObject):
    """Build one snapshot in a worker, delivering it on the owner's GUI thread.

    The callback receives the job and either a ``NavigatorSnapshotResult``, an
    exception raised by the builder, or ``None`` when the job was cancelled.
    """

    def __init__(
        self,
        parent: QObject,
        root: Path,
        previous: NavigatorSourceSnapshot | None,
        callback: Callable[["NavigatorSnapshotJob", object], None],
        *,
        rescan: bool,
        detection: SourceDetection | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
        on_partial: Callable[[NavigatorSourceSnapshot], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.root = root
        self.callback = callback
        # Called on the owner's thread with (folders read, folders found, phase), and on a
        # first read with the parts read so far.
        self.on_progress = on_progress
        self.on_partial = on_partial
        self._stop_event = threading.Event()
        self.worker = _SnapshotTask(
            root, previous.copy() if previous is not None else None, rescan, self._stop_event, detection
        )
        self.worker.signals.completed.connect(self._finished)
        self.worker.signals.progress.connect(self._progress)
        self.worker.signals.partial.connect(self._partial)
        QThreadPool.globalInstance().start(self.worker)

    @property
    def cancelled(self) -> bool:
        return self._stop_event.is_set()

    def cancel(self) -> None:
        self._stop_event.set()

    @Slot(int, int, str)
    def _progress(self, done: int, total: int, phase: str) -> None:
        if not self.cancelled and self.on_progress is not None:
            self.on_progress(done, total, phase)

    @Slot(object)
    def _partial(self, snapshot: object) -> None:
        if not self.cancelled and self.on_partial is not None:
            self.on_partial(snapshot)

    @Slot(object)
    def _finished(self, result: object) -> None:
        if not self.cancelled:
            self.callback(self, result)
        self.deleteLater()
