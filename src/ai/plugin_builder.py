"""Create plugin: an AI agent builds a LODGaZilla plugin in ``plugin/<id>``. No Qt.

The plugin folder is its own Git repository, created here. The agent's instructions
(CLAUDE.md or AGENTS.md) and the user's example files (``.lodgazilla_ai/``) stay out
of its commits through ``.git/info/exclude``. Each question is one agent run in the
folder; later runs continue the same session, so the agent remembers the conversation.
"""
from __future__ import annotations

import filecmp
import json
import re
import shutil
import subprocess
from pathlib import Path

from src.ai.agent_common import NO_WINDOW
from src.ai.magic_drop import UNITS_SECTION
from src.data.folder_info import is_info_file, read_existing_info_file, read_info_file, write_info_file
from src.jobs.cache_files import is_cache_output
from src.plugins.catalog import PLUGIN_ROOT

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
# The user's example files and trial output, inside the plugin folder but never committed.
WORK_FOLDER = ".lodgazilla_ai"
PLUGIN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
# Read-only folder info entry marking a data folder as a plugin's examples folder.
EXAMPLES_INFO_KEY = "create plugin"


def plugin_id_from(text: str) -> str:
    """A plugin ID suggestion from a file or format name: lowercase letters, digits and underscores."""
    words = re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")
    words = re.sub(r"^[0-9_]+", "", words)[:50].strip("_")
    return f"{words or 'my'}_importer"


def check_plugin_id(plugin_id: str, root: Path | None = None) -> str:
    """Why ``plugin_id`` cannot be used, or "" when it can."""
    if not PLUGIN_ID_PATTERN.match(plugin_id):
        return "Use 2 to 64 lowercase letters, digits and underscores, starting with a letter."
    if ((root or PLUGIN_ROOT) / plugin_id).exists():
        return f"plugin/{plugin_id} already exists."
    return ""


def instructions(plugin_id: str, host: Path = REPOSITORY_ROOT) -> str:
    """The agent's instructions for building ``plugin_id`` in its folder."""
    return f"""# Create a LODGaZilla plugin: {plugin_id}

You build a LODGaZilla plugin with the user, in this folder. It is its own Git
repository, installed as `plugin/{plugin_id}` of LODGaZilla at `{host}`.
The user talks to you through LODGaZilla's AI panel; keep replies short and plain.

## Your task

- Find out what the user needs: an importer for a file format, a plugin that reads
  data from a device (such as a data logger over USB), or an export. Ask when
  something is unclear, one or two questions at a time, and wait for the answer.
- The user's example files and attachments are in `{WORK_FOLDER}/inputs/`. Read them;
  never change them.
- Follow the plugin contract in `{host / 'docs' / 'Plugin_API.md'}`; importers also
  the data contract in `{host / 'docs' / 'H5_Format.md'}`. Use only the public
  interfaces in `{host / 'src' / 'contracts'}`. The installed plugins under
  `{host / 'plugin'}` are working examples.
- The plugin ID is `{plugin_id}`. The folder needs `plugin.json`, the implementation,
  `requirements.txt`, `constraints.txt`, `README.md` and `tests/`; importers also
  `importer.json`.
- Test as you go. An importer: `python -m src.ai.trial_import . "<example file>"
  {WORK_FOLDER}/trial/<name>.h5` prints the series, units, ranges and time range;
  run it from this folder. The plugin's own tests:
  `python "{host / 'tools' / 'run_tests.py'}" --host "{host}" --plugin . --isolated`.
- LODGaZilla commits this folder after each of your answers. Do not commit or push
  yourself; use Git only to look (status, diff, log).
- The plugin test command copies LODGaZilla to the temporary folder. Never copy
  LODGaZilla or other large files into this folder; if a test cannot run, say why.
- After each of your answers, LODGaZilla copies the example files into the user's data
  folder (`{plugin_id}` there), imports them with this plugin's importer, if it has one,
  and shows the user the series found or the error. Keep the importer runnable.
- When the plugin works, tell the user to enable it in Settings > Plugins; no restart is
  needed. Only a plugin with new requirements needs one, to install them; and so does a
  change to the UI code of a plugin that is already enabled.

{UNITS_SECTION}
## Rules

- Write only in this folder. Never change LODGaZilla's own files.
- You may read web pages to find a format's or device's specification, when your
  tools allow it. Never put the user's files or their values in a web request.
- Python and Git (to look) are the only commands you may run.
- Ask the user before adding any dependency to `requirements.txt`.
"""


def create_plugin_folder(plugin_id: str, instructions_file: str, *, root: Path | None = None,
                         host: Path = REPOSITORY_ROOT) -> Path:
    """``plugin/<id>`` as a new Git repository with the agent's instructions, kept out of commits."""
    problem = check_plugin_id(plugin_id, root)
    if problem:
        raise ValueError(problem)
    folder = (root or PLUGIN_ROOT) / plugin_id
    (folder / WORK_FOLDER / "inputs").mkdir(parents=True)
    (folder / instructions_file).write_text(instructions(plugin_id, host), encoding="utf-8")
    try:
        subprocess.run(["git", "init", "-q"], cwd=folder, check=True, capture_output=True,
                       stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"Git could not create the plugin repository ({error}). Install Git and try again.") \
            from error
    _exclude(folder, f"/{WORK_FOLDER}/")
    _exclude(folder, f"/{instructions_file}")
    return folder


