"""Read and write folder metadata stored beside imported data."""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FolderInfoData:
    editable: dict[str, str] = field(default_factory=dict)
    readonly: dict[str, str] = field(default_factory=dict)
    plugin: dict[str, str] = field(default_factory=dict)
    videos: list[dict[str, str]] = field(default_factory=list)
    images: list[dict[str, str]] = field(default_factory=list)
    # Audio H5 links: "path" (the H5), optional "source" (the original audio or
    # video file), "offset_seconds", and "video" when it is a video's soundtrack.
    audio: list[dict[str, str]] = field(default_factory=list)
    _baseline: dict | None = field(default=None, repr=False, compare=False)
    _path: Path | None = field(default=None, repr=False, compare=False)


# A newly imported folder should carry only information known at import time.
# Users can add editable metadata later through the navigator's empty Info row.
DEFAULT_EDITABLE_INFO: dict[str, str] = {}
DEFAULT_READONLY_INFO: dict[str, str] = {"serial number": ""}


def info_file_path(folder: str | Path) -> Path:
    folder_path = Path(folder)
    return folder_path / f"{folder_path.name}_info.json"


def normalise_info_mapping(data: object) -> FolderInfoData:
    if not isinstance(data, dict):
        raise ValueError("Folder info JSON must contain an object.")
    editable = normalise_string_mapping(data.get("editable", {}))
    readonly = normalise_string_mapping(data.get("readonly", {}))
    plugin = normalise_plugin_mapping(data.get("plugin", {}))
    # The plural section supersedes the legacy single attachment, including
    # when the list is empty after removing the last link.
    if "videos" in data:
        raw_videos = data["videos"]
        if not isinstance(raw_videos, list):
            raise ValueError("Folder videos JSON section must contain an array.")
    else:
        raw_videos = [data.get("video", {})]
    videos = []
    for entry in raw_videos:
        video = normalise_plugin_mapping(entry)
        if video.get("path", "").strip():
            video["path"] = video["path"].strip()
            video.setdefault("offset_seconds", "0")
            videos.append(video)
    raw_images = data.get("images", [])
    if not isinstance(raw_images, list):
        raise ValueError("Folder images JSON section must contain an array.")
    images = []
    for entry in raw_images:
        image = normalise_plugin_mapping(entry)
        if image.get("path", "").strip():
            image["path"] = image["path"].strip()
            images.append(image)
    raw_audio = data.get("audio", [])
    if not isinstance(raw_audio, list):
        raise ValueError("Folder audio JSON section must contain an array.")
    audio = []
    for entry in raw_audio:
        link = normalise_plugin_mapping(entry)
        if link.get("path", "").strip():
            link["path"] = link["path"].strip()
            link.setdefault("offset_seconds", "0")
            audio.append(link)
    # Metadata keys are identifiers rather than display text.  Treat them as
    # case-insensitive and give an editable value precedence if an old file
    # contains the same key in both sections.
    readonly = {key: value for key, value in readonly.items() if key not in editable}
    readonly.setdefault("serial number", "")
    return FolderInfoData(editable=editable, readonly=readonly, plugin=plugin, videos=videos, images=images,
                          audio=audio)


def attachment_path(folder: str | Path, attachment: dict[str, str]) -> Path:
    """Resolve a stored attachment relative to its data folder."""
    return (Path(folder) / attachment["path"]).resolve()


def video_attachment_path(folder: str | Path, video: dict[str, str]) -> Path:
    """Resolve a stored attachment relative to its data folder."""
    return attachment_path(folder, video)


def normalise_string_mapping(data: object) -> dict[str, str]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Folder info section must contain an object.")
    normalised: dict[str, str] = {}
    for key, value in data.items():
        canonical_key = normalise_info_key(key)
        if canonical_key:
            # In a legacy file containing casing variants, the later JSON
            # entry wins deterministically.  Writing the file removes the
            # duplicate and keeps this one canonical key.
            normalised[canonical_key] = "" if value is None else str(value)
    return normalised


def normalise_plugin_mapping(data: object) -> dict[str, str]:
    """Preserve plugin field spelling; plugin data is not navigator metadata."""
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Folder plugin JSON section must contain an object.")
    return {
        str(key).strip(): "" if value is None else str(value)
        for key, value in data.items()
        if str(key).strip()
    }


def normalise_info_key(key: object) -> str:
    """Return the canonical, case-insensitive form of an info metadata key."""

    return str(key).strip().casefold()


