"""Install core and installed-plugin dependencies into the selected Python.

No repositories are downloaded. LODGaZilla runs this before each start
(``python -m src.app.lodgazilla``); run it explicitly to install without starting.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def dependency_files(root: Path, plugin_root: Path):
    from src.plugins.catalog import discover_plugins
    requirements = [root / "requirements.txt"]
    constraints = [root / "constraints.txt"] if (root / "constraints.txt").exists() else []
    manifests = list(plugin_root.glob("*/plugin.json"))
    errors = []
    descriptors = discover_plugins(plugin_root, check_dependencies=False, log=errors.append)
    for error in errors:
        print(error, file=sys.stderr)
    for descriptor in descriptors:
        if descriptor.error:
            continue
        for name, files in (("requirements.txt", requirements), ("constraints.txt", constraints)):
            path = descriptor.path.parent / name
            if path.exists():
                files.append(path)
    return requirements, constraints, manifests


def fingerprint(files, interpreter: str):
    digest = hashlib.sha256(interpreter.encode())
    for path in sorted(files, key=str):
        digest.update(str(path.resolve()).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path,
                        default=Path(os.environ.get("LODGAZILLA_PLUGIN_ROOT", ROOT / "plugin")))
    parser.add_argument("--check", action="store_true", help="Check the dependency stamp without installing anything")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    from src.plugins.installation import apply_pending, pending_requests
    if args.check and pending_requests(args.plugin_root):
        print("Plugin installations are pending. Start LODGaZilla (python -m src.app.lodgazilla) to finish them.")
        return 1
    if not args.check:
        for message in apply_pending(args.plugin_root):
            print(message, flush=True)
    requirements, constraints, manifests = dependency_files(ROOT, args.plugin_root.resolve())
    python = Path(sys.executable).resolve()
    marker = ROOT / ".lodgazilla_dependencies.json"
    stamp = fingerprint(requirements + constraints + manifests,
                        str(Path(sys.executable).resolve()) + sys.version)
    try:
        previous = json.loads(marker.read_text())
    except (OSError, ValueError):
        previous = {}
    if previous.get("fingerprint") == stamp and not args.force:
        return 0
    if args.check:
        print("Dependencies have not been checked for this Python and plugin set. Run tools/setup_environment.py.")
        return 1
    print(f"Using Python: {python}", flush=True)
    print(f"Installing requirements for core and {len(manifests)} installed plugin(s)...", flush=True)
    command = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
    for path in requirements:
        command.extend(["-r", str(path)])
    for path in constraints:
        command.extend(["-c", str(path)])
    subprocess.run(command, check=True)
    subprocess.run([str(python), "-m", "pip", "check"], check=True)
    # Source checkouts have no installed distribution metadata, so validate
    # their declared requirements explicitly in the target interpreter too.
    subprocess.run([str(python), "-c",
                    "import sys; from pathlib import Path; from src.plugins.catalog import requirement_errors; "
                    "errors=[e for p in sys.argv[1:] for e in requirement_errors(Path(p))]; "
                    "print('\\n'.join(errors)); sys.exit(bool(errors))",
                    *map(str, requirements)], cwd=ROOT, check=True)
    marker.write_text(json.dumps({"fingerprint": stamp}, indent=2) + "\n")
    return 0


def run(argv: list[str] | None = None) -> int:
    """main() with failures reported, for this script and LODGaZilla's start."""
    try:
        return main(argv)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Dependency setup failed: {error}", file=sys.stderr)
        print("Check the internet connection and permissions, then start LODGaZilla again.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
