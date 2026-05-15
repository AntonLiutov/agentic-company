"""Docker-related QA checks."""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

from agentic_company.agents.quality.commands import run_command_check, skipped_check
from agentic_company.agents.quality.models import CommandExecutor, QualityCheckResult
from agentic_company.agents.quality.playwright_checks import (
    failed_check,
    looks_like_missing_browser,
    required_env_present,
)

DEV_DOCKER_COMPOSE_PROJECT = "agentic_qa_generated_dev"


def run_docker_compose_config(
    target_dir: Path,
    *,
    command_executor: CommandExecutor | None,
    timeout_seconds: int,
    commands_log_path: Path,
) -> QualityCheckResult:
    if not _has_compose_file(target_dir):
        return skipped_check("Docker Compose config", "No Docker Compose file was generated.")
    if not shutil.which("docker"):
        return skipped_check("Docker Compose config", "`docker` is not available on PATH.")

    return run_command_check(
        "Docker Compose config",
        ["docker", "compose", "config"],
        target_dir,
        command_executor=command_executor,
        timeout_seconds=timeout_seconds,
        commands_log_path=commands_log_path,
    )


def run_docker_runtime_e2e(
    target_dir: Path,
    *,
    command_executor: CommandExecutor | None,
    timeout_seconds: int,
    commands_log_path: Path,
) -> QualityCheckResult:
    if not _has_compose_file(target_dir):
        return skipped_check("Docker runtime E2E", "No Docker Compose file was generated.")
    if not shutil.which("docker"):
        return skipped_check("Docker runtime E2E", "`docker` is not available on PATH.")
    if not shutil.which("uv"):
        return failed_check("Docker runtime E2E", "`uv` is not available on PATH.")
    if not required_env_present(target_dir):
        return failed_check(
            "Docker runtime E2E",
            "OPENAI_API_KEY must be saved in generated-project/.env before Docker QA.",
        )

    script_path = _write_docker_runtime_script(commands_log_path.parent.resolve())
    result = run_command_check(
        "Docker runtime E2E",
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
        timeout_seconds=max(timeout_seconds, 3600),
        commands_log_path=commands_log_path,
    )
    if result.status != "failed" or not looks_like_missing_browser(result.output):
        return result

    install = run_command_check(
        "Playwright browser install for Docker QA",
        ["uv", "run", "--with", "playwright", "python", "-m", "playwright", "install", "chromium"],
        target_dir,
        command_executor=command_executor,
        timeout_seconds=max(timeout_seconds, 900),
        commands_log_path=commands_log_path,
    )
    if install.status == "failed":
        return failed_check(
            "Docker runtime E2E",
            "Chromium browser installation failed, so Docker browser QA could not run.",
            install.command,
            install.output,
        )

    script_path = _write_docker_runtime_script(commands_log_path.parent.resolve())
    return run_command_check(
        "Docker runtime E2E",
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
        timeout_seconds=max(timeout_seconds, 3600),
        commands_log_path=commands_log_path,
    )


def _has_compose_file(target_dir: Path) -> bool:
    return any((target_dir / name).exists() for name in ("docker-compose.yml", "compose.yml"))


def _write_docker_runtime_script(evidence_dir: Path) -> Path:
    script_path = evidence_dir / "scripts" / "docker_runtime_e2e.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(_docker_runtime_script(evidence_dir), encoding="utf-8")
    return script_path


