"""Live AI tests: Magic drop and Create plugin on tests/AI_demodata, with Claude and Codex.

These run the real agents, so they cost tokens and take time (up to the AI time limit per
run). Run them only when the user asks (see AGENTS.md); the unit tests never do.

    python tools/run_ai_tests.py                         # every small case, both providers and modes
    python tools/run_ai_tests.py --provider codex --mode magic --case csv_bike_power
    python tools/run_ai_tests.py --big --jobs 3 --yes    # include the big files, no question

Each run gets the same treatment as in LODGaZilla: Magic drop runs ``MagicDropJob`` on the
data file and its description, if any; Create plugin runs ``PluginChatJob`` with an
automated request (and one follow-up when no importer was built), then imports the data
file with the new plugin. The H5 results are checked against tests/AI_demodata/cases.json.
Everything, with each agent's transcript, goes to logs/ai_tests/<time>/, with report.md.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA = ROOT / "tests" / "AI_demodata"
CASES_FILE = DATA / "cases.json"
MODES = ("magic", "plugin")
PROVIDER_NAMES = ("claude", "codex")
TRIAL_TIMEOUT_SECONDS = 900.0
COST = re.compile(r"cost \$(\d+(?:\.\d+)?)")

PLUGIN_REQUEST = (
    "This is an automated test of Create plugin: no one will answer questions. Make your own "
    "reasonable decisions without asking, and build the complete importer plugin for the attached "
    "file(s) now, tested with the trial import. Some attached files may describe the format instead "
    "of holding data: use them, and let the importer accept only the data files.")
PLUGIN_FOLLOW_UP = ("No one can answer questions in this automated test. Build the complete importer "
                    "plugin now with your own assumptions, and test it with the trial import.")


# ---- Cases and checks (no AI, no Qt; covered by tests/test_ai_live_checks.py) --------------

def load_cases(path: Path = CASES_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def normalized(name: str) -> str:
    """A series name for matching: lower case, other characters as single spaces."""
    return " " + re.sub(r"[\W_]+", " ", name.lower()).strip() + " "


def series_passes(check: dict, series: dict) -> bool:
    if "match" in check and not re.search(check["match"], normalized(series.get("name", ""))):
        return False
    low, high = check.get("samples", (0, math.inf))
    if not low <= series.get("samples", -1) <= high:
        return False
    ranges = check.get("ranges")
    if ranges:
        if "min" not in series or "max" not in series:
            return False
        return any(low <= series["min"] and series["max"] <= high for low, high in ranges)
    return True


def time_seconds(text: str) -> float | None:
    """A trial import summary's time: ISO for absolute times, "12.5 s" for relative ones."""
    if not text:
        return None
    if text.endswith(" s"):
        return float(text[:-2])
    return datetime.fromisoformat(text).timestamp()


def evaluate(case: dict, summaries: list[dict], metadata_text: str = "") -> list[dict]:
    """Each check's label, whether it passed and what was found."""
    series = [item for summary in summaries for item in summary.get("series", [])]
    results = []
    for check in case["checks"]:
        found = [item for item in series if series_passes(check, item)]
        results.append({"label": check["label"], "passed": len(found) >= check.get("at_least", 1),
                        "found": ", ".join(item["name"] for item in found[:4]) or "no such series"})
    if "start" in case:
        starts = [summary.get("time_start", "") for summary in summaries]
        passed = bool(starts) and all(any(start.startswith(prefix) for prefix in case["start"]) for start in starts)
        results.append({"label": "start time", "passed": passed, "found": ", ".join(starts) or "none"})
    if "duration" in case:
        low, high = case["duration"]
        spans = []
        for summary in summaries:
            start, end = time_seconds(summary.get("time_start", "")), time_seconds(summary.get("time_end", ""))
            spans.append(None if start is None or end is None else end - start)
        passed = bool(spans) and all(span is not None and low <= span <= high for span in spans)
        results.append({"label": "duration", "passed": passed,
                        "found": ", ".join("none" if span is None else f"{span:g} s" for span in spans) or "none"})
    for text in case.get("metadata", []):
        results.append({"label": f"metadata {text}", "passed": text in metadata_text, "found": ""})
    return results


