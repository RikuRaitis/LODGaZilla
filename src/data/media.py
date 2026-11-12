"""Audio H5 files made from attached audio files and video soundtracks.

An audio H5 has the same layout as other caches, with series in the **Media**
navigator group. Its time runs from the start of the media; the file's
``log_start_unix_seconds`` is the data folder's log start plus the attachment's
offset, so changing the offset rewrites only that attribute.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import h5py

MEDIA_GROUP = "Media"
# File attribute marking an audio H5, so it is never mistaken for a measurement cache.
MEDIA_KIND_ATTR = "media_kind"
AUDIO_KIND = "audio"
AUDIO_SUFFIX = ".audio.h5"
FOLDER_LOG_START_ATTR = "folder_log_start_unix_seconds"
OFFSET_ATTR = "media_offset_seconds"
AUDIO_FILE_FILTER = "Audio files (*.wav *.mp3 *.flac *.ogg *.oga *.opus *.m4a *.aac *.wma *.aif *.aiff *.h5 *.hdf5)"


def audio_output_path(source: Path, data_folder: Path) -> Path:
    """``<file name>.audio.h5`` beside the source, or in the data folder when that cannot be written."""
    source = Path(source)
    folder = source.parent if os.access(source.parent, os.W_OK) else Path(data_folder)
    return folder / f"{source.name}{AUDIO_SUFFIX}"


def is_h5_file(path: Path) -> bool:
    return Path(path).suffix.lower() in {".h5", ".hdf5"}


def is_media_h5(path: Path) -> bool:
    """Whether an H5 file is an audio file made by LODGaZilla rather than a measurement cache."""
    try:
        with h5py.File(path, "r") as h5_file:
            return str(h5_file.attrs.get(MEDIA_KIND_ATTR, "")) == AUDIO_KIND
    except OSError:
        return False


def has_audio_track(path: Path) -> bool:
    """Whether a media file has a soundtrack; reads only the container header."""
    try:
        import av

        with av.open(str(path)) as container:
            return bool(container.streams.audio)
    except Exception:  # noqa: BLE001 - unreadable media simply has no usable soundtrack.
        return False


def set_audio_offset(path: Path, offset_seconds: float, *, attempts: int = 5) -> bool:
    """Move an audio H5 in time by rewriting its log start; retries while a reader has it open."""
    for attempt in range(attempts):
        try:
            with h5py.File(path, "r+") as h5_file:
                folder_start = h5_file.attrs.get(FOLDER_LOG_START_ATTR)
                h5_file.attrs[OFFSET_ATTR] = float(offset_seconds)
                if folder_start is not None:
                    h5_file.attrs["log_start_unix_seconds"] = float(folder_start) + float(offset_seconds)
            return True
        except OSError:
            if attempt + 1 == attempts:
                return False
            time.sleep(0.2)
    return False
