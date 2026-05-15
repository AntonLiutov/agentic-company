"""Real browser checks powered by Playwright."""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

from agentic_company.agents.quality.commands import run_command_check
from agentic_company.agents.quality.models import CommandExecutor, QualityCheckResult


def run_playwright_live_chat(
    target_dir: Path,
    *,
    command_executor: CommandExecutor | None,
    timeout_seconds: int,
    commands_log_path: Path,
) -> QualityCheckResult:
    if not (target_dir / "app.py").exists():
        return failed_check("Playwright live chat E2E", "No app.py was generated.")
    if not (target_dir / "pyproject.toml").exists():
        return failed_check("Playwright live chat E2E", "No pyproject.toml was generated.")
    if not shutil.which("uv"):
        return failed_check("Playwright live chat E2E", "`uv` is not available on PATH.")
    if not required_env_present(target_dir):
        return failed_check(
            "Playwright live chat E2E",
            "OPENAI_API_KEY must be saved in generated-project/.env before live browser QA.",
        )

    script_path = _write_playwright_live_chat_script(commands_log_path.parent.resolve())
    result = run_command_check(
        "Playwright live chat E2E",
        [
            "uv",
            "run",
            "--with",
            "playwright",
            "python",
            str(script_path),
        ],
        target_dir,
        command_executor=command_executor,
        timeout_seconds=max(timeout_seconds, 420),
        commands_log_path=commands_log_path,
    )
    if result.status != "failed" or not looks_like_missing_browser(result.output):
        return result

    install = run_command_check(
        "Playwright browser install",
        ["uv", "run", "--with", "playwright", "python", "-m", "playwright", "install", "chromium"],
        target_dir,
        command_executor=command_executor,
        timeout_seconds=max(timeout_seconds, 420),
        commands_log_path=commands_log_path,
    )
    if install.status == "failed":
        return failed_check(
            "Playwright live chat E2E",
            "Chromium browser installation failed, so live browser QA could not run.",
            install.command,
            install.output,
        )

    script_path = _write_playwright_live_chat_script(commands_log_path.parent.resolve())
    return run_command_check(
        "Playwright live chat E2E",
        [
            "uv",
            "run",
            "--with",
            "playwright",
            "python",
            str(script_path),
        ],
        target_dir,
        command_executor=command_executor,
        timeout_seconds=max(timeout_seconds, 420),
        commands_log_path=commands_log_path,
    )


def looks_like_missing_browser(output: str) -> bool:
    lowered = output.lower()
    return "playwright install" in lowered or "executable doesn't exist" in lowered


def failed_check(
    name: str,
    details: str,
    command: list[str] | None = None,
    output: str = "",
) -> QualityCheckResult:
    return QualityCheckResult(
        name=name,
        status="failed",
        command=command or [],
        exit_code=None,
        details=details,
        output=output,
    )


def required_env_present(target_dir: Path) -> bool:
    env_path = target_dir / ".env"
    if not env_path.exists():
        return False
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("OPENAI_API_KEY="):
            return bool(stripped.split("=", 1)[1].strip())
    return False


def _write_playwright_live_chat_script(evidence_dir: Path) -> Path:
    script_path = evidence_dir / "scripts" / "playwright_live_chat_e2e.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(_playwright_live_chat_script(evidence_dir), encoding="utf-8")
    return script_path


