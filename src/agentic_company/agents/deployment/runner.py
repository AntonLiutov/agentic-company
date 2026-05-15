"""Azure Container Apps deployment runner for generated projects."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import textwrap
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from agentic_company.agents.deployment.planner import (
    DEPLOYMENT_REQUEST_JSON,
    DEPLOYMENT_REQUEST_MARKDOWN,
    build_deployment_request,
    render_deployment_request,
)
from agentic_company.integrations.azure.container_apps import (
    container_app_create_command,
    container_app_registry_set_command,
    container_app_secret_set_command,
    container_app_update_image_env_command,
)
from agentic_company.integrations.commands import (
    StreamedCommand,
    append_completed_command_log,
    stream_command,
)
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.security import redact_sensitive_output

LOGGER = logging.getLogger(__name__)

DEPLOYMENT_SUMMARY_MARKDOWN = "13-deployment-summary.md"
DEPLOYMENT_COMMAND_LOG = "deployment/commands.log"

CommandExecutor = Callable[
    [Sequence[str], Path, int],
    subprocess.CompletedProcess[str],
]


@dataclass(slots=True)
class DeploymentStep:
    name: str
    status: str
    details: str
    exit_code: int | None = None
    output: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class AzureDeploymentRunner:
    """Deploy a generated Dockerized project to Azure Container Apps."""

    def __init__(
        self,
        *,
        command_executor: CommandExecutor | None = None,
        timeout_seconds: int = 1800,
    ) -> None:
        self.command_executor = command_executor
        self.timeout_seconds = timeout_seconds
        self._command_log_path: Path | None = None

    def run(self, run_dir: Path) -> AgentRunResult:
        from agentic_company.agents.deployment.graph import run_deployment_workflow_graph

        return run_deployment_workflow_graph(run_dir, self)

    def _run_post_deploy_qa(
        self,
        run_dir: Path,
        cwd: Path,
        public_url: str,
        steps: list[DeploymentStep],
    ) -> bool:
        script_path = _write_post_deploy_qa_script(run_dir, public_url)
        result = self._run_step(
            "Post-deployment chatbot QA",
            ["uv", "run", "--with", "playwright", "python", str(script_path)],
            cwd,
            steps,
        )
        if result or not _looks_like_missing_browser(steps[-1].output):
            return result

        install_ok = self._run_step(
            "Install Playwright Chromium for post-deploy QA",
            [
                "uv",
                "run",
                "--with",
                "playwright",
                "python",
                "-m",
                "playwright",
                "install",
                "chromium",
            ],
            cwd,
            steps,
        )
        if not install_ok:
            return False
        return self._run_step(
            "Post-deployment chatbot QA",
            ["uv", "run", "--with", "playwright", "python", str(script_path)],
            cwd,
            steps,
        )

    def _ensure_resource(
        self,
        cwd: Path,
        steps: list[DeploymentStep],
        *,
        check_name: str,
        create_name: str,
        check_command: list[str],
        create_command: list[str],
    ) -> bool:
        if self._resource_exists(check_name, check_command, cwd, steps):
            return True
        return self._run_step(create_name, create_command, cwd, steps)

    def _resource_exists(
        self,
        name: str,
        command: list[str],
        cwd: Path,
        steps: list[DeploymentStep],
    ) -> bool:
        result = self._execute(command, cwd, heading=name)
        output = _combined_output(result)
        if result.returncode == 0:
            status = "passed"
            details = "Resource exists; reusing it."
            exists = True
        else:
            status = "not_found"
            details = "Resource was not found; creating it next."
            exists = False
        step = DeploymentStep(
            name=name,
            status=status,
            details=details,
            exit_code=result.returncode,
            output=redact_sensitive_output(output),
        )
        steps.append(step)
        self._append_command_result(step, command, cwd)
        LOGGER.info("Deployment resource check completed name=%s status=%s", name, status)
        return exists

    def _create_container_app(
        self,
        cwd: Path,
        steps: list[DeploymentStep],
        *,
        app_name: str,
        resource_group: str,
        environment_name: str,
        registry_server: str,
        registry_username: str,
        registry_password: str,
        image: str,
        env_values: dict[str, str],
    ) -> bool:
        return self._run_step(
            "Create Container App",
            container_app_create_command(
                app_name=app_name,
                resource_group=resource_group,
                environment_name=environment_name,
                registry_server=registry_server,
                registry_username=registry_username,
                registry_password=registry_password,
                image=image,
                env_values=env_values,
            ),
            cwd,
            steps,
            sensitive=True,
        )

    def _update_container_app(
        self,
        cwd: Path,
        steps: list[DeploymentStep],
        *,
        app_name: str,
        resource_group: str,
        registry_server: str,
        registry_username: str,
        registry_password: str,
        image: str,
        env_values: dict[str, str],
    ) -> bool:
        commands = [
            (
                "Update Container App registry",
                container_app_registry_set_command(
                    app_name=app_name,
                    resource_group=resource_group,
                    registry_server=registry_server,
                    registry_username=registry_username,
                    registry_password=registry_password,
                ),
                True,
            ),
            (
                "Update Container App secrets",
                container_app_secret_set_command(
                    app_name=app_name,
                    resource_group=resource_group,
                    env_values=env_values,
                ),
                True,
            ),
            (
                "Update Container App image and env",
                container_app_update_image_env_command(
                    app_name=app_name,
                    resource_group=resource_group,
                    image=image,
                    env_values=env_values,
                ),
                False,
            ),
        ]
        for name, command, sensitive in commands:
            if not self._run_step(name, command, cwd, steps, sensitive=sensitive):
                return False
        return True

    def _run_step(
        self,
        name: str,
        command: list[str],
        cwd: Path,
        steps: list[DeploymentStep],
        *,
        allow_failure: bool = False,
        sensitive: bool = False,
    ) -> bool:
        result = self._execute(command, cwd, heading=name, sensitive=sensitive)
        status = "passed" if result.returncode == 0 else "skipped" if allow_failure else "failed"
        output = "" if sensitive else _combined_output(result)
        step = DeploymentStep(
            name=name,
            status=status,
            details=_step_details(result, output, allow_failure=allow_failure),
            exit_code=result.returncode,
            output=redact_sensitive_output(output),
        )
        steps.append(step)
        self._append_command_result(step, command, cwd)
        _log_step_result(name, status, result.returncode, output)
        return result.returncode == 0

    def _run_json_step(
        self,
        name: str,
        command: list[str],
        cwd: Path,
        steps: list[DeploymentStep],
        *,
        sensitive: bool = False,
    ) -> dict[str, object] | None:
        result = self._execute(command, cwd, heading=name, sensitive=sensitive)
        output = "" if sensitive else _combined_output(result)
        status = "passed" if result.returncode == 0 else "failed"
        step = DeploymentStep(
            name=name,
            status=status,
            details=_step_details(result, output),
            exit_code=result.returncode,
            output=redact_sensitive_output(output),
        )
        steps.append(step)
        self._append_command_result(step, command, cwd)
        if result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            steps.append(
                DeploymentStep(
                    name=name,
                    status="failed",
                    details="Command did not return valid JSON.",
                    output=redact_sensitive_output(output),
                )
            )
            return None
        return payload if isinstance(payload, dict) else None

    def _run_text_step(
        self,
        name: str,
        command: list[str],
        cwd: Path,
        steps: list[DeploymentStep],
    ) -> str:
        result = self._execute(command, cwd, heading=name)
        output = _combined_output(result)
        status = "passed" if result.returncode == 0 else "failed"
        step = DeploymentStep(
            name=name,
            status=status,
            details=_step_details(result, output),
            exit_code=result.returncode,
            output=redact_sensitive_output(output),
        )
        steps.append(step)
        self._append_command_result(step, command, cwd)
        return (result.stdout or "").strip() if result.returncode == 0 else ""

    def _execute(
        self,
        command: Sequence[str],
        cwd: Path,
        *,
        heading: str | None = None,
        sensitive: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        LOGGER.info("Deployment command started command=%s cwd=%s", _safe_command(command), cwd)
        if self.command_executor:
            return self.command_executor(command, cwd, self.timeout_seconds)
        resolved_command = _resolve_command(command)
        return stream_command(
            StreamedCommand(
                command=resolved_command,
                cwd=cwd,
                timeout_seconds=self.timeout_seconds,
                log_path=self._command_log_path,
                heading=heading,
                display_command=_safe_command(command),
                sensitive_output=sensitive,
                redactor=redact_sensitive_output,
            )
        )

    def _append_command_result(
        self,
        step: DeploymentStep,
        command: Sequence[str],
        cwd: Path,
    ) -> None:
        if not self._command_log_path:
            return
        if self.command_executor:
            append_completed_command_log(
                log_path=self._command_log_path,
                command=command,
                cwd=cwd,
                exit_code=step.exit_code,
                output=step.output,
                display_command=_safe_command(command),
                status=step.status,
                details=step.details,
                redactor=redact_sensitive_output,
            )
            return
        with self._command_log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"status={step.status}\n")
            handle.write(f"exit_code={step.exit_code}\n")
            handle.write(f"details={step.details}\n")
            handle.write("\n")


def render_deployment_summary(payload: dict[str, object]) -> str:
    steps = payload.get("steps", [])
    step_lines = []
    failure_output = []
    if isinstance(steps, list):
        step_lines = [
            f"| {step.get('status', '')} | {step.get('name', '')} | {step.get('details', '')} |"
            for step in steps
            if isinstance(step, dict)
        ]
        failure_output = [
            _render_step_output(step)
            for step in steps
            if isinstance(step, dict)
            and step.get("status") == "failed"
            and str(step.get("output", "")).strip()
        ]

    public_url = payload.get("public_url") or "not available"
    teardown = payload.get("teardown_command") or "not available"
    account = payload.get("azure_account", {})
    account_lines = []
    if isinstance(account, dict):
        account_lines = [
            f"- {key}: `{value}`" for key, value in account.items() if str(value).strip()
        ]

    return f"""# Deployment Summary

