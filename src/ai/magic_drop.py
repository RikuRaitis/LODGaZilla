"""Magic drop: files dropped on the AI panel become H5 files in the data folder. No Qt.

The AI agent (Claude or Codex) reads the dropped files and writes a parser into a
**draft**: an importer plugin folder outside the repository. LODGaZilla then runs
that parser itself, in a
separate process with a time limit (``python -m src.ai.trial_import``), and writes
the H5 with its own writer. The draft is kept, so a later step can turn it into an
installed importer plugin.

A Magic drop folder in the data folder holds copies of the dropped files, one
``<source>.h5`` per file and ``<folder>_info.json`` with the static values the
agent found in the files.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from src.data.folder_info import MAGIC_DROP_PLUGIN, SOURCE_IMPORT, read_info_file, source_entry, source_entry_key, write_info_file

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DRAFTS_ROOT = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "LODGaZilla" / "magic_drop"
# Written by the agent in the draft: static values found in the files, for _info.json.
METADATA_FILE = "metadata.json"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

IMPORTER_TEMPLATE = '''"""Parser written by Magic drop for: {sources}."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.contracts.importer import DataParser, ProgressCallback


class Parser(DataParser):
    name = "Magic drop {stamp}"
    priority = 50
    extensions = frozenset()
    format_name = "magic_drop.{stamp}"

    def can_parse(self, input_path: str | Path) -> bool:
        raise NotImplementedError

    def parse(self, input_path: str | Path, progress_callback: ProgressCallback | None = None) -> dict[str, Any]:
        raise NotImplementedError
