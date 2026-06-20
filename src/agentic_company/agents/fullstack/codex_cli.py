"""Real Codex CLI runner for execution requests."""

from __future__ import annotations

import argparse
import logging
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from agentic_company.integrations.codex import (
    build_codex_exec_command,
    stream_codex_exec_to_log,
    write_structured_codex_artifacts,
)
from agentic_company.platform.artifacts.artifacts import (
    load_execution_request,
    read_text_artifact,
)
from agentic_company.platform.run.events import write_event
from agentic_company.platform.run.executions import (
    build_agent_execution_id,
    build_codex_execution_id,
    execution_artifact_dir,
    extract_codex_thread_id,
)
from agentic_company.platform.logging import configure_logging
from agentic_company.platform.mirror.messages import render_incoming_messages_for_prompt
from agentic_company.platform.db.models import AgentRunResult, ExecutionRequest

LOGGER = logging.getLogger(__name__)
REQUEST_CONTEXT_PREVIEW_CHARS = 2500

CommandExecutor = Callable[
    [Sequence[str], str, int, Path, Path],
    subprocess.CompletedProcess[str],
]


@dataclass(slots=True)
class CodexCliRunner:
    """Execute a planning handoff through `codex exec`.

    The command executor is injectable so tests can verify the CLI contract without
    starting a live Codex run.
    """

    codex_binary: str | None = None
    # Implementation installs dependencies and runs builds/tests that need network.
    sandbox: str = "danger-full-access"
    timeout_seconds: int = 1800
    summary_filename: str = "07-execution-summary.md"
    prompt_filename: str = "codex/prompt.md"
    log_filename: str = "codex/execution.log"
    raw_events_filename: str = "codex/events.jsonl"
    command_executor: CommandExecutor | None = None

    def run(self, run_dir: Path) -> AgentRunResult:
        request = load_execution_request(run_dir)
        event_log = run_dir
        target_dir = Path(request.target_project_dir)
        work_item_id = _work_item_id(request)
        execution_id = _execution_id(request, work_item_id)
        codex_execution_id = build_codex_execution_id(
            execution_id=execution_id,
            codex_agent_id=request.agent_id,
        )
        paths = _execution_paths(
            run_dir=run_dir,
            work_item_id=work_item_id,
            execution_id=request.execution_id,
            summary_filename=self.summary_filename,
            prompt_filename=self.prompt_filename,
            log_filename=self.log_filename,
            raw_events_filename=self.raw_events_filename,
        )
        summary_filename = paths["summary"]
        prompt_filename = paths["prompt"]
        log_filename = paths["log"]
        raw_events_filename = paths["raw_events"]
        summary_path = run_dir / summary_filename
        prompt_path = run_dir / prompt_filename
        log_path = run_dir / log_filename
        raw_events_path = run_dir / raw_events_filename

        target_dir.mkdir(parents=True, exist_ok=True)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        if raw_events_path.exists():
            raw_events_path.unlink()
        command = self.build_command(request, run_dir, summary_path)
        prompt = build_codex_prompt(request, run_dir)
        resume_thread_id = request.codex_resume_thread_id
        prompt_path.write_text(prompt, encoding="utf-8")
        LOGGER.info(
            "Codex execution starting run_id=%s model=%s target_dir=%s",
            request.run_id,
            request.model,
            target_dir,
        )
        log_path.write_text(
            f"$ {' '.join(command)}\n"
            f"timeout_seconds={self.timeout_seconds}\n\n"
            f"execution_id={execution_id}\n"
            f"codex_execution_id={codex_execution_id}\n\n"
            f"codex_resume_thread_id={resume_thread_id or '(none)'}\n\n"
            "Codex process is starting...\n",
            encoding="utf-8",
        )

        write_event(
            event_log,
            request.run_id,
            request.agent_id,
            "execution_started",
            {
                "target_project_dir": request.target_project_dir,
                "provider": "codex-cli",
                "mode": "codex-exec",
                "execution_id": execution_id,
                "codex_execution_id": codex_execution_id,
                "codex_resume_thread_id": resume_thread_id,
                "work_item_id": work_item_id,
            },
        )

        try:
            completed = self._execute(
                command,
                prompt,
                log_path,
                raw_events_path,
                codex_execution_id=codex_execution_id,
                run_dir=run_dir,
                run_id=request.run_id,
                agent_id=request.agent_id,
                work_item_id=work_item_id,
            )
        except FileNotFoundError as exc:
            LOGGER.exception("Codex CLI missing run_id=%s", request.run_id)
            summary = render_codex_failure_summary(request, command, "Codex CLI was not found.")
            summary_path.write_text(summary, encoding="utf-8")
            write_event(
                event_log,
                request.run_id,
                request.agent_id,
                "execution_failed",
                {
                    "artifact": summary_filename,
                    "target_project_dir": request.target_project_dir,
                    "status": "codex_cli_missing",
                    "work_item_id": work_item_id,
                },
            )
            raise RuntimeError(
                "Codex CLI was not found. Install or authenticate Codex first."
            ) from exc
        structured_artifacts = write_structured_codex_artifacts(
            run_dir,
            completed.stdout,
            raw_events_filename=raw_events_filename,
        )

        if completed.returncode != 0:
            LOGGER.error(
                "Codex execution failed run_id=%s returncode=%s",
                request.run_id,
                completed.returncode,
            )
            reason = (
                completed.stderr.strip() or completed.stdout.strip() or "Codex exited non-zero."
            )
            summary = render_codex_failure_summary(request, command, reason)
            summary_path.write_text(summary, encoding="utf-8")
            write_event(
                event_log,
                request.run_id,
                request.agent_id,
                "execution_failed",
                {
                    "artifact": summary_filename,
                    "target_project_dir": request.target_project_dir,
                    "status": "codex_failed",
                    "returncode": completed.returncode,
                    "work_item_id": work_item_id,
                },
            )
            return AgentRunResult(
                agent_id=request.agent_id,
                status="codex_failed",
                output_artifacts=[summary_filename, log_filename, *structured_artifacts],
                summary=summary,
                execution_id=execution_id,
                codex_thread_id=extract_codex_thread_id(raw_events_path) or resume_thread_id,
                blocking_findings=[reason],
                recommended_next_action=(
                    "Inspect Codex execution log and retry the implementation task."
                ),
            )

        if summary_path.exists():
            summary = read_text_artifact(summary_path)
        else:
            summary = render_codex_success_summary(request, completed)
            summary_path.write_text(summary, encoding="utf-8")

        codex_thread_id = extract_codex_thread_id(raw_events_path) or resume_thread_id
        write_event(
            event_log,
            request.run_id,
            request.agent_id,
            "execution_completed",
            {
                "artifact": summary_filename,
                "target_project_dir": request.target_project_dir,
                "status": "codex_completed",
                "execution_id": execution_id,
                "codex_execution_id": codex_execution_id,
                "codex_thread_id": codex_thread_id,
                "work_item_id": work_item_id,
            },
        )
        LOGGER.info("Codex execution completed run_id=%s", request.run_id)
        output_artifacts = _unique_artifacts(
            [
                summary_filename,
                prompt_filename,
                log_filename,
                *structured_artifacts,
            ]
        )
        return AgentRunResult(
            agent_id=request.agent_id,
            status="codex_completed",
            output_artifacts=output_artifacts,
            summary=summary,
            execution_id=execution_id,
            codex_thread_id=codex_thread_id,
        )

    def build_command(
        self,
        request: ExecutionRequest,
        run_dir: Path,
        summary_path: Path,
    ) -> list[str]:
        return build_codex_exec_command(
            codex_binary=self.codex_binary,
            model=request.model,
            sandbox=self.sandbox,
            target_project_dir=request.target_project_dir,
            run_dir=run_dir,
            summary_path=summary_path,
            resume_session_id=request.codex_resume_thread_id,
            force_sandbox=True,
        )

    def _execute(
        self,
        command: Sequence[str],
        prompt: str,
        log_path: Path,
        raw_events_path: Path,
        *,
        codex_execution_id: str,
        run_dir: Path,
        run_id: int | str,
        agent_id: str,
        work_item_id: str | None,
    ) -> subprocess.CompletedProcess[str]:
        if self.command_executor:
            return self.command_executor(
                command, prompt, self.timeout_seconds, log_path, raw_events_path
            )

        return stream_codex_exec_to_log(
            command,
            prompt,
            self.timeout_seconds,
            log_path,
            raw_events_path,
            codex_execution_id=codex_execution_id,
            trace_run_dir=run_dir,
            trace_run_id=run_id,
            trace_agent_id=agent_id,
            trace_work_item_id=work_item_id,
        )


