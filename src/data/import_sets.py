"""Descriptions of same-folder inputs producing one importer output."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath


@dataclass(frozen=True)
class ImportSet:
    """An optional multi-file import. Names are relative to the input's folder.

    ``key`` is stable, independent of display text. ``inputs`` includes missing
    members so incomplete sets remain visible. Dependencies (for example a
    clock reference) affect readiness and freshness but are not member CSVs.
    Discovery must be cheap: inspect names/headers, never load measurement arrays.
    """

    key: str
    title: str
    inputs: tuple[str, ...]
    output: str
    dependencies: tuple[str, ...] = ()
    problem: str = ""

    def __post_init__(self) -> None:
        if not self.key or not self.title or not self.inputs:
            raise ValueError("An import set needs a key, title and inputs.")
        for name in (*self.inputs, *self.dependencies, self.output):
            if (not isinstance(name, str) or not name or name in {'.', '..'}
                    or any(c in name for c in '/\\|:') or PureWindowsPath(name).name != name):
                raise ValueError(f"Import set paths must be same-folder file names: {name!r}")
        names = [name.casefold() for name in (*self.inputs, *self.dependencies)]
        if len(names) != len(set(names)) or self.output.casefold() in names:
            raise ValueError("Import set inputs, dependencies and output must be distinct.")
        if Path(self.output).suffix.lower() not in {'.h5', '.hdf5'}:
            raise ValueError("An import set must produce an H5 file.")

    def missing(self, folder: Path) -> tuple[str, ...]:
        return tuple(name for name in (*self.inputs, *self.dependencies)
                     if not (folder / name).is_file())

    def check_ready(self, folder: Path) -> None:
        if self.problem:
            raise ValueError(self.problem)
        missing = self.missing(folder)
        if missing:
            raise ValueError("Incomplete import set; missing: " + ", ".join(missing))


def describe_import_set(parser, source: Path) -> ImportSet | None:
    """Validate an optional description, including older duck-typed parsers."""
    describe = getattr(parser, "import_set", None)
    result = describe(source) if callable(describe) else None
    if result is not None:
        if not isinstance(result, ImportSet):
            raise ValueError("import_set() must return ImportSet or None.")
        if source.name not in result.inputs:
            raise ValueError("An import set must include the selected input.")
    return result