'''

INSTRUCTIONS = """# Magic drop parser

You write a LODGaZilla importer for the data file(s) the user dropped. While you
work there is no user to ask: decide from the files themselves, and put your
questions in the final summary (step 5).

## Your task

1. Inspect each data file listed in the prompt. Read text files directly; for binary
   files, use short `python -c` snippets to dump bytes, find headers and record sizes.
2. Edit `importer.py` in this folder: a `Parser(DataParser)` whose `parse()` returns
   the common series dictionary. Find every data series in the file. Give each series
   values in physical units with an explicit unit (see Units below), and the time
   column with its unit and time reference; group
   related series (for example `Accelerometer` with X, Y, Z). Make `can_parse()`
   cheap and strict for this format. Build the result dictionary directly, as in the
   example in section 9 of `H5_Format.md` (`make_series()` only adds series defined in
   `importer.json`). Use only the Python standard library, numpy and
   `src.contracts.importer`.
3. Always test it on every file before you finish, with your shell tool:
   `python -m src.ai.trial_import . "<data file>" trial/<name>.h5`
   It prints a JSON summary (series, units, original units, sample counts, value
   ranges, time range). Fix the parser until every file imports and the summary is
   plausible: no 1970 or far-future times, increasing time, no all-NaN series, units
   set, and value ranges that make sense for their units (gravity near 9.8 m/s^2,
   not 1000). Python is the only command you may run.
4. Write `metadata.json` in this folder: a flat JSON object of static values found in
   the files, such as device, serial number, firmware, sample rate, start time or
   operator (`{"device": "...", "serial number": "..."}`). Strings only; `{}` if none.
5. Finish with a short plain-text summary for the user: the format, the series found,
   every unit conversion, and every assumption you made about units or time. End it
   with two questions:
   - Whether the data looks right: are all the series there, or are there too many?
     Name anything you suspect you missed or could not decode, such as unread bytes,
     unknown record types or columns, and ask which series to keep looking for, or
     for more information about the format, such as a specification, the device
     model or what a column means.
   - Whether they want a LODGaZilla importer plugin for this format, so that more
     files like these import without an AI agent.

## Units

- Plotted values must be in physical units, never raw counts or scaled integers.
  Prefer SI units (m, s, m/s, m/s^2, Pa, V, A, rad/s), unless another unit is the
  usual one for the quantity, such as deg for angles, degC for temperature or bpm
  for heart rate. GNSS latitude and longitude are decimal degrees (`deg`); altitude
  is metres (`m`).
- Take the scaling from the files: headers, column names, unit fields, embedded
  format descriptions, or the value range. The user's notes in the prompt win.
- A linear conversion (scale and offset): return the decoded raw values in `values`
  with their `original_unit`, the physical `unit`, and
  `"transform": {"scale": 0.001, "offset": 0, "formula": "raw * 0.001"}` in the
  series. LODGaZilla plots `values * scale + offset` and keeps the raw values in
  `value_raw`. Any other conversion: convert in the parser and set `original_unit`
  to the unit of the source values. Never convert the same values twice.
- A unit string is only a label: `"unit": "m"` converts nothing.
- If you cannot determine a series' unit or scaling, keep its raw values, name
  their unit honestly (such as `counts`) and say so in your summary.
- Use `bool` for on/off states and `index` for event or state codes.

## Rules

- Only write files in this folder. Never modify the data files.
- The data contract is in `H5_Format.md` in this folder; section 9 is the importer
  payload. The base class is in `parser_base.py`, for reference only.
- Do not write H5 files yourself; LODGaZilla does that from your parser.
- You may read web pages to find the file format's specification, when your tools
  allow it. Never put the files' contents or values in a web request.
- No new dependencies.
"""

# The AI's suggestion to extend an installed importer plugin, in the draft; LODGaZilla
# then asks the user whether to update that plugin or create a new one.
SUGGESTION_FILE = "plugin_suggestion.json"


def installed_importers_section(importers) -> str:  # type: ignore[no-untyped-def]
    """Instructions listing the installed importer plugins: (id, title, folder) tuples."""
    if not importers:
        return ""
    lines = ["", "## Installed importer plugins", "",
             "No enabled plugin can read these files; LODGaZilla asked them first. These importer",
             "plugins are installed; read their code (in the folder named) when the file looks related:", ""]
    lines += [f"- `{plugin_id}`: {title} (`{folder}`)" for plugin_id, title, folder in importers]
    lines += ["",
              "If the file is a variant of a format one of them reads (the same device family or",
              f"format, slightly different), write `{SUGGESTION_FILE}` in this folder:",
              '`{"plugin": "<plugin id>", "reason": "<one sentence on what differs>"}`.',
              "Say so in your summary too. LODGaZilla then asks the user whether to update that",
              "plugin or create a new one. Write the file only for a real near match.", ""]
    return "\n".join(lines)


def read_plugin_suggestion(draft: Path, known: set[str]) -> tuple[str, str] | None:
    """The AI's suggested plugin and its reason, when it names an installed importer."""
    try:
        data = json.loads((draft / SUGGESTION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    plugin_id = str(data.get("plugin", "")).strip()
    return (plugin_id, str(data.get("reason", "")).strip()) if plugin_id in known else None


# The unit rules, shared with Create plugin's instructions.
UNITS_SECTION = INSTRUCTIONS[INSTRUCTIONS.index("## Units"):INSTRUCTIONS.index("## Rules")]


def drafts_root() -> Path:
    return DRAFTS_ROOT


def create_draft(sources: list[Path], *, root: Path | None = None, instructions_file: str = "CLAUDE.md",
                 installed_importers=()) -> Path:  # type: ignore[no-untyped-def]
    """A new importer plugin folder for the agent to complete, outside the repository.

    The instructions go in ``instructions_file``: CLAUDE.md for Claude, AGENTS.md for Codex.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    draft = (root or drafts_root()) / stamp
    draft.mkdir(parents=True)
    plugin_id = f"magic_drop_{stamp}"
    (draft / "plugin.json").write_text(json.dumps({
        "id": plugin_id, "title": f"Magic drop {stamp}", "capabilities": ["importer"],
        "entry_point": "importer.py", "api_version": 1, "version": "0.1.0",
    }, indent=2), encoding="utf-8")
    (draft / "importer.json").write_text(json.dumps({"sections": []}, indent=2), encoding="utf-8")
    (draft / "importer.py").write_text(
        IMPORTER_TEMPLATE.format(sources=", ".join(path.name for path in sources), stamp=stamp), encoding="utf-8")
    (draft / instructions_file).write_text(INSTRUCTIONS + installed_importers_section(installed_importers),
                                           encoding="utf-8")
    shutil.copyfile(REPOSITORY_ROOT / "docs" / "H5_Format.md", draft / "H5_Format.md")
    shutil.copyfile(REPOSITORY_ROOT / "src" / "data" / "parser_base.py", draft / "parser_base.py")
    return draft


def agent_prompt(files: list[Path], hints: str = "", instructions_file: str = "CLAUDE.md") -> str:
    """The task for the agent; the rules are in the draft's instructions file."""
    lines = [f"Write the importer for these data files, following {instructions_file} in this folder:"]
    lines += [f"- {path} ({path.stat().st_size:,} bytes)" for path in files]
    if len(files) > 1:
        lines += ["", "Some of them may describe the format instead of holding data, such as a document or "
                  "source code: use them, and let the parser accept only the data files."]
    if hints.strip():
        lines += ["", "The user said about the files:", hints.strip()]
    return "\n".join(lines)


def magic_drop_folder(data_folder: Path, files: list[Path]) -> Path:
    """A new subfolder of the data folder, named after the first file; never an existing one."""
    stem = re.sub(r"[^\w\-. ]+", "_", files[0].stem).strip(" ._") or "magic_drop"
    folder = data_folder / stem
    number = 2
    while folder.exists():
        folder = data_folder / f"{stem}_{number}"
        number += 1
    return folder


def copy_sources(files: list[Path], folder: Path) -> list[Path]:
    """Copy the dropped files into the new folder; the originals stay where they are."""
    folder.mkdir(parents=True)
    copies = []
    for path in files:
        target = folder / path.name
        shutil.copy2(path, target)
        copies.append(target)
    return copies


def run_trial_import(draft: Path, source: Path, output: Path, *, timeout: float, cancelled=None) -> dict:
    """Run the draft's parser on ``source`` in its own process and return its summary.

    The parser is code the AI wrote, so it gets the agent's environment: without the Git
    hosting tokens (``agent_environment``). It runs in its own process tree: setting
    ``cancelled`` (a ``threading.Event``), or the timeout, ends it and everything it started.
    """
    import time
    from src.ai.agent_common import agent_environment
    from src.jobs.process_tree import kill_process_tree, popen_in_tree
    process, tree = popen_in_tree(
        [sys.executable, "-m", "src.ai.trial_import", str(draft), str(source), str(output)],
        cwd=REPOSITORY_ROOT, env=agent_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if cancelled is not None and cancelled.is_set():
                    raise InterruptedError("Trial import stopped") from None
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(process.args, timeout) from None
    finally:
        kill_process_tree(process, tree)
    if process.returncode != 0:
        lines = (stderr or stdout or "").strip().splitlines()
        raise RuntimeError(lines[-1] if lines else f"Trial import failed with exit code {process.returncode}")
    return json.loads(stdout.strip().splitlines()[-1])


def read_metadata(draft: Path) -> dict[str, str]:
    """The agent's static values, as strings; a missing or malformed file gives none."""
    try:
        data = json.loads((draft / METADATA_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key).strip(): str(value).strip() for key, value in data.items()
            if str(key).strip() and value is not None and not isinstance(value, (dict, list))}


def write_folder_info(folder: Path, metadata: dict[str, str], draft: Path,
                      imported: list[tuple[Path, Path]] = ()) -> Path:
    """The folder's _info.json: the static values as read-only entries, and a Source data
    record for each imported (source, H5) pair, by Magic drop with this draft."""
    info = read_info_file(folder)
    info.readonly.update(metadata)
    info.readonly["magic drop"] = draft.name
    records = {source_entry_key(entry): entry for entry in info.sources}
    for source, output in imported:
        entry = source_entry(SOURCE_IMPORT, source=source.name, output=output.name, plugin=MAGIC_DROP_PLUGIN,
                             draft=str(draft))
        records[source_entry_key(entry)] = entry
    info.sources = list(records.values())
    return write_info_file(folder, info)


def record_draft_import(folder: Path, source: Path, output: Path, draft: Path) -> Path:
    """Note a file imported again with a Magic drop parser in the folder's Source data."""
    from src.data.folder_info import record_sources
    return record_sources(folder, [source_entry(SOURCE_IMPORT, source=source.name, output=output.name,
                                                plugin=MAGIC_DROP_PLUGIN, draft=str(draft))])


def summary_text(summary: dict) -> str:
    """One line per series from a trial import summary."""
    lines = []
    for series in summary.get("series", []):
        unit = f" [{series['unit']}]" if series.get("unit") else ""
        original = series.get("original_unit")
        converted = f", converted from {original}" if original and original != series.get("unit") else ""
        lines.append(f"{series['name']}{unit}: {series['samples']:,} samples{converted}")
    return "\n".join(lines)
