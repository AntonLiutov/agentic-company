import io
import json

import pytest

from agentic_company.console.web import github_oauth as g


def test_is_configured_requires_both_env(monkeypatch):
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_SECRET", raising=False)
    assert g.is_configured() is False
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "Ov23abc")
    assert g.is_configured() is False  # secret still missing
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "s" * 40)
    assert g.is_configured() is True


def test_build_authorize_url_carries_client_scope_state_and_callback(monkeypatch):
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "Ov23abc")
    url = g.build_authorize_url("http://127.0.0.1:8503/auth/github/callback", "state-xyz")
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=Ov23abc" in url
    assert "scope=repo" in url
    assert "state=state-xyz" in url
    assert "auth%2Fgithub%2Fcallback" in url  # redirect_uri is url-encoded


def test_exchange_code_for_token_parses_access_token(monkeypatch):
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "Ov23abc")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "s" * 40)

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        # token endpoint must be called with Accept: application/json
        assert req.headers.get("Accept") == "application/json"
        return _Resp(json.dumps({"access_token": "gho_secret", "token_type": "bearer"}).encode())

    monkeypatch.setattr(g.urllib.request, "urlopen", fake_urlopen)
    assert g.exchange_code_for_token("the-code", "http://x/cb") == "gho_secret"


def test_exchange_raises_when_no_token(monkeypatch):
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "Ov23abc")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "s" * 40)

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        g.urllib.request,
        "urlopen",
        lambda req, timeout=0: _Resp(json.dumps({"error_description": "bad_verification_code"}).encode()),
    )
    with pytest.raises(g.GitHubOAuthError):
        g.exchange_code_for_token("bad", "http://x/cb")


def test_list_and_create_repo_map_fields(monkeypatch):
    monkeypatch.setattr(
        g,
        "_api_request",
        lambda method, path, token, body=None: (
            [{"full_name": "me/app", "name": "app", "private": True, "default_branch": "main"}]
            if method == "GET"
            else {"full_name": "me/new", "name": "new", "private": False, "default_branch": "main"}
        ),
    )
    repos = g.list_repos("tok")
    assert repos[0].full_name == "me/app" and repos[0].private is True
    created = g.create_repo("tok", "new", private=False)
    assert created.full_name == "me/new" and created.private is False
