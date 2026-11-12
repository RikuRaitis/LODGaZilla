"""Recognize completed caches and this application's atomic-write and lock files."""
import re

_CACHE_TEMP_PATTERN = re.compile(r'\..+\.(?:h5|hdf5)\.[^.]+\.tmp', re.IGNORECASE)
# An audio H5 being written (media_import.convert_audio).
_AUDIO_PARTIAL_PATTERN = re.compile(r'\..+\.(?:h5|hdf5)\.[^.]+\.partial', re.IGNORECASE)
# Folder-info writes: the lock beside each _info.json, and its atomic-write temp file.
_INFO_WORK_PATTERN = re.compile(r'.+_info\.json(?:\.lock|\.[^.]+\.tmp)', re.IGNORECASE)


def is_cache_output(path):
    """Whether ``path`` is LODGaZilla's own file (an H5 cache or a file it is writing or
    locking), never a data file to offer to the importers."""
    return (path.suffix.lower() in {'.h5', '.hdf5'} or is_cache_temp_file(path)
            or bool(_AUDIO_PARTIAL_PATTERN.fullmatch(path.name)) or bool(_INFO_WORK_PATTERN.fullmatch(path.name)))


def is_cache_temp_file(path):
    """Whether ``path`` is an in-progress cache file written by write_hdf5_cache."""
    return bool(_CACHE_TEMP_PATTERN.fullmatch(path.name))