def build_codex_prompt(request: ExecutionRequest, run_dir: Path) -> str:
    request_context = _render_request_context(request, run_dir)
    inputs = "\n".join(f"- {artifact}" for artifact in request.input_artifacts)
    outputs = "\n".join(f"- {artifact}" for artifact in request.expected_outputs)
    instructions = "\n".join(f"- {instruction}" for instruction in request.instructions)
    constraints = "\n".join(f"- {constraint}" for constraint in request.constraints)
    feature_context = _render_feature_context(request)
    upstream_messages = render_incoming_messages_for_prompt(run_dir, to_agent=request.agent_id)
    # Skills now reach the worker via the central skill index prepended in
    # stream_codex_exec_to_log (progressive disclosure) — no per-agent paste here.

    return f"""You are the Fullstack Agent for agentic-company.

Build the requested project inside this working directory only:
{request.target_project_dir}

Planning run directory:
{run_dir}

Platform execution id:
{request.execution_id or "(not provided)"}

Execution intent:
{request.execution_intent or "(not provided)"}

Input artifacts available:
{inputs}

Expected outputs:
{outputs}

Instructions:
{instructions}

Constraints:
{constraints}

Feature delivery context:
{feature_context}

Upstream agent messages:
{upstream_messages}

Contract precedence:
- Use the active feature context, canonical work item packet, and referenced
  artifacts as the source of truth for implementation.
- Treat coordinator free-text as routing/context unless it is supported by those
  sources.
- Do not add or omit acceptance criteria, status codes, feature scope,
  deployment gates, or QA gates based only on a coordinator paraphrase.
- If coordinator text conflicts with the canonical work item or artifacts, call
  out the mismatch in your summary instead of silently changing the contract.

Workspace ownership:
- Treat `{run_dir}` as the delivery run workspace and
  `{request.target_project_dir}` as the generated product project.
- Work only inside the target project directory.
- You may use network-backed tools when needed for the generated project, such
  as package indexes, documentation lookup, dependency installation, Docker
  builds, and local verification.
- Product implementation files belong inside `{request.target_project_dir}` only.
- Create application folders there, for example `api/`, `web/`, `src/`, `tests/`,
  `scripts/`, `docs/`, or other project-local folders when they are part of the
  generated application.
- Do not write QA, deployment, handoff, or orchestration artifacts. Those belong
  to other agents.
- Do not modify files outside `{run_dir}`. In particular, do not modify the
  platform repository source, root configuration, user home files, or unrelated
  projects unless the execution request explicitly names that path.
- Do not write outside `{request.target_project_dir}` unless the execution
  request explicitly names a required run-level artifact under `{run_dir}`.
- If you create implementation helper scripts or smoke tests, keep them inside
  the generated project, preferably under `scripts/`, `tests/`, or a clearly
  named project-local folder.
- You may create or update project-local `.gitignore` and `.dockerignore` files
  inside `{request.target_project_dir}` when they keep generated app artifacts,
  caches, secrets, or Docker contexts clean.
- Do not update the platform repository's root `.gitignore` or `.dockerignore`
  unless the execution request explicitly asks for platform-level changes.
- The platform owns Codex logs and execution summaries outside the generated
  project; mention generated project files in your final summary instead of
  creating duplicate run-level reports.

Credential handling:
- Treat platform/provider keys as agent runtime secrets. They are available to
  tools through the process environment; they must not be copied into the
  generated application folder.
- If `{request.target_project_dir}\\.env` is needed, it may contain only
  app-owned runtime variables from `.env.example` with empty placeholders or
  safe defaults. Never store OpenAI, Codex, Gemini, Azure, platform, or user
  account secrets there.
- Do not print secret values in generated files, logs, summaries, or comments.
- Keep `.env.example` limited to empty placeholders or safe defaults.
- Use ASCII in generated source and documentation unless non-ASCII is explicitly required.

Environment setup:
- Prefer `uv` for Python project setup and run commands.
- Include a `pyproject.toml` for generated Python apps when dependencies are needed.
- Include `uv.lock` when possible so Docker and local setup are reproducible.
- Local verification may create `.venv`, `__pycache__`, `.pytest_cache`, Playwright
  caches, or other tool caches inside the generated project. That is acceptable
  during execution. Exclude those paths in project-local `.gitignore` and
  `.dockerignore`; do not treat their temporary presence as a delivery failure.
- Do not recursively list dependency/cache directories such as `.venv`,
  `node_modules`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.next`,
  `dist`, or `build`. Use targeted file listings that exclude caches.
- Do not attempt risky cleanup of locked environments on Windows. If a cache or
  virtual environment cannot be removed because a process holds a file handle,
  leave it in place, document it as a local verification artifact, and finish
  successfully if the product acceptance criteria are satisfied.
- Do not stop broad sets of processes by matching the generated project path.
  Only stop an exact process ID that you started and still own; never stop the
  running Codex process, terminal process, Docker Desktop, or unrelated tools.
- For `api-web-compose`, Docker Compose naming is not flexible:
  - service names must be exactly `api` and `web`;
  - image names must follow the exact `agentic-{{app-slug}}-{{service}}:latest`
    policy from the execution request;
  - container names must follow the exact `agentic-{{app-slug}}-{{service}}`
    policy from the execution request.
- Keep generated Python dependency constraints stable and minimal. Avoid changing Python
  version requirements or package lower bounds unless the requirements explicitly need it,
  because those changes invalidate Docker dependency cache layers.
- Document the framework-native setup and run commands needed for the generated app.
- Mention `pip install` only for users without `uv`.
- If Docker Compose is requested or not explicitly listed as a non-goal, include a minimal
  `Dockerfile` and `docker-compose.yml`.
- Docker Compose should allow this flow: copy or create `.env`, fill credentials, then run
  `docker compose up --build`.
- Docker setup must read credentials at runtime from `.env` or environment variables and must
  not copy secret values into the image.
- Do not install `uv` with `pip` inside Docker. Use an official uv image/prebuilt uv binary
  and a cache-friendly Dockerfile that copies `pyproject.toml`/`uv.lock` before app source.
- For uv-based Dockerfiles, enable BuildKit cache reuse for dependency downloads, for example:
  `RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev`.
- Runtime commands inside Docker should use `uv run --no-sync ...` after the build has already
  run `uv sync`, so container startup does not repeat dependency checks.

Cloud/runtime readiness:
- Do not treat successful local tests, local Docker smoke checks, or local filesystem
  persistence as proof that the app is ready for hosted/containerized deployment.
- When the product requires shared or durable state, implement configuration-driven
  persistence so local development/tests can use lightweight local storage while
  deployed environments can use a backend suitable for the target runtime.
- Avoid hard-coding local filesystem-only storage when the release requires state to
  survive refreshes, restarts, revisions, redeployments, or access from multiple users.
- If Team Lead sends Deployment or QA evidence showing an application runtime/cloud
  mismatch, repair the generated app accordingly. Examples include persistence support,
  startup initialization, runtime config, health endpoint behavior, container definition,
  and application-owned environment assumptions.

UI delivery honesty:
- For UI/web features, every visible primary button, menu item, tab, form, and
  modal control must either work for the current feature scope or be hidden until
  it is implemented.
- Show admin/moderation controls only to roles that are allowed to use them by
  the active acceptance criteria.
- Do not use demo/static chat data, fake notifications, placeholder files, or
  future-only controls in a way that looks like delivered functionality.
- If the acceptance criteria require a visible UI flow, such as private room
  invitations or joining by invitation, implement the flow in the UI instead of
  proving it only through API calls.
- Preserve responsive desktop and mobile usability; avoid overlapping text,
  clipped controls, and layout states that block the primary chat workflow.

Request context:
{request_context}

When finished, leave the project files in the working directory and summarize what you built,
what you intentionally skipped, and how to run it.
"""


