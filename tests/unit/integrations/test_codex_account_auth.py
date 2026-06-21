import json
import subprocess
from pathlib import Path

from agentic_company.integrations.codex import account_auth


def test_codex_login_status_success_uses_user_scoped_home(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTIC_CODEX_AUTH_ROOT", str(tmp_path / "auth-root"))
    monkeypatch.setattr(account_auth, "resolve_codex_binary", lambda: "codex-test")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        auth_file = tmp_path / "auth-root" / "users" / "7" / "auth.json"
        auth_file.write_text(json.dumps({"auth_mode": "chatgpt"}), encoding="utf-8")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="Logged in", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    status = account_auth.codex_login_status(7)

    assert status.connected is True
    assert status.auth_mode == "chatgpt"
    assert calls[0][0] == ["codex-test", "login", "status"]
    assert Path(calls[0][1]["env"]["CODEX_HOME"]).parts[-2:] == ("users", "7")


def test_codex_login_status_failure_is_non_throwing(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTIC_CODEX_AUTH_ROOT", str(tmp_path / "auth-root"))
    monkeypatch.setattr(account_auth, "resolve_codex_binary", lambda: "codex-test")

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout="",
            stderr="not logged in",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    status = account_auth.codex_login_status(12)

    assert status.connected is False
    assert "not logged in" in status.message


def test_device_login_state_reads_output_without_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTIC_CODEX_AUTH_ROOT", str(tmp_path / "auth-root"))
    home = account_auth.ensure_codex_home_for_user(4)
    (home / account_auth.DEVICE_LOGIN_STATE).write_text(
        json.dumps(
            {
                "status": "running",
                "message": "Copy the code.",
                "output": "Open https://github.com/login/device and enter ABCD-1234",
            }
        ),
        encoding="utf-8",
    )

    state = account_auth.codex_device_login_state(4)

    assert state["status"] == "running"
    assert "ABCD-1234" in state["output"]
    assert "token" not in json.dumps(state).lower()


def test_start_codex_login_picks_browser_or_device_flow(monkeypatch, tmp_path):
    # local host -> `codex login` browser OAuth (VS Code-style, no code typing);
    # headless host -> `codex login --device-auth` (the device-code fallback).
    monkeypatch.setenv("AGENTIC_CODEX_AUTH_ROOT", str(tmp_path / "auth-root"))
    monkeypatch.setattr(account_auth, "resolve_codex_binary", lambda: "codex-test")
    captured = {}

    class _FakeProc:
        pid = 4321
        stdout = None  # capture thread skips reading; wait() returns immediately

        def wait(self):
            return 0

    monkeypatch.setattr(
        account_auth.subprocess,
        "Popen",
        lambda args, **kw: captured.update(args=args) or _FakeProc(),
    )

    state = account_auth.start_codex_login(5, device=False)
    assert captured["args"] == ["codex-test", "login"]
    assert state["flow"] == "browser"

    account_auth.start_codex_login(6, device=True)
    assert captured["args"] == ["codex-test", "login", "--device-auth"]