def outcome(results: list[dict]) -> str:
    passed = sum(result["passed"] for result in results)
    return "pass" if passed == len(results) else "partial" if passed else "fail"


# ---- Running one case -----------------------------------------------------------------------

@dataclass
class Run:
    case: str
    provider: str
    mode: str
    result: str = "error"  # pass, partial, fail or error
    message: str = ""
    checks: list[dict] = field(default_factory=list)
    seconds: float = 0.0
    cost_usd: float | None = None
    folder: str = ""


class Transcript:
    """An agent's events in a file, and the costs it reported."""

    def __init__(self, path: Path) -> None:
        self.stream = path.open("w", encoding="utf-8")
        self.costs: list[float] = []
        self.lock = threading.Lock()

    def __call__(self, kind: str, text: str) -> None:
        with self.lock:
            self.stream.write(f"[{datetime.now():%H:%M:%S}] {kind}: {text}\n")
            self.stream.flush()
        self.costs += [float(value) for value in COST.findall(text)]

    def close(self) -> None:
        self.stream.close()


def h5_metadata_text(path: Path) -> str:
    """Every attribute in an H5 file, as text, for the metadata checks."""
    import h5py
    parts: list[str] = []
    with h5py.File(path, "r") as handle:
        def collect(_name, item):  # type: ignore[no-untyped-def]
            parts.extend(f"{key}={value}" for key, value in item.attrs.items())
        parts.extend(f"{key}={value}" for key, value in handle.attrs.items())
        handle.visititems(collect)
    return "\n".join(parts)


def run_case(case: dict, provider, executable: Path, mode: str, output: Path, settings: dict,  # type: ignore[no-untyped-def]
             web: bool, active: set) -> Run:
    from PySide6.QtCore import Qt
    from src.ai import plugin_builder
    from src.ai.magic_drop import run_trial_import
    from src.ai.trial_import import h5_summary
    from src.jobs.magic_drop import MagicDropJob
    from src.jobs.plugin_chat import PluginChatJob

    direct = Qt.ConnectionType.DirectConnection
    run = Run(case["id"], provider.name, mode)
    run_folder = output / f"{case['id']}-{provider.name.lower()}-{mode}"
    run_folder.mkdir(parents=True)
    run.folder = str(run_folder.relative_to(output))
    data_files = [DATA / name for name in case["data"]]
    files = data_files + [DATA / name for name in case.get("spec", [])]
    transcript = Transcript(run_folder / "transcript.log")
    budget, limit = settings["max_budget_usd"], 60.0 * settings["time_limit_minutes"]
    started = time.monotonic()
    try:
        if mode == "magic":
            job = MagicDropJob(executable, files, run_folder / "data", max_budget_usd=budget,
                               time_limit_seconds=limit, trial_timeout_seconds=TRIAL_TIMEOUT_SECONDS,
                               drafts_root=run_folder / "drafts", provider=provider, web=web)
            finished: list = []
            job.event.connect(transcript, direct)
            job.finished.connect(lambda *result: finished.append(result), direct)
            active.add(job)
            job.start()
            job.wait()
            active.discard(job)
            succeeded, message, folder = finished[0] if finished else (False, "No result.", None)
            run.message = message
            if not succeeded:
                return run
            outputs = [Path(folder) / (path.stem + ".h5") for path in data_files]
            info = "\n".join(path.read_text(encoding="utf-8", errors="replace")
                             for path in Path(folder).glob("*_info.json"))
        else:
            plugin_root = run_folder / "plugin"
            plugin_root.mkdir()
            plugin_id = f"aitest_{case['id']}"
            session = ""
            for request in (PLUGIN_REQUEST, PLUGIN_FOLLOW_UP):
                job = PluginChatJob(executable, plugin_id, request, files if not session else [],
                                    session_id=session, provider=provider, web=web, max_budget_usd=budget,
                                    time_limit_seconds=limit, plugin_root=plugin_root)
                finished = []
                job.event.connect(transcript, direct)
                job.finished.connect(lambda *result: finished.append(result), direct)
                active.add(job)
                job.start()
                job.wait()
                active.discard(job)
                succeeded, note, session = finished[0] if finished else (False, "No result.", "")
                run.message = note
                if not succeeded or plugin_builder.plugin_has_importer(plugin_root / plugin_id):
                    break
            if not plugin_builder.plugin_has_importer(plugin_root / plugin_id):
                run.message = run.message or "The plugin has no importer."
                return run
            examples = run_folder / "examples"
            examples.mkdir()
            outputs = []
            for path in data_files:
                copy = examples / path.name
                shutil.copy2(path, copy)
                try:
                    run_trial_import(plugin_root / plugin_id, copy, copy.with_suffix(".h5"),
                                     timeout=TRIAL_TIMEOUT_SECONDS)
                except Exception as error:  # noqa: BLE001 - reported as this run's result.
                    run.message = f"{path.name}: not imported: {error}"
                    return run
                outputs.append(copy.with_suffix(".h5"))
            info = ""
        missing = [path.name for path in outputs if not path.is_file()]
        if missing:
            run.message = f"No H5 file for {', '.join(missing)}."
            return run
        summaries = [h5_summary(path) for path in outputs]
        (run_folder / "summary.json").write_text(json.dumps(summaries, indent=1), encoding="utf-8")
        run.checks = evaluate(case, summaries, info + "\n" + "\n".join(h5_metadata_text(path) for path in outputs))
        run.result = outcome(run.checks)
        return run
    except Exception as error:  # noqa: BLE001 - one broken run must not stop the others.
        run.message = f"{type(error).__name__}: {error}"
        return run
    finally:
        run.seconds = time.monotonic() - started
        run.cost_usd = sum(transcript.costs) if transcript.costs else None
        transcript.close()


