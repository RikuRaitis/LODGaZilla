"""Stage explicit installs; activate them before the next process loads plugins."""
from __future__ import annotations

from dataclasses import asdict
import json
import base64
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time

from src.plugins.catalog import PLUGIN_ROOT, discover_plugins, requirement_errors, validate_manifest
from src.plugins.repositories import Candidate, authorization, command, repository_url
from urllib.parse import urlsplit


def git(folder, *args):
    return command(["git", "-c", "core.hooksPath=", "-c", "http.followRedirects=false",
                    "-C", str(folder), *args])


def installed_plugins(root):
    return {d.plugin_id: d for d in discover_plugins(root, check_dependencies=False)}


def pending_requests(root):
    return sorted((Path(root) / ".pending").glob("*/request.json"))


def pending_status(request):
    try:
        error = json.loads(request.read_text(encoding="utf-8")).get("error")
        return f"Update postponed: {error}" if error else "Restart required"
    except (OSError, ValueError, AttributeError):
        return "Invalid pending installation; see the startup log"


def remove_download(path: Path, root: Path):
    if path.is_symlink() or not path.resolve().is_relative_to((root / ".pending").resolve()):
        raise ValueError("Invalid download cleanup path")
    def writable(function, name, _error):
        Path(name).chmod(stat.S_IWRITE)
        function(name)
    shutil.rmtree(path, onerror=writable)


def candidate_status(candidate: Candidate, root: Path) -> tuple[str, str]:
    if candidate.error:
        return "", candidate.error
    request = root / ".pending" / candidate.plugin_id / "request.json"
    if request.exists():
        return "", pending_status(request)
    installed = installed_plugins(root).get(candidate.plugin_id)
    if installed is None:
        if (root / candidate.plugin_id).exists():
            return "", "Installation folder already exists without a valid manifest"
        return "Install", "Available"
    try:
        folder = installed.path.parent
        check_checkout(folder, candidate.url)
        if git(folder, "rev-parse", "HEAD") == candidate.commit:
            return "", "Up to date"
        return "Update", f"Installed {installed.version}; remote {candidate.version} ({candidate.commit[:8]})"
    except (OSError, ValueError) as error:
        return "", str(error)


def check_checkout(folder, url):
    if folder.is_symlink() or not (folder / ".git").is_dir():
        raise ValueError("Update requires a regular Git checkout")
    if repository_url(git(folder, "remote", "get-url", "origin")) != repository_url(url):
        raise ValueError("Plugin id belongs to a different repository")
    if git(folder, "status", "--porcelain"):
        raise ValueError("Local changes found; commit or move them before updating")


def validate_checkout(folder, candidate):
    manifest = json.loads((folder / "plugin.json").read_text(encoding="utf-8"))
    error = validate_manifest(manifest)
    if error or manifest["id"] != candidate.plugin_id:
        raise ValueError(error or "Downloaded plugin id does not match the selected plugin")
    descriptors = discover_plugins(folder.parent, check_dependencies=False)
    descriptor = next((d for d in descriptors if d.path.parent == folder), None)
    if descriptor is None or descriptor.error:
        raise ValueError("Downloaded plugin has missing files or an incompatible manifest")
    # Parse dependency declarations now, without installing or importing anything.
    for filename in ("requirements.txt", "constraints.txt"):
        path = folder / filename
        if path.is_symlink():
            raise ValueError("Dependency files must not be symbolic links")
        requirement_errors(path)