def _playwright_live_chat_script(evidence_dir: Path) -> str:
    script = textwrap.dedent(
        r"""
        import json
        import os
        import socket
        import subprocess
        import sys
        import time
        import urllib.request
        from pathlib import Path

        from playwright.sync_api import sync_playwright

        def load_dotenv(path):
            if not path.exists():
                return
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

        def free_port():
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                return sock.getsockname()[1]

        port = free_port()
        url = f"http://127.0.0.1:{port}"
        load_dotenv(Path(".env"))
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise AssertionError("OPENAI_API_KEY is missing from .env")

        env = os.environ.copy()
        env.setdefault("DEFAULT_MODEL", "gpt-4o-mini")

        def find_chat_input(page):
            placeholders = [
                "Message the assistant",
                "Send a message",
                "Type your message",
                "Message",
            ]
            for placeholder in placeholders:
                candidate = page.get_by_placeholder(placeholder)
                try:
                    candidate.wait_for(state="visible", timeout=5000)
                    return candidate
                except Exception:
                    pass

            candidate = page.locator(
                'textarea[data-testid="stChatInputTextArea"], '
                '[data-testid="stChatInput"] textarea'
            ).first
            candidate.wait_for(state="visible", timeout=10000)
            return candidate

        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                "app.py",
                "--server.headless=true",
                f"--server.port={port}",
                "--browser.gatherUsageStats=false",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )

        try:
            for _ in range(60):
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if response.status < 500:
                            break
                except Exception:
                    time.sleep(0.5)
            else:
                raise AssertionError("Streamlit server did not become ready in 30 seconds")

            qa_dir = Path(__QA_EVIDENCE_DIR__)
            screenshots = qa_dir / "screenshots"
            browser_dir = qa_dir / "browser"
            screenshots.mkdir(parents=True, exist_ok=True)
            browser_dir.mkdir(parents=True, exist_ok=True)

            prompt = "Could you briefly explain what this chat app can do?"

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.get_by_text("Simple LLM Chat").wait_for(timeout=30000)
                chat_input = find_chat_input(page)
                page.wait_for_load_state("networkidle", timeout=30000)
                page.wait_for_timeout(500)
                page.screenshot(
                    path=str(screenshots / "playwright-before-chat.png"),
                    full_page=True,
                )
                body_text = page.locator("body").inner_text(timeout=10000)

                chat_input.fill(prompt)
                chat_input.press("Enter")

                page.get_by_text(prompt, exact=True).wait_for(timeout=20000)
                assistant_response_script = (
                    "(prompt) => {"
                    " const messages = Array.from("
                    "document.querySelectorAll('[data-testid=\"stChatMessage\"]')"
                    ");"
                    " if (messages.length < 2) return false;"
                    " const lastText = messages[messages.length - 1].innerText.trim();"
                    " return lastText.length > 20"
                    " && !lastText.includes(prompt)"
                    " && !lastText.includes('Thinking');"
                    "}"
                )
                page.wait_for_function(
                    assistant_response_script,
                    arg=prompt,
                    timeout=90000,
                )
                page.wait_for_timeout(500)
                after_text = page.locator("body").inner_text(timeout=10000)
                assistant_text = page.locator('[data-testid="stChatMessage"]').last.inner_text(
                    timeout=10000
                )
                page.screenshot(path=str(screenshots / "playwright-chat.png"), full_page=True)
                browser.close()

            assert body_text.strip(), "Rendered page did not expose visible content"
            assert prompt in after_text, "User chat prompt was not visible after submission"
            assert assistant_text.strip(), "Assistant response was empty"
            assert "OpenAI request failed" not in after_text, "Live OpenAI request failed"
            assert "Unexpected error:" not in after_text, "Generated app raised an unexpected error"
            assert "LLM request failed" not in after_text, "Generated app reported an LLM failure"
            assert (
                "I could not get a response from the LLM" not in after_text
            ), "Generated app reported a missing LLM response"

            transcript = {
                "url": url,
                "prompt": prompt,
                "assistant_text": assistant_text.strip(),
                "before_text_length": len(body_text),
                "after_text_length": len(after_text),
                "screenshots": [
                    "qa/screenshots/playwright-before-chat.png",
                    "qa/screenshots/playwright-chat.png",
                ],
            }
            (browser_dir / "chat-transcript.json").write_text(
                json.dumps(transcript, indent=2) + "\n",
                encoding="utf-8",
            )
            print("Playwright live chat E2E completed")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        """
    ).strip()
    return script.replace("__QA_EVIDENCE_DIR__", json.dumps(str(evidence_dir)))
