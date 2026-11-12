"""Build the Navigator's disk-backed snapshot without blocking the GUI thread."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from src.data.folder_info import FolderInfoData, attachment_path, info_file_path, read_existing_info_file
from src.data.navigator_data import (
    H5NavigatorData,
    ensure_folder_info_files,
    read_h5_navigator_data,
    scan_navigator_folder,
)


@dataclass(frozen=True)
class FileSignature:
    modified_ns: int
    size: int


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
        )


@dataclass
class NavigatorSnapshotResult:
    snapshot: NavigatorSourceSnapshot
    # False when every folder, file and parsed entry was reused unchanged.
    changed: bool
    read_errors: dict[Path, str] = field(default_factory=dict)
    read_ok: set[Path] = field(default_factory=set)


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
) -> NavigatorSnapshotResult:
    """Scan folders, read changed ``_info.json`` files and inspect changed H5 files.

    A refresh without rescan reuses the previous folder list, but skips folders
    that no longer exist and never creates metadata files, so a deleted or
    renamed folder cannot be recreated on disk.
    """

    def check_cancelled() -> None:
        if cancelled():
            raise SnapshotCancelled()

    root = root.resolve()
    if previous is not None and previous.root != root:
        previous = None
    errors: dict[Path, str] = {}
    if previous is None or rescan:
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
    folder_infos: dict[Path, FolderInfoData | None] = {}
    info_signatures: dict[Path, FileSignature | None] = {}
    read_ok: set[Path] = set()
    for folder in folders:
        check_cancelled()
        signature = file_signature(info_file_path(folder))
        info_signatures[folder] = signature
        if folder in previous_info_signatures and previous_info_signatures[folder] == signature:
            folder_infos[folder] = previous_infos.get(folder)
            continue
        changed = True
        try:
            folder_infos[folder] = read_existing_info_file(folder)
        except (OSError, ValueError) as error:
            folder_infos[folder] = None
            errors[folder] = str(error)
        else:
            read_ok.add(folder)

    previous_h5 = previous.h5_data if previous is not None else {}
    previous_h5_signatures = previous.h5_signatures if previous is not None else {}
    h5_data: dict[Path, H5NavigatorData] = {}
    h5_signatures: dict[Path, FileSignature | None] = {}
    # Linked audio may be stored outside the scanned folders; inspect it here too, off the GUI thread.
    scanned = set(h5_files)
    linked_audio = [
        path for path in dict.fromkeys(
            attachment_path(folder, audio) for folder, info in folder_infos.items() if info is not None for audio in info.audio
        ) if path not in scanned and path.is_file()
    ]
    for h5_path in [*h5_files, *linked_audio]:
        check_cancelled()
        signature = file_signature(h5_path)
        h5_signatures[h5_path] = signature
        if h5_path in previous_h5 and previous_h5_signatures.get(h5_path) == signature:
            h5_data[h5_path] = previous_h5[h5_path]
        else:
            changed = True
            h5_data[h5_path] = read_h5_navigator_data(h5_path)

    snapshot = NavigatorSourceSnapshot(
        root, folders, h5_files, folder_infos, info_signatures, h5_data, h5_signatures
    )
    return NavigatorSnapshotResult(snapshot, changed, errors, read_ok)


class _SnapshotSignals(QObject):
    completed = Signal(object)


class _SnapshotTask(QRunnable):
    def __init__(self, root, previous, rescan, stop_event):  # type: ignore[no-untyped-def]
        super().__init__()
        self.root = root
        self.previous = previous
        self.rescan = rescan
        self.stop_event = stop_event
        self.signals = _SnapshotSignals()

    def run(self) -> None:
        try:
            result: object = build_navigator_snapshot(
                self.root, self.previous, rescan=self.rescan, cancelled=self.stop_event.is_set
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
    ) -> None:
        super().__init__(parent)
        self.root = root
        self.callback = callback
        self._stop_event = threading.Event()
        self.worker = _SnapshotTask(
            root, previous.copy() if previous is not None else None, rescan, self._stop_event
        )
        self.worker.signals.completed.connect(self._finished)
        QThreadPool.globalInstance().start(self.worker)

    @property
    def cancelled(self) -> bool:
        return self._stop_event.is_set()

    def cancel(self) -> None:
        self._stop_event.set()

    @Slot(object)
    def _finished(self, result: object) -> None:
        if not self.cancelled:
            self.callback(self, result)
        self.deleteLater()