def _docker_runtime_script(evidence_dir: Path) -> str:
    script = textwrap.dedent(
        r"""
        import json
        import os
        import queue
        import subprocess
        import threading
        import time
        import urllib.request
        from pathlib import Path

        from playwright.sync_api import sync_playwright

        qa_dir = Path(__QA_EVIDENCE_DIR__)
        screenshots = qa_dir / "screenshots"
        browser_dir = qa_dir / "browser"
        docker_dir = qa_dir / "docker"
        screenshots.mkdir(parents=True, exist_ok=True)
        browser_dir.mkdir(parents=True, exist_ok=True)
        docker_dir.mkdir(parents=True, exist_ok=True)
        command_log = docker_dir / "runtime-command.log"

        compose_project = __DEV_DOCKER_COMPOSE_PROJECT__
        compose = ["docker", "compose", "-p", compose_project]

        def kill_process_tree(process):
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
            else:
                process.kill()

        def run_command(command, timeout):
            command_log.parent.mkdir(parents=True, exist_ok=True)
            with command_log.open("a", encoding="utf-8") as handle:
                handle.write("$ " + " ".join(command) + "\n")
                handle.flush()

            process = subprocess.Popen(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )
            try:
                output_lines = []
                output_queue = queue.Queue()
                deadline = time.monotonic() + timeout

                def read_output():
                    assert process.stdout is not None
                    for line in process.stdout:
                        output_queue.put(line)
                    output_queue.put(None)

                reader = threading.Thread(target=read_output, daemon=True)
                reader.start()
                with command_log.open("a", encoding="utf-8") as handle:
                    while True:
                        try:
                            line = output_queue.get(timeout=0.2)
                        except queue.Empty:
                            line = ""

                        if line is None:
                            break
                        if line:
                            output_lines.append(line)
                            handle.write(line)
                            handle.flush()
                            print(line.rstrip())

                        if time.monotonic() > deadline:
                            raise subprocess.TimeoutExpired(command, timeout, "".join(output_lines))
                process.wait(timeout=5)
                output = "".join(output_lines)
            except subprocess.TimeoutExpired as exc:
                output = exc.stdout or ""
                kill_process_tree(process)
                with command_log.open("a", encoding="utf-8") as handle:
                    if output:
                        handle.write(str(output))
                    handle.write(f"\nTIMEOUT after {timeout}s\n\n")
                    handle.flush()
                raise AssertionError(
                    f"Command timed out after {timeout}s: {' '.join(command)}"
                ) from exc

            with command_log.open("a", encoding="utf-8") as handle:
                handle.write(f"\nexit_code={process.returncode}\n\n")
                handle.flush()

            print("$ " + " ".join(command))
            if process.returncode != 0:
                raise AssertionError(
                    f"Command failed with exit code {process.returncode}: {' '.join(command)}"
                )
            return output

        def compose_service():
            output = run_command([*compose, "config", "--services"], timeout=60)
            services = [line.strip() for line in output.splitlines() if line.strip()]
            if not services:
                raise AssertionError("Docker Compose did not report any services")
            return services[0]

        def exposed_url(service):
            output = run_command([*compose, "port", service, "8501"], timeout=60)
            endpoint = output.strip().splitlines()[-1].strip()
            host, port = endpoint.rsplit(":", 1)
            if host in {"0.0.0.0", "::"}:
                host = "127.0.0.1"
            return f"http://{host}:{port}"

        def wait_for_app(url):
            for _ in range(180):
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if response.status < 500:
                            return
                except Exception:
                    time.sleep(1)
            raise AssertionError(f"Containerized app did not become ready at {url}")

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

        prompt = "Could you briefly explain what this chat app can do?"
        service = compose_service()

        try:
            run_command([*compose, "up", "--build", "-d"], timeout=3600)
            url = exposed_url(service)
            wait_for_app(url)

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.get_by_text("Simple LLM Chat").wait_for(timeout=30000)
                chat_input = find_chat_input(page)
                page.wait_for_load_state("networkidle", timeout=30000)
                page.wait_for_timeout(500)
                page.screenshot(
                    path=str(screenshots / "docker-before-chat.png"),
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
                page.screenshot(path=str(screenshots / "docker-chat.png"), full_page=True)
                browser.close()

            assert body_text.strip(), "Rendered Docker page did not expose visible content"
            assert prompt in after_text, "User chat prompt was not visible in Docker app"
            assert assistant_text.strip(), "Docker assistant response was empty"
            assert "OpenAI request failed" not in after_text, "Docker live OpenAI request failed"
            assert "Unexpected error:" not in after_text, "Docker generated app raised an error"
            assert (
                "LLM request failed" not in after_text
            ), "Docker generated app reported LLM failure"
            assert (
                "I could not get a response from the LLM" not in after_text
            ), "Docker generated app reported a missing LLM response"

            transcript = {
                "url": url,
                "service": service,
                "compose_project": compose_project,
                "prompt": prompt,
                "assistant_text": assistant_text.strip(),
                "before_text_length": len(body_text),
                "after_text_length": len(after_text),
                "screenshots": [
                    "qa/screenshots/docker-before-chat.png",
                    "qa/screenshots/docker-chat.png",
                ],
            }
            (browser_dir / "docker-chat-transcript.json").write_text(
                json.dumps(transcript, indent=2) + "\n",
                encoding="utf-8",
            )
            print("Docker runtime E2E completed")
        finally:
            logs = subprocess.run(
                [*compose, "logs", "--no-color"],
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
            )
            (docker_dir / "compose.log").write_text(
                ((logs.stdout or "") + (logs.stderr or "")).strip() + "\n",
                encoding="utf-8",
            )
            subprocess.run(
                [*compose, "down", "--remove-orphans"],
                text=True,
                capture_output=True,
                timeout=180,
                check=False,
            )
        """
    ).strip()
    return script.replace("__QA_EVIDENCE_DIR__", json.dumps(str(evidence_dir))).replace(
        "__DEV_DOCKER_COMPOSE_PROJECT__",
        json.dumps(DEV_DOCKER_COMPOSE_PROJECT),
    )