Status: {payload.get("status", "unknown")}

Target: `{payload.get("target", "azure-container-apps")}`

Target project:
`{payload.get("target_project_dir", "")}`

Public URL:
{public_url}

## Azure Account

{chr(10).join(account_lines) or "- Azure account was not resolved."}

## Resources

- Resource group: `{payload.get("resource_group", "not created")}`
- Container app environment: `{payload.get("container_app_environment", "not created")}`
- Container app: `{payload.get("container_app_name", "not created")}`
- Image: `{payload.get("image", "not pushed")}`

## Steps

| Status | Step | Details |
| --- | --- | --- |
{chr(10).join(step_lines) or "| blocked | No steps ran | Deployment did not start. |"}

{_render_failure_output_section(failure_output)}

## Teardown

```powershell
{teardown}
```
"""


def _load_or_create_deployment_request(run_dir: Path, target_dir: Path) -> dict[str, object]:
    request_path = run_dir / DEPLOYMENT_REQUEST_JSON
    if request_path.exists():
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    payload = build_deployment_request(target_dir)
    request_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / DEPLOYMENT_REQUEST_MARKDOWN).write_text(
        render_deployment_request(payload),
        encoding="utf-8",
    )
    return payload


def _request_inputs(request: dict[str, object]) -> dict[str, object]:
    inputs = request.get("inputs", {})
    return inputs if isinstance(inputs, dict) else {}


def _load_required_env_values(env_path: Path, keys: list[str]) -> dict[str, str]:
    values: dict[str, str] = {key: "" for key in keys}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key in values:
            values[key] = value.strip().strip('"').strip("'")
    return values


def _write_post_deploy_qa_script(run_dir: Path, public_url: str) -> Path:
    script_path = run_dir / "deployment" / "scripts" / "post_deploy_chat_qa.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        _post_deploy_qa_script(run_dir / "deployment", public_url), encoding="utf-8"
    )
    return script_path


def _post_deploy_qa_script(evidence_dir: Path, public_url: str) -> str:
    script = textwrap.dedent(
        r"""
        import json
        import time
        import urllib.request
        from pathlib import Path

        from playwright.sync_api import sync_playwright

        evidence_dir = Path(__EVIDENCE_DIR__)
        screenshots = evidence_dir / "screenshots"
        browser_dir = evidence_dir / "browser"
        screenshots.mkdir(parents=True, exist_ok=True)
        browser_dir.mkdir(parents=True, exist_ok=True)

        url = __PUBLIC_URL__
        prompt = "Could you briefly confirm this deployed chat app is working?"

        def wait_for_app():
            for _ in range(180):
                try:
                    with urllib.request.urlopen(url, timeout=2) as response:
                        if response.status < 500:
                            return
                except Exception:
                    time.sleep(1)
            raise AssertionError(f"Deployed app did not become ready at {url}")

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
            candidate.wait_for(state="visible", timeout=15000)
            return candidate

        wait_for_app()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            chat_input = find_chat_input(page)
            page.wait_for_load_state("networkidle", timeout=60000)
            page.wait_for_timeout(1000)
            page.screenshot(path=str(screenshots / "post-deploy-before-chat.png"), full_page=True)
            before_text = page.locator("body").inner_text(timeout=10000)

            chat_input.fill(prompt)
            chat_input.press("Enter")
            page.get_by_text(prompt, exact=True).wait_for(timeout=30000)
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
                timeout=120000,
            )
            page.wait_for_timeout(1000)
            after_text = page.locator("body").inner_text(timeout=10000)
            assistant_text = page.locator('[data-testid="stChatMessage"]').last.inner_text(
                timeout=10000
            )
            page.screenshot(path=str(screenshots / "post-deploy-chat.png"), full_page=True)
            browser.close()

        assert before_text.strip(), "Deployed app did not expose visible content"
        assert prompt in after_text, "User chat prompt was not visible after submission"
        assert assistant_text.strip(), "Assistant response was empty"
        assert "OpenAI request failed" not in after_text, "Live OpenAI request failed"
        assert "Unexpected error:" not in after_text, "Generated app raised an unexpected error"
        assert "LLM request failed" not in after_text, "Generated app reported an LLM failure"

        transcript = {
            "url": url,
            "prompt": prompt,
            "assistant_text": assistant_text.strip(),
            "before_text_length": len(before_text),
            "after_text_length": len(after_text),
            "screenshots": [
                "deployment/screenshots/post-deploy-before-chat.png",
                "deployment/screenshots/post-deploy-chat.png",
            ],
        }
        (browser_dir / "post-deploy-chat-transcript.json").write_text(
            json.dumps(transcript, indent=2) + "\n",
            encoding="utf-8",
        )
        print("Post-deployment chatbot QA completed")
        """
    ).strip()
    return script.replace("__EVIDENCE_DIR__", json.dumps(str(evidence_dir))).replace(
        "__PUBLIC_URL__", json.dumps(public_url)
    )


def _string_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _summary_payload(
    status: str,
    target_dir: Path,
    request: dict[str, object],
    steps: list[DeploymentStep],
) -> dict[str, object]:
    return {
        "agent_id": "deployment-agent",
        "runtime": "L2 Tool Executor",
        "status": status,
        "target": request.get("target", "azure-container-apps"),
        "target_project_dir": str(target_dir),
        "steps": [step.to_dict() for step in steps],
    }


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return ((result.stdout or "") + (result.stderr or "")).strip()


def _resolve_command(command: Sequence[str]) -> list[str]:
    if not command:
        return []
    executable = str(command[0])
    resolved = shutil.which(executable)
    if os.name == "nt" and executable.lower() == "az":
        resolved = shutil.which("az.cmd") or resolved
    return [resolved or executable, *[str(part) for part in command[1:]]]


def _step_details(
    result: subprocess.CompletedProcess[str],
    output: str,
    *,
    allow_failure: bool = False,
) -> str:
    if result.returncode == 0:
        return "Command completed successfully."

    first_line = _first_output_line(output)
    if allow_failure:
        base = "Not present yet or already handled; continuing with the deployment flow."
    else:
        base = "Command failed."
    return f"{base} {first_line}" if first_line else base


def _first_output_line(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:240]
    return ""


def _looks_like_missing_browser(output: str) -> bool:
    lowered = output.lower()
    return "playwright install" in lowered or "executable doesn't exist" in lowered


def _log_step_result(name: str, status: str, exit_code: int, output: str) -> None:
    if exit_code == 0 or status == "skipped":
        LOGGER.info("Deployment step completed name=%s status=%s", name, status)
        return
    LOGGER.warning(
        "Deployment step failed name=%s status=%s exit_code=%s output=%s",
        name,
        status,
        exit_code,
        _first_output_line(output),
    )


def _render_failure_output_section(failure_output: list[str]) -> str:
    if not failure_output:
        return ""
    return "## Failure Output\n\n" + "\n\n".join(failure_output) + "\n"


def _render_step_output(step: dict[str, object]) -> str:
    name = str(step.get("name", "Failed step"))
    output = str(step.get("output", "")).strip()
    if len(output) > 4000:
        output = output[:4000].rstrip() + "\n... truncated ..."
    return f"### {name}\n\n```text\n{output}\n```"


def _safe_command(command: Sequence[str]) -> list[str]:
    safe: list[str] = []
    redact_until_next_flag = False
    for part in command:
        text = str(part)
        if text.startswith("--"):
            safe.append(text)
            redact_until_next_flag = text in {"--registry-password", "--secrets", "--password"}
            continue
        if redact_until_next_flag:
            safe.append("<redacted>")
            continue
        safe.append(text)
    return safe