def add_instructions(folder: Path, plugin_id: str, instructions_file: str, *, host: Path = REPOSITORY_ROOT) -> None:
    """The other agent's instructions file, when the user switches provider; kept out of commits too."""
    (folder / instructions_file).write_text(instructions(plugin_id, host), encoding="utf-8")
    _exclude(folder, f"/{instructions_file}")


def _exclude(folder: Path, pattern: str) -> None:
    """Keep ``pattern`` out of the plugin repository's commits (``.git/info/exclude``)."""
    exclude = folder / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8").splitlines() if exclude.is_file() else []
    if pattern not in existing:
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write(f"\n# LODGaZilla Create plugin\n{pattern}\n")


def copy_inputs(folder: Path, files: list[Path]) -> list[Path]:
    """Copy attached files into the plugin's work folder; a name already there gets a number."""
    return _copy_numbered(files, folder / WORK_FOLDER / "inputs")


def _copy_numbered(files: list[Path], target_folder: Path, *, keep_identical: bool = False) -> list[Path]:
    """Copy files into a folder; a name already there gets a number. With ``keep_identical``,
    a file identical to one already there is not copied again."""
    target_folder.mkdir(parents=True, exist_ok=True)
    copies = []
    for path in files:
        target = target_folder / path.name
        number = 2
        while target.exists() and not (keep_identical and filecmp.cmp(path, target, shallow=False)):
            target = target_folder / f"{path.stem}_{number}{path.suffix}"
            number += 1
        if not target.exists():
            shutil.copy2(path, target)
        copies.append(target)
    return copies


def examples_folder(data_folder: Path, plugin_id: str) -> Path:
    """The plugin's example folder in the data folder: ``<plugin_id>``, or a numbered name
    when a folder of that name holds other data. It may not exist yet."""
    candidate, number = data_folder / plugin_id, 2
    while candidate.exists() and _examples_owner(candidate) != plugin_id:
        candidate = data_folder / f"{plugin_id}_{number}"
        number += 1
    return candidate


def _examples_owner(folder: Path) -> str | None:
    try:
        info = read_existing_info_file(folder)
    except (OSError, ValueError):
        return None
    return info.readonly.get(EXAMPLES_INFO_KEY) if info is not None else None


def copy_examples(folder: Path, plugin_id: str, files: list[Path]) -> list[Path]:
    """Copy the user's files into the examples folder; the originals stay where they are."""
    # Attaching the same example again does not add a second copy.
    copies = _copy_numbered(files, folder, keep_identical=True)
    info = read_info_file(folder)
    if info.readonly.get(EXAMPLES_INFO_KEY) != plugin_id:
        info.readonly[EXAMPLES_INFO_KEY] = plugin_id
        write_info_file(folder, info)
    return copies


def example_sources(folder: Path) -> list[Path]:
    """The example files to import: not H5 files, folder info or LODGaZilla's working files."""
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir()
                  if path.is_file() and not path.name.startswith(".")
                  and not is_cache_output(path) and not is_info_file(path))


def plugin_has_importer(folder: Path) -> bool:
    """Whether the plugin declares an importer and has its importer.json."""
    try:
        manifest = json.loads((folder / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    capabilities = manifest.get("capabilities") if isinstance(manifest, dict) else None
    return (isinstance(capabilities, list) and "importer" in capabilities
            and (folder / "importer.json").is_file())


def plugin_prompt(text: str, copies: list[Path], instructions_file: str, *, first: bool) -> str:
    """One question for the agent, with the files attached to it."""
    lines = [f"Follow {instructions_file} in this folder." if first else ""]
    if copies:
        lines.append("Attached files (in your inputs folder):")
        lines.extend(f"- {path.relative_to(path.parents[2]).as_posix()} ({path.stat().st_size:,} bytes)"
                     for path in copies)
    lines += ["", "The user says:", text.strip() or "(no text, only the files above)"]
    return "\n".join(line for line in lines if line is not None).strip()


def commit_changes(folder: Path, request: str) -> str:
    """Commit everything the agent changed, named after the user's request; return what happened.

    LODGaZilla commits rather than the agent, because Codex's sandbox does not let it
    write into ``.git``. The instructions and the user's files are excluded.
    """
    first_line = next((line.strip() for line in request.splitlines() if line.strip()), "Update the plugin")
    message = first_line if len(first_line) <= 72 else first_line[:69].rstrip() + "..."

    def git(*arguments: str) -> subprocess.CompletedProcess:
        # The agent can write in this folder, .git included (Claude): run no hooks it wrote.
        return subprocess.run(["git", "-c", "core.hooksPath=", *arguments], cwd=folder, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)

    try:
        git("add", "-A")
        if git("diff", "--cached", "--quiet").returncode == 0:
            return ""
        committed = git("commit", "-q", "-m", message)
    except OSError as error:
        return f"Could not commit the changes: {error}"
    if committed.returncode != 0:
        reason = (committed.stderr or committed.stdout).strip().splitlines()
        return f"Could not commit the changes: {reason[-1] if reason else 'git commit failed'}"
    head = git("log", "-1", "--format=%h").stdout.strip()
    return f"Committed {head} in plugin/{folder.name}: {message}"


def plugin_ready(folder: Path) -> bool:
    """Whether the folder has a plugin manifest, so it can be enabled in Settings > Plugins."""
    return (folder / "plugin.json").is_file()