def _render_request_context(request: ExecutionRequest, run_dir: Path) -> str:
    sections: list[str] = []
    for artifact in request.input_artifacts:
        path = run_dir / artifact
        if not path.exists() or path.is_dir():
            continue
        if path.suffix.lower() not in {".md", ".txt", ".json", ".csv", ".mmd"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            continue
        sections.append(f"## {artifact}\n\n{_preview_text(text, path)}")
    if sections:
        return (
            "The following sections are previews only. Treat artifact paths as "
            "the source of truth and open full files from the workspace when "
            "implementation details matter.\n\n" + "\n\n".join(sections)
        )
    requirements_path = run_dir / "00-requirements.md"
    if requirements_path.exists():
        text = requirements_path.read_text(encoding="utf-8", errors="replace")
        return (
            "Preview of 00-requirements.md. Open the full file from the workspace "
            "when details matter.\n\n" + _preview_text(text, requirements_path)
        )
    return "(No readable request artifacts found.)"


def _preview_text(text: str, path: Path, limit: int = REQUEST_CONTEXT_PREVIEW_CHARS) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return (
        f"{text[:limit].rstrip()}\n\n... [truncated {omitted} chars; open {path} for full source]"
    )


def _work_item_id(request: ExecutionRequest) -> str:
    work_item_id = str(request.work_item.get("work_item_id") or "").strip()
    if not work_item_id:
        raise ValueError("Execution request is missing explicit work_item.work_item_id")
    return work_item_id


def _work_item_artifact_filename(filename: str, work_item_id: str | None) -> str:
    if not work_item_id:
        return filename
    path = Path(filename)
    if path.parent == Path("."):
        return f"{path.stem}-{work_item_id}{path.suffix}"
    return str(path.parent / work_item_id / path.name).replace("\\", "/")


def _execution_id(request: ExecutionRequest, work_item_id: str | None) -> str:
    if request.execution_id:
        return request.execution_id
    return build_agent_execution_id(
        run_id=request.run_id,
        agent_id=request.agent_id,
        correlation_id=work_item_id or "work-item",
        intent=request.execution_intent or "implementation",
    )


def _execution_paths(
    *,
    run_dir: Path,
    work_item_id: str | None,
    execution_id: str,
    summary_filename: str,
    prompt_filename: str,
    log_filename: str,
    raw_events_filename: str,
) -> dict[str, str]:
    if not execution_id:
        return {
            "summary": _work_item_artifact_filename(summary_filename, work_item_id),
            "prompt": _work_item_artifact_filename(prompt_filename, work_item_id),
            "log": _work_item_artifact_filename(log_filename, work_item_id),
            "raw_events": _work_item_artifact_filename(raw_events_filename, work_item_id),
        }

    root = run_dir / "codex"
    if work_item_id:
        root = root / work_item_id
    artifact_dir = execution_artifact_dir(root=root, execution_id=execution_id)
    relative = artifact_dir.relative_to(run_dir).as_posix()
    return {
        "summary": f"{relative}/summary.md",
        "prompt": f"{relative}/prompt.md",
        "log": f"{relative}/execution.log",
        "raw_events": f"{relative}/events.jsonl",
    }


def _render_feature_context(request: ExecutionRequest) -> str:
    if not request.work_item:
        return "- No work item is scoped for this run; this is a contract error."

    work_item = request.work_item
    completed = ", ".join(request.completed_work_item_ids) or "none"
    return f"""- Work item: `{work_item.get("work_item_id")}` - {work_item.get("title")}
- Sprint: `{work_item.get("sprint_id")}`
- Completed work items before this run: {completed}
- Implement only the scoped work item in this Codex run.
- Preserve behavior for completed work items and avoid broad rewrites unless required."""


def _unique_artifacts(paths: Sequence[str]) -> list[str]:
    unique: list[str] = []
    for path in paths:
        normalized = str(path or "").strip().replace("\\", "/")
        if normalized and normalized not in unique:
            unique.append(normalized)
    return unique


def _is_failed_summary(summary_path: Path) -> bool:
    summary = read_text_artifact(summary_path)
    return "Status: codex failed" in summary


def render_codex_success_summary(
    request: ExecutionRequest,
    completed: subprocess.CompletedProcess[str],
) -> str:
    output = completed.stdout.strip() or "Codex completed without stdout."
    return f"""# Execution Summary

Status: codex completed

## Runner

- Agent: `{request.agent_id}`
- Runtime: {request.maturity_level}
- Provider: codex-cli
- Model: {request.model}
- Target project directory: `{request.target_project_dir}`

## Codex Output

```text
{output}
```
"""


def render_codex_failure_summary(
    request: ExecutionRequest,
    command: Sequence[str],
    reason: str,
) -> str:
    command_text = " ".join(command)
    return f"""# Execution Summary

Status: codex failed

## Runner

- Agent: `{request.agent_id}`
- Runtime: {request.maturity_level}
- Provider: codex-cli
- Model: {request.model}
- Target project directory: `{request.target_project_dir}`

## Command

```text
{command_text}
```

## Failure

```text
{reason}
```
"""


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Run a planning handoff with Codex CLI.")
    parser.add_argument("run_dir", type=Path, help="Planning run directory.")
    args = parser.parse_args()

    result = CodexCliRunner().run(args.run_dir)
    print(result.summary)
    if result.status == "codex_failed":
        raise SystemExit(1)