def stage_install(candidate: Candidate, root: Path = PLUGIN_ROOT):
    root = Path(root).resolve()
    repository_url(candidate.url)
    if not re.fullmatch(r"[a-z][a-z0-9_]*", candidate.plugin_id):
        raise ValueError("Invalid plugin id")
    if not re.fullmatch(r"[0-9a-f]{40,64}", candidate.commit):
        raise ValueError("Invalid plugin revision")
    action, status = candidate_status(candidate, root)
    if not action:
        raise ValueError(status)
    root.mkdir(parents=True, exist_ok=True)
    pending = root / ".pending"
    pending.mkdir(exist_ok=True)
    if pending.is_symlink():
        raise ValueError("Pending installation directory must not be a symbolic link")
    installed = installed_plugins(root).get(candidate.plugin_id)
    old_head = git(installed.path.parent, "rev-parse", "HEAD") if installed else None
    target = installed.path.parent.name if installed else candidate.plugin_id
    with tempfile.TemporaryDirectory(prefix="download-", dir=pending) as temporary:
        work = Path(temporary)
        checkout = work / "checkout"
        auth = authorization(urlsplit(candidate.url).netloc)
        if auth.startswith("Bearer "):
            username = "x-access-token" if urlsplit(candidate.url).netloc == "github.com" else "x-token-auth"
            auth = "Basic " + base64.b64encode(f"{username}:{auth[7:]}".encode()).decode()
        environment = ({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
                        "GIT_CONFIG_VALUE_0": "Authorization: " + auth} if auth else {})
        # No submodules, hooks or candidate Python code are executed.
        command(["git", "-c", "core.hooksPath=", "-c", "http.followRedirects=false",
                 "clone", "--no-checkout", "--", candidate.url, str(checkout)], timeout=300,
                environment=environment)
        git(checkout, "checkout", "--detach", candidate.commit)
        validate_checkout(checkout, candidate)
        if old_head:
            try:
                git(checkout, "merge-base", "--is-ancestor", old_head, candidate.commit)
            except ValueError:
                raise ValueError("Update is not a fast-forward; preserve/push local commits and resolve the branch first") from None
        request = {"candidate": asdict(candidate), "target": target, "old_head": old_head}
        (work / "request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
        destination = pending / candidate.plugin_id
        if destination.exists():
            # An interrupted cleanup after a successful previous activation.
            if (destination / "request.json").exists():
                raise ValueError("An installation is already pending for this plugin")
            remove_download(destination, root)
        work.rename(destination)
    return candidate.plugin_id


def apply_pending(root: Path = PLUGIN_ROOT) -> list[str]:
    """Fast-forward only. Failed requests remain available for retry at launch."""
    root = Path(root).resolve()
    messages = []
    for request_path in pending_requests(root):
        try:
            if not request_path.resolve().is_relative_to(root):
                raise ValueError("Pending request escapes the plugin directory")
            data = json.loads(request_path.read_text(encoding="utf-8"))
            candidate = Candidate(**data["candidate"])
            repository_url(candidate.url)
            target_name = data["target"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", target_name):
                raise ValueError("Invalid installation folder")
            target = root / target_name
            checkout = request_path.parent / "checkout"
            if not checkout.resolve().is_relative_to(root) or not target.resolve().is_relative_to(root):
                raise ValueError("Installation path escapes the plugin directory")
            # Recover a new installation interrupted after the final rename.
            if not checkout.exists() and data["old_head"] is None and target.exists():
                check_checkout(target, candidate.url)
                if git(target, "rev-parse", "HEAD") != candidate.commit:
                    raise ValueError("Installed revision differs from pending request")
                request_path.unlink()
                remove_download(request_path.parent, root)
                continue
            validate_checkout(checkout, candidate)
            if git(checkout, "rev-parse", "HEAD") != candidate.commit:
                raise ValueError("Downloaded revision changed; check again")
            if data["old_head"] is None:
                if target.exists():
                    # Recover if the previous activation finished before deleting its request.
                    raise ValueError("Installation folder already exists")
                # Make subsequent developer commits behave like a normal clone.
                git(checkout, "checkout", "-B", candidate.branch, candidate.commit)
                git(checkout, "branch", "--set-upstream-to", f"origin/{candidate.branch}")
                _rename_with_retry(checkout, target)
            else:
                check_checkout(target, candidate.url)
                head = git(target, "rev-parse", "HEAD")
                if head not in {data["old_head"], candidate.commit}:
                    raise ValueError("Local revision changed since download; update was not applied")
                # Git otherwise permits overwriting ignored files (e.g. local settings).
                ignored = set(git(target, "ls-files", "--others", "--ignored", "--exclude-standard").splitlines())
                incoming = set(git(checkout, "ls-files").splitlines())
                if ignored & incoming:
                    raise ValueError("Update would overwrite ignored local files")
                git(target, "-c", "protocol.file.allow=always", "fetch", str(checkout), candidate.commit)
                git(target, "merge", "--ff-only", "--no-edit", candidate.commit)
            request_path.unlink()
            messages.append(f"Installed {candidate.title}")
            remove_download(request_path.parent, root)
        except (OSError, ValueError, KeyError, TypeError) as error:
            messages.append(f"Plugin installation postponed ({request_path.parent.name}): {error}")
            try:
                request = json.loads(request_path.read_text(encoding="utf-8"))
                request["error"] = str(error)
                request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
            except (OSError, ValueError, TypeError):
                pass
    return messages


def _rename_with_retry(source: Path, target: Path, *, seconds: float = 5.0) -> None:
    """Move a fresh checkout into place. On Windows a virus scanner or the search indexer
    may hold its new files for a moment ("Access is denied"), so try again briefly
    rather than postponing the installation to the next start."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            source.rename(target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
