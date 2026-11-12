"""Read-only GitHub/Bitbucket discovery. Never import candidate plugin code."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
import re
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from src.plugins.catalog import validate_manifest


def source_parts(source: str) -> tuple[str, str]:
    parsed = urlsplit(source.strip())
    owner = parsed.path.strip("/")
    if (parsed.scheme != "https" or parsed.netloc not in {"github.com", "bitbucket.org"}
            or parsed.query or parsed.fragment or not re.fullmatch(r"[A-Za-z0-9_-]+", owner)):
        raise ValueError("Use https://github.com/OWNER or https://bitbucket.org/WORKSPACE")
    return parsed.netloc, owner


def repository_url(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc not in {"github.com", "bitbucket.org"}
            or parsed.query or parsed.fragment
            or not re.fullmatch(r"/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", parsed.path)):
        raise ValueError("Invalid plugin repository URL")
    return url.removesuffix(".git").lower()


def command(arguments, *, cwd=None, timeout=120, input=None, environment=None):
    """Noninteractive subprocesses; never echo credential-helper output."""
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never", **(environment or {}))
    try:
        result = subprocess.run(arguments, cwd=cwd, input=input, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise ValueError(f"{arguments[0]} timed out") from None
    if result.returncode:
        # Git output can contain credentials supplied by a helper or remote URL.
        raise ValueError(f"{arguments[0]} failed. Check authentication, repository access and local Git state.")
    return result.stdout.strip()


def authorization(host: str) -> str:
    # An environment token also supports machines without Git Credential Manager.
    token = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")) if host == "github.com" else None
    if token:
        return "Bearer " + token
    token = os.environ.get("BITBUCKET_ACCESS_TOKEN") if host == "bitbucket.org" else None
    if token:
        return "Bearer " + token
    try:
        output = command(["git", "credential", "fill"], timeout=15,
                         input=f"protocol=https\nhost={host}\n\n")
    except (OSError, ValueError):
        return ""
    fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    if not fields.get("password"):
        return ""
    if host == "github.com":
        return "Bearer " + fields["password"]
    value = f"{fields.get('username', '')}:{fields['password']}".encode()
    return "Basic " + base64.b64encode(value).decode()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward authentication to a redirect target.
        return None


class Api:
    def __init__(self, host: str):
        self.base = "https://api.github.com" if host == "github.com" else "https://api.bitbucket.org/2.0"
        self.auth = authorization(host)

    def get(self, path: str):
        url = path if path.startswith("https://") else self.base + path
        if not url.startswith(self.base + "/"):
            raise ValueError("Unexpected API pagination address")
        headers = {"Accept": "application/json", "User-Agent": "LODGaZilla-plugin-manager"}
        if self.auth:
            headers["Authorization"] = self.auth
        try:
            with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=20) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError("Plugin API response too large")
                return json.loads(raw)
        except HTTPError as error:
            raise ValueError(f"Repository API HTTP {error.code}; check access credentials and API rate limits") from None
        except URLError:
            raise ValueError("Cannot reach repository API; check the network connection") from None

    def pages(self, path, *, bitbucket=False):
        for page in range(1, 101):
            data = self.get(path if bitbucket else f"{path}&page={page}")
            yield from data["values"] if bitbucket else data
            if bitbucket:
                path = data.get("next")
                if not path:
                    return
            elif len(data) < 100:
                return
        raise ValueError("Repository listing exceeded 100 pages; narrow the source account")


@dataclass(frozen=True)
class Candidate:
    plugin_id: str
    title: str
    version: str
    url: str
    branch: str
    commit: str
    error: str = ""


def search_sources(sources: list[str]) -> tuple[list[Candidate], list[str]]:
    candidates, errors, seen = [], [], set()
    for source in dict.fromkeys(s.strip().rstrip("/") for s in sources if isinstance(s, str) and s.strip()):
        try:
            host, owner = source_parts(source)
            api = Api(host)
            if not api.auth:
                errors.append(f"{source}: No saved credentials; checking public repositories only. "
                              "Configure Git Credential Manager to discover private plugins.")
            if host == "github.com":
                # Public owner listing plus all accessible private repositories.
                repos = list(api.pages(f"/users/{owner}/repos?per_page=100"))
                if api.auth:
                    repos += list(api.pages("/user/repos?per_page=100"))
                repos = [r for r in repos if r["owner"]["login"].lower() == owner.lower()]
            else:
                repos = api.pages(f"/repositories/{owner}?pagelen=100", bitbucket=True)
            for repo in repos:
                name = repo.get("name", "")
                if not name.lower().startswith("lodgazilla-"):
                    continue
                slug = repo["name"] if host == "github.com" else repo["slug"]
                url = f"https://{host}/{owner}/{slug}"
                identity = repository_url(url)
                if identity in seen:
                    continue
                seen.add(identity)
                try:
                    if host == "github.com":
                        branch = repo["default_branch"]
                        base = f"/repos/{quote(owner)}/{quote(slug)}"
                        commit = api.get(f"{base}/commits/{quote(branch, safe='')}")["sha"]
                        content = api.get(f"{base}/contents/plugin.json?ref={commit}")
                        manifest = json.loads(base64.b64decode(content["content"]))
                    else:
                        branch = repo["mainbranch"]["name"]
                        base = f"/repositories/{quote(owner)}/{quote(slug)}"
                        commit = api.get(f"{base}/refs/branches/{quote(branch, safe='')}")["target"]["hash"]
                        manifest = api.get(f"{base}/src/{commit}/plugin.json")
                    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
                        raise ValueError("Invalid repository revision")
                    compatibility = validate_manifest(manifest)
                    candidates.append(Candidate(manifest["id"], manifest["title"],
                                                str(manifest.get("version", "0.1.0")), url, branch,
                                                commit, compatibility))
                except (ValueError, KeyError, TypeError, OSError) as error:
                    errors.append(f"{url}: {error}")
        except (ValueError, KeyError, TypeError, OSError) as error:
            errors.append(f"{source}: {error}")
    return candidates, errors
