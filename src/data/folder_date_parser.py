"""Parse date information from data-folder names.

New folder-name syntaxes belong here so navigation code does not need to know
how individual devices or loggers name their output directories.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


FOLDER_TIMESTAMP_PATTERN = re.compile(
    r"^(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})_(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})$"
)


@dataclass(frozen=True)
class FolderDate:
    """Date information inferred from one folder name."""

    timestamp: datetime | None
    source_name: str

    @property
    def is_known(self) -> bool:
        return self.timestamp is not None


def parse_folder_date(folder_name: str | Path) -> FolderDate:
    """Parse a ``YYYYMMDD_HHMMSS`` folder name, or return an unknown date."""

    name = Path(folder_name).name
    match = FOLDER_TIMESTAMP_PATTERN.fullmatch(name)
    if match is None:
        return FolderDate(timestamp=None, source_name=name)

    try:
        timestamp = datetime(**{key: int(value) for key, value in match.groupdict().items()})
    except ValueError:
        return FolderDate(timestamp=None, source_name=name)
    return FolderDate(timestamp=timestamp, source_name=name)


def find_folder_date(path: Path, root_folder: Path) -> FolderDate:
    """Find the closest parseable folder date between a file and navigator root."""

    root_folder = root_folder.resolve()
    current = path.resolve()
    if current.is_file():
        current = current.parent

    while True:
        parsed = parse_folder_date(current.name)
        if parsed.is_known:
            return parsed
        if current == root_folder or current.parent == current:
            return FolderDate(timestamp=None, source_name=path.parent.name)
        current = current.parent
