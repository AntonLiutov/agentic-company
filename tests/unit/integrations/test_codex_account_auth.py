import json
import subprocess
from pathlib import Path

from agentic_company.integrations.codex import account_auth


def test_codex_home_is_absolute_even_with_relative_auth_root(monkeypatch):
    # Regression: a relative AGENTIC_CODEX_AUTH_ROOT (e.g. "data/codex-auth") must still
    # yield an ABSOLUTE codex home. It is passed as CODEX_HOME *and* as the subprocess cwd;
    # codex resolves a relative CODEX_HOME against that cwd, doubling the path so it "does
    # not exist" and `codex login` dies on startup before printing its auth URL.
    monkeypatch.setenv("AGENTIC_CODEX_AUTH_ROOT", "data/codex-auth")
    home = account_auth.codex_home_for_user(3)
    assert home.is_absolute()
    assert home.parts[-2:] == ("users", "3")


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


def test_device_login_state_reads_clean_login_fields_without_raw_output(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTIC_CODEX_AUTH_ROOT", str(tmp_path / "auth-root"))
    home = account_auth.ensure_codex_home_for_user(4)
    (home / account_auth.DEVICE_LOGIN_STATE).write_text(
        json.dumps(
            {
                "status": "running",
                "message": "Copy the code.",
                "auth_url": "https://github.com/login/device",
                "user_code": "ABCD-1234",
            }
        ),
        encoding="utf-8",
    )

    state = account_auth.codex_device_login_state(4)

    assert state["status"] == "running"
    assert state["auth_url"] == "https://github.com/login/device"
    assert state["user_code"] == "ABCD-1234"
    assert "output" not in state
    assert "token" not in json.dumps(state).lower()


def test_parse_login_output_extracts_device_url_and_code():
    out = (
        "Welcome to Codex.\n"
        "To sign in, open https://auth.openai.com/codex/device\n"
        "and enter the code WXYZ-1234 to authorize this device.\n"
    )
    parsed = account_auth._parse_login_output(out)
    assert parsed["auth_url"] == "https://auth.openai.com/codex/device"
    assert parsed["user_code"] == "WXYZ-1234"


def test_parse_login_output_strips_ansi_and_reads_4_5_device_code():
    # Real codex 0.141 COLORS its device-login output: the URL + code are wrapped in
    # \x1b[94m…\x1b[0m, and the code is 4-5 (e.g. 0SAE-KWF9Q). Without stripping ANSI the URL
    # keeps a trailing reset (a broken link) and the code's \b boundary breaks (empty code).
    out = (
        "1. Open this link in your browser\n"
        "   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n"
        "2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m\n"
        "   \x1b[94m0SAE-KWF9Q\x1b[0m\n"
    )
    parsed = account_auth._parse_login_output(out)
    assert parsed["auth_url"] == "https://auth.openai.com/codex/device"
    assert parsed["user_code"] == "0SAE-KWF9Q"


def test_parse_login_output_browser_flow_uses_authorize_url_no_code():
    # The clunky raw browser-flow dump the user pasted: a localhost server line + a giant
    # oauth/authorize URL. We surface the authorize URL as a button and find no device code.
    out = (
        "Starting local login server on http://localhost:1455.\n"
        "If your browser did not open, navigate to this URL to authenticate:\n"
        "https://auth.openai.com/oauth/authorize?response_type=code"
        "&client_id=app_EMoamEEZ73f0CkXaXp7hrann"
        "&code_challenge=fmow3WY-0frYFMPVohqpg1J5J9-uPitF4bUGc8ZpzsM\n"
        "On a remote or headless machine? Use `codex login --device-auth` instead.\n"
    )
    parsed = account_auth._parse_login_output(out)
    assert parsed["auth_url"].startswith("https://auth.openai.com/oauth/authorize")
    assert "localhost" not in parsed["auth_url"]
    assert parsed["user_code"] == ""


def test_device_login_capture_writes_clean_url_and_code(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTIC_CODEX_AUTH_ROOT", str(tmp_path / "auth-root"))
    home = account_auth.ensure_codex_home_for_user(8)
    state_path = home / account_auth.DEVICE_LOGIN_STATE
    # seed the flow marker that start_codex_login would have written
    state_path.write_text(json.dumps({"flow": "device", "pid": "999"}), encoding="utf-8")

    account_auth._write_device_state(
        state_path,
        "running",
        ["Open https://auth.openai.com/codex/device", "Code: ABCD-9999"],
    )
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["auth_url"] == "https://auth.openai.com/codex/device"
    assert payload["user_code"] == "ABCD-9999"
    assert "output" not in payload
    assert payload["flow"] == "device"  # preserved across rewrites
    assert payload["pid"] == "999"


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
