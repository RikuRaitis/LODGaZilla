"""Recognize completed caches and this application's atomic-write files."""
import re

_CACHE_TEMP_PATTERN = re.compile(r'\..+\.(?:h5|hdf5)\.[^.]+\.tmp', re.IGNORECASE)


def is_cache_output(path):
    return path.suffix.lower() in {'.h5', '.hdf5'} or is_cache_temp_file(path)


def is_cache_temp_file(path):
    """Whether ``path`` is an in-progress cache file written by write_hdf5_cache."""
    return bool(_CACHE_TEMP_PATTERN.fullmatch(path.name))