def ensure_info_file(folder: str | Path) -> Path:
    folder_path = Path(folder)
    path = info_file_path(folder_path)
    if not path.exists():
        folder_path.mkdir(parents=True, exist_ok=True)
        with _info_lock(path):
            if not path.exists():
                _write_info_atomic(path, FolderInfoData(dict(DEFAULT_EDITABLE_INFO), dict(DEFAULT_READONLY_INFO)))
    return path


def read_info_file(folder: str | Path) -> FolderInfoData:
    return _read_info(ensure_info_file(folder))


def read_existing_info_file(folder: str | Path) -> FolderInfoData | None:
    """Read folder metadata without creating the file or its folder."""
    try:
        return _read_info(info_file_path(folder))
    except FileNotFoundError:
        return None


def write_info_file(folder: str | Path, data: FolderInfoData | dict[str, str]) -> Path:
    """Save only changes since a read, preserving concurrent independent edits.

    A fresh document or a copy to another folder replaces the whole document.
    Conflicting edits to the same field fail explicitly instead of losing data.
    """
    folder_path = Path(folder)
    path = info_file_path(folder_path)
    is_update = isinstance(data, FolderInfoData) and data._path == path.resolve() and data._baseline is not None
    if not is_update:
        folder_path.mkdir(parents=True, exist_ok=True)
    proposed = _info_payload(normalise_info_mapping(_info_payload(data) if isinstance(data, FolderInfoData) else data))
    with _info_lock(path):
        if is_update:
            current = _info_payload(_read_info(path))
            proposed = _merge_info(data._baseline, proposed, current)
        saved = normalise_info_mapping(proposed)
        _write_info_atomic(path, saved)
    if isinstance(data, FolderInfoData):
        for key, value in _info_payload(saved).items():
            setattr(data, key, value)
        data._baseline, data._path = deepcopy(_info_payload(saved)), path.resolve()
    return path


def _info_payload(data: FolderInfoData) -> dict:
    return {name: getattr(data, name) for name in ("editable", "readonly", "plugin", "videos", "images", "audio")}


def _read_info(path: Path) -> FolderInfoData:
    with path.open("r", encoding="utf-8") as file:
        result = normalise_info_mapping(json.load(file))
    result._baseline, result._path = deepcopy(_info_payload(result)), path.resolve()
    return result


_MISSING = object()


def _merge_info(before, proposed, current, field_name="metadata"):
    if proposed == before:
        return deepcopy(current) if current is not _MISSING else _MISSING
    if current == before or current == proposed:
        return deepcopy(proposed) if proposed is not _MISSING else _MISSING
    if all(isinstance(value, dict) for value in (before, proposed, current)):
        result = {}
        for key in dict.fromkeys((*current, *proposed, *before)):
            value = _merge_info(before.get(key, _MISSING), proposed.get(key, _MISSING),
                                current.get(key, _MISSING), f"{field_name}.{key}")
            if value is not _MISSING:
                result[key] = value
        return result
    if all(isinstance(value, list) for value in (before, proposed, current)):
        # Attachment identity is its stored path. Merge offset/other field
        # changes independently and preserve concurrent additions/removals.
        mappings = [{entry["path"]: entry for entry in value} for value in (before, proposed, current)]
        if all(len(mapping) == len(value) for mapping, value in zip(mappings, (before, proposed, current))):
            return list(_merge_info(*mappings, field_name).values())
    raise ValueError(f"{field_name} changed in another writer; refresh and retry the edit")


@contextmanager
def _info_lock(path: Path):
    """Serialize writers across threads/processes; OS locks release on a crash.

    Keep the lock file in place: unlinking it permits two writers to lock
    different file objects. Never truncate the byte while another writer owns it.
    """
    with path.with_name(path.name + ".lock").open("a+b") as lock:
        if os.fstat(lock.fileno()).st_size == 0:
            lock.write(b"\0")
            lock.flush()
        deadline = time.monotonic() + 5.0
        while True:
            try:
                lock.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Folder metadata is busy: {path}")
                time.sleep(0.01)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _write_info_atomic(path: Path, data: FolderInfoData) -> None:
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp_path = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(_info_payload(data), file, indent=2, ensure_ascii=True)
            file.write("\n")
        _replace_with_retry(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _replace_with_retry(source: Path, target: Path, *, seconds: float = 2.0) -> None:
    """Replace ``target``; on Windows a reader (such as the Navigator) holding it open makes
    the replace fail with a sharing violation for a moment, so try again briefly."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            source.replace(target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def is_info_file(path: str | Path) -> bool:
    item = Path(path)
    return item.name == f"{item.parent.name}_info.json"
