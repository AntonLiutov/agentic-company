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
from agentic_company.platform.artifacts import load_execution_request
from agentic_company.platform.events import write_event
from agentic_company.platform.logging import configure_logging
from agentic_company.platform.models import AgentRunResult, ExecutionRequest

LOGGER = logging.getLogger(__name__)

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
    sandbox: str = "workspace-write"
    timeout_seconds: int = 1800
    summary_filename: str = "07-execution-summary.md"
    prompt_filename: str = "codex/prompt.md"
    log_filename: str = "codex/execution.log"
    raw_events_filename: str = "codex/events.jsonl"
    command_executor: CommandExecutor | None = None

    def run(self, run_dir: Path) -> AgentRunResult:
        request = load_execution_request(run_dir)
        event_log = run_dir / "events.jsonl"
        target_dir = Path(request.target_project_dir)
        active_feature_id = _active_feature_id(request)
        summary_filename = _feature_artifact_filename(
            self.summary_filename,
            active_feature_id,
        )
        prompt_filename = _feature_artifact_filename(self.prompt_filename, active_feature_id)
        log_filename = _feature_artifact_filename(self.log_filename, active_feature_id)
        raw_events_filename = _feature_artifact_filename(
            self.raw_events_filename,
            active_feature_id,
        )
        summary_path = run_dir / summary_filename
        prompt_path = run_dir / prompt_filename
        log_path = run_dir / log_filename
        raw_events_path = run_dir / raw_events_filename

        if summary_path.exists() and not _is_failed_summary(summary_path):
            LOGGER.info("Codex execution already completed run_dir=%s", run_dir)
            summary = summary_path.read_text(encoding="utf-8")
            return AgentRunResult(
                agent_id=request.agent_id,
                status="already_completed",
                output_artifacts=[summary_filename],
                summary=summary,
            )

        target_dir.mkdir(parents=True, exist_ok=True)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        if raw_events_path.exists():
            raw_events_path.unlink()
        command = self.build_command(request, run_dir, summary_path)
        prompt = build_codex_prompt(request, run_dir)
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
                **_feature_event_data(active_feature_id),
            },
        )

        try:
            completed = self._execute(command, prompt, log_path, raw_events_path)
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
                    **_feature_event_data(active_feature_id),
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
                    **_feature_event_data(active_feature_id),
                },
            )
            return AgentRunResult(
                agent_id=request.agent_id,
                status="codex_failed",
                output_artifacts=[summary_filename, log_filename, *structured_artifacts],
                summary=summary,
            )

        if summary_path.exists():
            summary = summary_path.read_text(encoding="utf-8")
        else:
            summary = render_codex_success_summary(request, completed)
            summary_path.write_text(summary, encoding="utf-8")

        write_event(
            event_log,
            request.run_id,
            request.agent_id,
            "execution_completed",
            {
                "artifact": summary_filename,
                "target_project_dir": request.target_project_dir,
                "status": "codex_completed",
                **_feature_event_data(active_feature_id),
            },
        )
        LOGGER.info("Codex execution completed run_id=%s", request.run_id)
        return AgentRunResult(
            agent_id=request.agent_id,
            status="codex_completed",
            output_artifacts=[
                summary_filename,
                prompt_filename,
                log_filename,
                *structured_artifacts,
                *request.expected_outputs,
            ],
            summary=summary,
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
        )

    def _execute(
        self,
        command: Sequence[str],
        prompt: str,
        log_path: Path,
        raw_events_path: Path,
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
        )


def build_codex_prompt(request: ExecutionRequest, run_dir: Path) -> str:
    implementation_brief = (run_dir / "05-implementation-brief.md").read_text(encoding="utf-8")
    inputs = "\n".join(f"- {artifact}" for artifact in request.input_artifacts)
    outputs = "\n".join(f"- {artifact}" for artifact in request.expected_outputs)
    instructions = "\n".join(f"- {instruction}" for instruction in request.instructions)
    constraints = "\n".join(f"- {constraint}" for constraint in request.constraints)
    feature_context = _render_feature_context(request)

    return f"""You are the Fullstack Agent for agentic-company.

Build the requested project inside this working directory only:
{request.target_project_dir}

Planning run directory:
{run_dir}

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

Workspace ownership:
- Product implementation files belong inside `{request.target_project_dir}` only.
- Create application folders there, for example `api/`, `web/`, `src/`, `tests/`,
  `scripts/`, `docs/`, or other project-local folders when they are part of the
  generated application.
- Do not write QA, deployment, handoff, or orchestration artifacts. Those belong
  to other agents.
- Do not write outside `{request.target_project_dir}` unless the execution
  request explicitly names a required run-level artifact.
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
- If `{request.target_project_dir}\\.env` already exists, preserve it.
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
- Document commands like `uv sync`, `uv add`, or `uv run streamlit run app.py`.
- Mention `pip install` only as a fallback for users without `uv`.
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

Implementation brief:
{implementation_brief}

When finished, leave the project files in the working directory and summarize what you built,
what you intentionally skipped, and how to run it.
"""


def _active_feature_id(request: ExecutionRequest) -> str | None:
    if request.project_archetype != "api-web-compose":
        return None
    if not request.active_feature:
        return None
    feature_id = request.active_feature.get("id")
    return str(feature_id) if feature_id else None


def _feature_artifact_filename(filename: str, feature_id: str | None) -> str:
    if not feature_id:
        return filename
    path = Path(filename)
    if path.parent == Path("."):
        return f"{path.stem}-{feature_id}{path.suffix}"
    return str(path.parent / feature_id / path.name).replace("\\", "/")


def _feature_event_data(feature_id: str | None) -> dict[str, str]:
    return {"feature_id": feature_id} if feature_id else {}


def _render_feature_context(request: ExecutionRequest) -> str:
    if not request.active_feature:
        return "- No active feature is scoped for this run; implement the full request."

    feature = request.active_feature
    criteria = "\n".join(f"  - {criterion}" for criterion in feature.get("acceptance_criteria", []))
    completed = ", ".join(request.completed_feature_ids) or "none"
    return f"""- Project archetype: `{request.project_archetype}`
- Active feature: `{feature.get("id")}` - {feature.get("title")}
- Active feature acceptance criteria:
{criteria or "  - None provided."}
- Completed features before this run: {completed}
- Implement only the active feature in this Codex run.
- Preserve behavior for completed features and avoid broad rewrites unless required."""


def _is_failed_summary(summary_path: Path) -> bool:
    summary = summary_path.read_text(encoding="utf-8")
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
