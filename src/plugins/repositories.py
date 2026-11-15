"""Read-only GitHub/Bitbucket discovery. Never import candidate plugin code."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
import re
import ssl
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from src.jobs.process_tree import kill_process_tree, popen_in_tree
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


def command(arguments, *, cwd=None, timeout=120, input=None, environment=None, interactive=False):
    """Noninteractive subprocesses; never echo credential-helper output.

    No prompt may appear: an editor's GIT_ASKPASS (VS Code sets one in its terminals)
    would otherwise ask for a Bitbucket user name and wait. The command runs in its own
    process tree, because on a timeout killing only Git's launcher leaves the real Git
    holding the output pipe and the worker waiting for ever. ``interactive`` lets Git
    Credential Manager show its own sign-in, and nothing else.
    """
    mode = "auto" if interactive else "never"
    if arguments and arguments[0] == "git":
        arguments = ["git", "-c", "core.askPass=", "-c", f"credential.interactive={mode}", *arguments[1:]]
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE=mode,
                       GIT_ASKPASS="", SSH_ASKPASS="", **(environment or {}))
    process, tree = popen_in_tree(arguments, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                  env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        stdout, _ = process.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_process_tree(process, tree)
        process.communicate()
        raise ValueError(f"{arguments[0]} timed out") from None
    finally:
        if tree is not None:
            tree.close()
    result = subprocess.CompletedProcess(arguments, process.returncode, stdout)
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
        output = command(["git", "credential", "fill"], timeout=15, input=credential_request(host))
    except (OSError, ValueError):
        return ""
    fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    if not fields.get("password"):
        return ""
    if host == "github.com":
        return "Bearer " + fields["password"]
    value = f"{fields.get('username', '')}:{fields['password']}".encode()
    return "Basic " + base64.b64encode(value).decode()


SIGN_IN_TIMEOUT_SECONDS = 300


def credential_request(host: str) -> str:
    return f"protocol=https\nhost={host}\n\n"


def sign_in(host: str, *, replace: bool = False) -> None:
    """Sign in to ``host`` with Git Credential Manager's own window or browser page.

    The credential manager saves the sign-in (Windows Credential Manager, the macOS
    keychain), where ``authorization`` finds it; LODGaZilla keeps nothing. ``replace``
    forgets the saved sign-in first, so the user can sign in again. Raises ValueError.
    """
    if replace:
        try:
            command(["git", "credential", "reject"], timeout=15, input=credential_request(host))
        except (OSError, ValueError):
            pass
    try:
        output = command(["git", "credential", "fill"], timeout=SIGN_IN_TIMEOUT_SECONDS,
                         input=credential_request(host), interactive=True)
    except OSError:
        raise ValueError("Git is not installed; install Git for Windows to sign in") from None
    except ValueError:
        raise ValueError("Sign-in was cancelled or did not finish") from None
    fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    if not fields.get("password"):
        raise ValueError("Sign-in was cancelled or did not finish")
    # Git saves a credential only after "approve"; the output goes back unchanged.
    command(["git", "credential", "approve"], timeout=15, input=output + "\n\n")


def tls_context() -> ssl.SSLContext:
    """Verify certificates with the operating system, as browsers do.

    Python's own check sees only the roots already stored on the computer; Windows
    fetches a missing root on demand, so GitHub could fail intermittently with
    "unable to get issuer certificate". Without truststore, use Python's default.
    """
    try:
        import truststore
    except ImportError:
        return ssl.create_default_context()
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward authentication to a redirect target.
        return None


class Api:
    def __init__(self, host: str):
        self.base = "https://api.github.com" if host == "github.com" else "https://api.bitbucket.org/2.0"
        self.auth = authorization(host)
        # Git Credential Manager's Bitbucket sign-in saves an OAuth token, which the API
        # takes as a Bearer token; an app password or API token goes as Basic.
        self.alternative_auth = ""
        if host == "bitbucket.org" and self.auth.startswith("Basic "):
            password = base64.b64decode(self.auth[6:]).decode().partition(":")[2]
            self.alternative_auth = "Bearer " + password

    def get(self, path: str):
        try:
            return self._get(path)
        except HTTPError as error:
            if error.code == 401 and self.alternative_auth:
                self.auth, self.alternative_auth = self.alternative_auth, ""
                try:
                    return self._get(path)
                except HTTPError as retry:
                    error = retry
            raise ValueError(f"Repository API HTTP {error.code}; check access credentials and API rate limits") from None

    def _get(self, path: str):
        url = path if path.startswith("https://") else self.base + path
        if not url.startswith(self.base + "/"):
            raise ValueError("Unexpected API pagination address")
        headers = {"Accept": "application/json", "User-Agent": "LODGaZilla-plugin-manager"}
        if self.auth:
            headers["Authorization"] = self.auth
        try:
            opener = build_opener(NoRedirect(), HTTPSHandler(context=tls_context()))
            with opener.open(Request(url, headers=headers), timeout=20) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError("Plugin API response too large")
                return json.loads(raw)
        except HTTPError:
            raise
        except URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                raise ValueError("Cannot verify the repository API's certificate "
                                 f"({error.reason.verify_message})") from None
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
