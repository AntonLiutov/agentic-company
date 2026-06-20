"""GitHub OAuth (web application flow) for the console — login + repo list/create.

The OAuth App credentials live in the host environment
(``GITHUB_OAUTH_CLIENT_ID`` / ``GITHUB_OAUTH_CLIENT_SECRET``); the per-user access
token is stored encrypted via the provider-credential store (``provider="github_oauth"``)
exactly like the OpenAI/Gemini keys. The client secret and the access token are never
logged. Uses only the standard library so it adds no dependency.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
API_BASE = "https://api.github.com"
SCOPE = "repo,project,read:org"  # clone/PR/merge + Projects board (read:org = gh owner-type resolution)
PROVIDER = "github_oauth"
LOGIN_PROVIDER = "github_login"  # stores the resolved username (non-secret) for display
_UA = "ADL-Console"
_TIMEOUT = 20


class GitHubOAuthError(RuntimeError):
    """A GitHub OAuth / API call failed."""


def client_id() -> str:
    return (os.environ.get("GITHUB_OAUTH_CLIENT_ID") or "").strip()


def _client_secret() -> str:
    return (os.environ.get("GITHUB_OAUTH_CLIENT_SECRET") or "").strip()


def is_configured() -> bool:
    """True when the operator registered an OAuth App and set both env vars."""
    return bool(client_id() and _client_secret())


def build_authorize_url(redirect_uri: str, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id(),
            "redirect_uri": redirect_uri,
            "scope": SCOPE,
            "state": state,
            "allow_signup": "false",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code_for_token(code: str, redirect_uri: str) -> str:
    """Exchange the OAuth ``code`` for a user access token. Never logs the token."""
    payload = urllib.parse.urlencode(
        {
            "client_id": client_id(),
            "client_secret": _client_secret(),
            "code": code,
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=payload,
        headers={"Accept": "application/json", "User-Agent": _UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise GitHubOAuthError(f"token exchange failed: {exc}") from exc
    token = str(body.get("access_token") or "")
    if not token:
        raise GitHubOAuthError(str(body.get("error_description") or "no access token returned"))
    return token


@dataclass(frozen=True, slots=True)
class Repo:
    full_name: str
    name: str
    private: bool
    default_branch: str


def _api_request(method: str, path: str, token: str, body: dict | None = None) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": _UA,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API_BASE}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = ""
        try:
            message = str(json.loads(exc.read().decode("utf-8")).get("message") or "")
        except Exception:
            pass
        raise GitHubOAuthError(message or f"GitHub API {method} {path} failed ({exc.code})") from exc
    except urllib.error.URLError as exc:
        raise GitHubOAuthError(f"GitHub API {method} {path} failed: {exc}") from exc


def viewer_login(token: str) -> str:
    """The authenticated user's GitHub login (for display / repo owner default)."""
    return str(_api_request("GET", "/user", token).get("login") or "")


def list_repos(token: str, *, limit: int = 100) -> list[Repo]:
    """Repos the user owns, most-recently-updated first."""
    data = _api_request(
        "GET",
        f"/user/repos?per_page={min(limit, 100)}&sort=updated&affiliation=owner",
        token,
    )
    repos = [
        Repo(
            full_name=str(r.get("full_name") or ""),
            name=str(r.get("name") or ""),
            private=bool(r.get("private")),
            default_branch=str(r.get("default_branch") or "main"),
        )
        for r in (data or [])
        if r.get("full_name")
    ]
    return repos


def create_repo(token: str, name: str, *, private: bool = True) -> Repo:
    """Create a new repo for the authenticated user (auto-initialised so it is clonable)."""
    r = _api_request(
        "POST",
        "/user/repos",
        token,
        body={"name": name, "private": bool(private), "auto_init": True},
    )
    return Repo(
        full_name=str(r.get("full_name") or ""),
        name=str(r.get("name") or ""),
        private=bool(r.get("private")),
        default_branch=str(r.get("default_branch") or "main"),
    )
