"""Run core or external-plugin tests against an explicit application checkout.

Examples: python tools/run_tests.py --isolated
          python HOST/tools/run_tests.py --plugin PLUGIN_CHECKOUT --isolated
Test runs use a temporary host copy so GUI settings never modify the checkout.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


def snapshot(host, plugin, destination, *, all_plugins=False):
    excluded = {".git", ".venv", "__pycache__", "app_settings_debug.json", ".lodgazilla_dependencies.json"}
    # The local data, demo data and log folders, only at the root: src/data is source
    # code. Their case is ignored for folders written before the lowercase rename.
    # Demo data alone is gigabytes, which every isolated run would otherwise copy.
    root_excluded = {"data", "demodata", "logs"}
    plugin_id = json.loads((plugin / "plugin.json").read_text())["id"] if plugin else None
    def ignore(folder, names):
        return [name for name in names if name in excluded or
                (Path(folder) == host and name.lower() in root_excluded) or
                (Path(folder) == host and name == "plugin" and not all_plugins) or
                (Path(folder) == host / "plugin" and name == plugin_id)]
    shutil.copytree(host, destination, ignore=ignore, dirs_exist_ok=True)
    plugins = destination / "plugin"
    plugins.mkdir(exist_ok=True)
    if plugin:
        target = plugins / plugin_id
        # Create plugin's work folder holds the user's example files and trial output.
        shutil.copytree(plugin, target, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".lodgazilla_ai"))
        return target
    return None


def test_folder(root):
    """A checkout's tests: `tests/`, or `Tests/` in plugins not yet renamed."""
    for name in ("tests", "Tests"):
        if (root / name).is_dir() and name in {child.name for child in root.iterdir()}:
            return root / name
    return root / "tests"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--plugin", type=Path)
    parser.add_argument("--all-plugins", action="store_true", help="Keep every installed plugin for combined integration checks")
    parser.add_argument("--isolated", action="store_true", help="One process per test module")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    host = args.host.resolve()
    plugin = args.plugin.resolve() if args.plugin else None
    if args.worker:
        os.environ["LODGAZILLA_HOST"] = str(host)
        os.environ["LODGAZILLA_PLUGIN_ROOT"] = str(host / "plugin")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path[:0] = [str(host), str(host / "plugin")]
        test_dir = test_folder(plugin or host)
        suite = unittest.defaultTestLoader.discover(str(test_dir), pattern=args.pattern)
        return int(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
    directory = Path(tempfile.mkdtemp(prefix="lodgazilla-tests-"))
    staged_host = directory / "host"
    staged_plugin = snapshot(host, plugin, staged_host, all_plugins=args.all_plugins)
    print(f"Test snapshot and logs: {directory}", flush=True)
    test_dir = test_folder(staged_plugin or staged_host)
    patterns = [p.name for p in sorted(test_dir.glob(args.pattern))] if args.isolated else [args.pattern]
    if not patterns:
        print("No test modules found", file=sys.stderr)
        return 1
    def run(pattern):
        command = [sys.executable, str(staged_host / "tools/run_tests.py"), "--worker",
                   "--host", str(staged_host), "--pattern", pattern]
        if staged_plugin:
            command.extend(["--plugin", str(staged_plugin)])
        start = time.monotonic()
        log_path = directory / (pattern.replace("*", "all") + ".log")
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=staged_host, stdout=log, stderr=subprocess.STDOUT)
            try:
                code = process.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                else:
                    process.kill()
                process.wait()
                code = "timeout"
        result = {"module": pattern, "exit": code, "seconds": round(time.monotonic()-start, 1)}
        print(json.dumps(result), flush=True)
        if code != 0:
            print(log_path.read_text(encoding="utf-8", errors="replace")[-8000:], flush=True)
        return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(run, patterns))
    (directory / "summary.json").write_text(json.dumps(results, indent=2))
    return int(any(result["exit"] != 0 for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
