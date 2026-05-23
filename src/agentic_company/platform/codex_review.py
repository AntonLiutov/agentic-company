"""Shared read-only Codex review runner for coordinator agents."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agentic_company.integrations.codex import (
    build_codex_exec_command,
    build_codex_exec_environment,
    stream_codex_exec_to_log,
)
from agentic_company.platform.artifact_registry import register_artifact
from agentic_company.platform.artifacts import read_text_artifact
from agentic_company.platform.executions import (
    build_agent_execution_id,
    build_codex_execution_id,
    execution_artifact_dir,
    extract_codex_thread_id,
)
from agentic_company.platform.run_trace import record_model_call_event

CommandExecutor = Callable[
    [Sequence[str], str, int, Path, Path],
    subprocess.CompletedProcess[str],
]


@dataclass(frozen=True, slots=True)
class CodexReviewRequest:
    """Input contract for a coordinator-owned read-only Codex review."""

    run_id: str
    run_dir: Path
    requesting_agent: str
    purpose: str
    question: str
    artifact_refs: list[str] = field(default_factory=list)
    target_agent: str | None = None
    correlation_id: str | None = None
    model: str = "gpt-5.3-codex"
    execution_id: str = ""
    codex_resume_thread_id: str = ""


@dataclass(frozen=True, slots=True)
class CodexReviewResult:
    """Read-only review result returned to a coordinator tool."""

    status: str
    content: str
    artifact_refs: list[str]
    summary_artifact: str
    prompt_artifact: str
    log_artifact: str
    raw_events_artifact: str = ""
    execution_id: str = ""
    codex_thread_id: str = ""


@dataclass(slots=True)
class CodexReviewRunner:
    """Run Codex as a shared read-only reviewer from a coordinator tool call."""

    codex_binary: str | None = None
    timeout_seconds: int = 900
    command_executor: CommandExecutor | None = None

    def run(self, request: CodexReviewRequest) -> CodexReviewResult:
        execution_id = request.execution_id or build_agent_execution_id(
            run_id=request.run_id,
            agent_id=request.requesting_agent,
            target=request.correlation_id or request.target_agent or "review",
            intent="codex_review",
            message_id=request.question,
        )
        review_agent_id = _review_agent_id(request.requesting_agent)
        artifact_owner = _review_artifact_owner(request.requesting_agent)
        codex_execution_id = build_codex_execution_id(
            execution_id=execution_id,
            codex_agent_id=review_agent_id,
        )
        review_dir = execution_artifact_dir(
            root=request.run_dir / artifact_owner / "codex-review",
            execution_id=execution_id,
        )
        review_dir.mkdir(parents=True, exist_ok=True)
        summary_path = review_dir / "summary.md"
        prompt_path = review_dir / "prompt.md"
        log_path = review_dir / "execution.log"
        raw_events_path = review_dir / "events.jsonl"
        if raw_events_path.exists():
            raw_events_path.unlink()

        prompt = build_codex_review_prompt(request)
        prompt_path.write_text(prompt, encoding="utf-8")
        prompt_artifact = prompt_path.relative_to(request.run_dir).as_posix()
        command = build_codex_exec_command(
            codex_binary=self.codex_binary,
            model=request.model,
            sandbox="read-only",
            target_project_dir=str(request.run_dir),
            run_dir=request.run_dir,
            summary_path=summary_path,
            force_sandbox=True,
            resume_session_id=request.codex_resume_thread_id,
        )
        started = time.perf_counter()
        completed = self._execute(
            command,
            prompt,
            log_path,
            raw_events_path,
            request.run_dir,
            codex_execution_id=codex_execution_id,
        )
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        content = (
            read_text_artifact(summary_path) if summary_path.exists() else completed.stdout.strip()
        )
        status = "reviewed" if completed.returncode == 0 else "failed"
        if not content:
            content = "Codex review produced no response."
        codex_thread_id = extract_codex_thread_id(raw_events_path) or request.codex_resume_thread_id
        summary_artifact = summary_path.relative_to(request.run_dir).as_posix()
        log_artifact = log_path.relative_to(request.run_dir).as_posix()
        raw_events_artifact = raw_events_path.relative_to(request.run_dir).as_posix()
        _register_review_artifacts(
            request,
            [
                (summary_artifact, "execution_summary"),
                (prompt_artifact, "tool_request"),
                (log_artifact, "codex_log"),
                (raw_events_artifact, "debug_trace"),
            ],
        )
        record_model_call_event(
            request.run_dir,
            run_id=request.run_id,
            agent_id=request.requesting_agent,
            provider="openai",
            model=request.model,
            purpose="codex_review",
            prompt_ref=prompt_artifact,
            status=status,
            duration_ms=duration_ms,
        )
        return CodexReviewResult(
            status=status,
            content=content,
            artifact_refs=list(request.artifact_refs),
            summary_artifact=summary_artifact,
            prompt_artifact=prompt_artifact,
            log_artifact=log_artifact,
            raw_events_artifact=raw_events_artifact,
            execution_id=execution_id,
            codex_thread_id=codex_thread_id,
        )

    def _execute(
        self,
        command: Sequence[str],
        prompt: str,
        log_path: Path,
        raw_events_path: Path,
        run_dir: Path,
        *,
        codex_execution_id: str,
    ) -> subprocess.CompletedProcess[str]:
        if self.command_executor:
            return self.command_executor(
                command,
                prompt,
                self.timeout_seconds,
                log_path,
                raw_events_path,
            )
        env = build_codex_exec_environment(run_dir)
        env["AGENTIC_CODEX_SANDBOX"] = "read-only"
        return stream_codex_exec_to_log(
            command,
            prompt,
            self.timeout_seconds,
            log_path,
            raw_events_path,
            env=env,
            codex_execution_id=codex_execution_id,
        )


def build_codex_review_prompt(request: CodexReviewRequest) -> str:
    artifact_refs = "\n".join(f"- {artifact}" for artifact in request.artifact_refs)
    target = request.target_agent or "the requesting coordinator"
    return f"""You are a read-only Codex reviewer for agentic-company.

Requesting agent: {request.requesting_agent}
Target agent for feedback: {target}
Run directory: {request.run_dir}
Platform execution id: {request.execution_id or "(not provided)"}
Purpose:
{request.purpose}

Question:
{request.question}

Artifact references:
{artifact_refs or "- None"}

Rules:
- Read only. Do not edit, create, delete, move, format, or rewrite files.
- Inspect only the run workspace and referenced artifacts needed for the review.
- Treat the Question and Purpose as the source of truth for what to review.
- Return concise, actionable text that can be sent as an agent message.
- Ground findings in the referenced artifacts and original run context.
- If the evidence is insufficient, say exactly what is missing.
- Do not include shell transcripts unless they are the evidence being reviewed.
"""


def _review_agent_id(requesting_agent: str) -> str:
    if requesting_agent.endswith("-agent"):
        return requesting_agent.removesuffix("-agent") + "-codex-review"
    return requesting_agent + "-codex-review"


def _review_artifact_owner(requesting_agent: str) -> str:
    return requesting_agent.removesuffix("-agent") or "review"


def _register_review_artifacts(
    request: CodexReviewRequest,
    artifacts: list[tuple[str, str]],
) -> None:
    for relative_path, artifact_type in artifacts:
        try:
            register_artifact(
                request.run_dir,
                relative_path=relative_path,
                run_id=request.run_id,
                owner_agent=request.requesting_agent,
                artifact_type=artifact_type,
                visibility="developer",
                source_tool="codex_review",
                source_model=request.model,
                metadata={"target_agent": request.target_agent or ""},
            )
        except Exception:
            continue