# ---- Report ---------------------------------------------------------------------------------

def report_text(runs: list[Run], started: datetime) -> str:
    counts = {name: sum(run.result == name for run in runs) for name in ("pass", "partial", "fail", "error")}
    costs = [run.cost_usd for run in runs if run.cost_usd is not None]
    lines = [f"# Live AI tests, {started:%Y-%m-%d %H:%M}", "",
             f"{len(runs)} runs: {counts['pass']} pass, {counts['partial']} partial, {counts['fail']} fail, "
             f"{counts['error']} error." + (f" Claude's reported cost: ${sum(costs):.2f}." if costs else ""), "",
             "| Case | Provider | Mode | Result | Checks | Time | Cost |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for run in sorted(runs, key=lambda item: (item.case, item.provider, item.mode)):
        checks = f"{sum(check['passed'] for check in run.checks)}/{len(run.checks)}" if run.checks else "-"
        cost = f"${run.cost_usd:.2f}" if run.cost_usd is not None else "-"
        lines.append(f"| {run.case} | {run.provider} | {run.mode} | {run.result} | {checks} | "
                     f"{run.seconds / 60:.1f} min | {cost} |")
    lines += ["", "## Details", ""]
    for run in sorted(runs, key=lambda item: (item.case, item.provider, item.mode)):
        if run.result == "pass":
            continue
        lines.append(f"### {run.case}, {run.provider}, {run.mode}: {run.result}")
        lines.append("")
        if run.message:
            lines.append(f"{run.message.strip()}")
            lines.append("")
        lines += [f"- {'ok' if check['passed'] else 'FAILED'}: {check['label']}"
                  + (f" ({check['found']})" if check["found"] else "") for check in run.checks]
        lines += [f"- Files: `{run.folder}`", ""]
    return "\n".join(lines) + "\n"


# ---- Main -----------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live AI tests of Magic drop and Create plugin (costs tokens).")
    parser.add_argument("--provider", nargs="+", choices=PROVIDER_NAMES, default=list(PROVIDER_NAMES))
    parser.add_argument("--mode", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--case", nargs="+", metavar="ID", help="only these cases (see --list)")
    parser.add_argument("--big", action="store_true", help="include the big files")
    parser.add_argument("--jobs", type=int, default=2, help="runs at the same time (default 2)")
    parser.add_argument("--web", action="store_true", help="let the agents read web pages")
    parser.add_argument("--output", type=Path, help="default: logs/ai_tests/<time>")
    parser.add_argument("--list", action="store_true", help="list the cases and exit")
    parser.add_argument("--yes", action="store_true", help="start without asking")
    options = parser.parse_args(argv)

    cases = load_cases()
    if options.list:
        for case in cases:
            print(f"{case['id']:28} {'big ' if case.get('big') else ''}{', '.join(case['data'] + case.get('spec', []))}")
        return 0
    if options.case:
        unknown = set(options.case) - {case["id"] for case in cases}
        if unknown:
            parser.error(f"unknown case(s): {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case["id"] in options.case]
    elif not options.big:
        cases = [case for case in cases if not case.get("big")]
    missing = sorted({name for case in cases for name in case["data"] + case.get("spec", [])
                      if not (DATA / name).is_file()})
    if missing:
        print(f"Missing test files: {', '.join(missing)}. Run python tools/create_ai_demodata.py first.")
        return 2

    from src.ai.providers import CLAUDE, CODEX
    from src.app.settings import load_ai_settings
    settings = load_ai_settings()
    providers = []
    for provider in (CLAUDE, CODEX):
        if provider.name.lower() not in options.provider:
            continue
        executable = provider.find(settings[provider.path_setting] or None)
        if executable is None:
            print(f"{provider.product} not found; skipping {provider.name}.")
        else:
            providers.append((provider, executable))
    if not providers:
        return 2
    plan = [(case, provider, executable, mode) for case in cases for provider, executable in providers
            for mode in options.mode]
    claude_runs = sum(provider.name == "Claude" for _case, provider, _executable, _mode in plan)
    print(f"{len(plan)} runs: {len(cases)} case(s) x {', '.join(p.name for p, _e in providers)} x "
          f"{', '.join(options.mode)}, {options.jobs} at a time, up to {settings['time_limit_minutes']:g} min each.")
    print("The synthetic files are sent to the providers (Claude to Anthropic, Codex to OpenAI).")
    if claude_runs:
        print(f"Claude's cost limit is ${settings['max_budget_usd']:g} per agent run; Create plugin may take two "
              f"runs, so at most about ${claude_runs * 2 * settings['max_budget_usd']:g} in all.")
    if not options.yes and input("Start? Type yes: ").strip().lower() != "yes":
        print("Not started.")
        return 1

    from PySide6.QtCore import QCoreApplication
    # The jobs are QObjects; the application must live while they run.
    application = QCoreApplication.instance() or QCoreApplication([])
    started = datetime.now()
    output = options.output or ROOT / "logs" / "ai_tests" / f"{started:%Y%m%d-%H%M%S}"
    output.mkdir(parents=True, exist_ok=True)
    active: set = set()
    runs: list[Run] = []
    executor = ThreadPoolExecutor(max_workers=max(1, options.jobs))
    pending = {executor.submit(run_case, case, provider, executable, mode, output, settings, options.web, active)
               for case, provider, executable, mode in plan}
    try:
        while pending:
            done, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
            for future in done:
                run = future.result()
                runs.append(run)
                checks = f" {sum(c['passed'] for c in run.checks)}/{len(run.checks)} checks" if run.checks else ""
                print(f"[{len(runs)}/{len(plan)}] {run.case} {run.provider} {run.mode}: {run.result}{checks}"
                      f" in {run.seconds / 60:.1f} min" + (f" - {run.message.strip()[:120]}" if run.result == "error" else ""))
    except KeyboardInterrupt:
        print("Stopping the running agents…")
        for job in list(active):
            job.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
    else:
        executor.shutdown(wait=True)
    (output / "results.json").write_text(json.dumps([asdict(run) for run in runs], indent=1), encoding="utf-8")
    (output / "report.md").write_text(report_text(runs, started), encoding="utf-8")
    print(f"Report: {output / 'report.md'}")
    application.processEvents()  # Deliver the jobs' deferred deletions before exiting.
    return 0


if __name__ == "__main__":
    sys.exit(main())
