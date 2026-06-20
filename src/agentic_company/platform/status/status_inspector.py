"""Codex-backed status inspection for coordinator agents."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from agentic_company.integrations.codex import (
    DEFAULT_CODEX_MODEL,
    build_codex_exec_command,
    build_codex_exec_environment,
    extract_codex_usage,
    stream_codex_exec_to_log,
)
from agentic_company.platform.artifacts.artifact_registry import artifact_id_for
from agentic_company.platform.artifacts.artifacts import read_json_object_artifact, write_json_artifact
from agentic_company.platform.run.executions import (
    build_agent_execution_id,
    build_codex_execution_id,
    execution_artifact_dir,
    extract_codex_thread_id,
)
from agentic_company.platform.run.run_trace import record_model_call_event
from agentic_company.platform.db.runtime_db import record_artifact_link
from agentic_company.platform.contracts.tool_contracts import ArtifactRegistrationRequest

CommandExecutor = Callable[
    [Sequence[str], str, int, Path, Path],
    subprocess.CompletedProcess[str],
]


class StatusInspectorLike(Protocol):
    """Runtime contract for status inspectors used by coordinator tools."""

    def run(self, request: StatusInspectionRequest) -> StatusInspectionResult:
        """Inspect current run status and return a structured recommendation."""


@dataclass(frozen=True, slots=True)
class StatusInspectionRequest:
    """Input contract for a coordinator-owned status inspection."""

    run_id: str
    run_dir: Path
    requesting_agent: str
    scope: str
    purpose: str
    status_context: Mapping[str, Any]
    artifact_refs: list[str] = field(default_factory=list)
    correlation_id: str = ""
    model: str = DEFAULT_CODEX_MODEL
    execution_id: str = ""
    codex_resume_thread_id: str = ""


@dataclass(frozen=True, slots=True)
class StatusInspectionResult:
    """Structured status inspection returned to a coordinator tool."""

    status: str
    payload: dict[str, Any]
    artifact_refs: list[str]
    result_artifact: str
    summary_artifact: str
    prompt_artifact: str
    log_artifact: str
    raw_events_artifact: str = ""
    execution_id: str = ""
    codex_thread_id: str = ""


@dataclass(slots=True)
class StatusInspectorRunner:
    """Run Codex to write and return a coordinator status JSON artifact."""

    codex_binary: str | None = None
    timeout_seconds: int = 900
    command_executor: CommandExecutor | None = None

    def run(self, request: StatusInspectionRequest) -> StatusInspectionResult:
        execution_id = request.execution_id or build_agent_execution_id(
            run_id=request.run_id,
            agent_id=request.requesting_agent,
            correlation_id=request.correlation_id or request.scope,
            intent="status_inspection",
            message_id=request.purpose,
        )
        inspector_agent_id = _inspector_agent_id(request.requesting_agent)
        artifact_owner = _inspector_artifact_owner(request.requesting_agent)
        codex_execution_id = build_codex_execution_id(
            execution_id=execution_id,
            codex_agent_id=inspector_agent_id,
        )
        inspection_dir = execution_artifact_dir(
            root=request.run_dir / artifact_owner / "status-inspections",
            execution_id=execution_id,
        )
        inspection_dir.mkdir(parents=True, exist_ok=True)
        result_path = inspection_dir / "status.json"
        context_path = inspection_dir / "context.json"
        summary_path = inspection_dir / "summary.md"
        prompt_path = inspection_dir / "prompt.md"
        log_path = inspection_dir / "execution.log"
        raw_events_path = inspection_dir / "events.jsonl"
        if raw_events_path.exists():
            raw_events_path.unlink()

        context_path.write_text(
            json.dumps(dict(request.status_context), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        prompt = build_status_inspection_prompt(
            request,
            context_path=context_path,
            result_path=result_path,
        )
        prompt_path.write_text(prompt, encoding="utf-8")
        prompt_artifact = prompt_path.relative_to(request.run_dir).as_posix()
        command = build_codex_exec_command(
            codex_binary=self.codex_binary,
            model=request.model,
            sandbox="workspace-write",
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
            run_id=request.run_id,
            agent_id=request.requesting_agent,
            work_item_id=request.correlation_id,
        )
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        payload = _load_payload(result_path)
        if not payload:
            payload = {
                "status": "inspection_failed",
                "scope": request.scope,
                "can_continue": False,
                "can_complete_sprint": False,
                "can_complete_delivery": False,
                "status_summary": "Status inspector did not write a valid status JSON artifact.",
                "errors": [f"missing_or_invalid_artifact: {_relative_path(result_path, request)}"],
            }
            write_json_artifact(result_path, payload)
        status = str(
            payload.get("status") or ("inspected" if completed.returncode == 0 else "failed")
        )
        # Same source-fix as the Codex reviewer: Codex may not write summary.md, so
        # persist a real one from the structured payload — the cited summary_artifact
        # must never be a phantom that trips downstream DB-registration validation.
        if not summary_path.exists():
            summary_path.write_text(
                str(payload.get("status_summary") or "Status inspection complete."),
                encoding="utf-8",
            )
        codex_thread_id = extract_codex_thread_id(raw_events_path) or request.codex_resume_thread_id
        result_artifact = result_path.relative_to(request.run_dir).as_posix()
        context_artifact = context_path.relative_to(request.run_dir).as_posix()
        summary_artifact = summary_path.relative_to(request.run_dir).as_posix()
        log_artifact = log_path.relative_to(request.run_dir).as_posix()
        raw_events_artifact = raw_events_path.relative_to(request.run_dir).as_posix()
        _register_status_artifacts(
            request,
            [
                (result_artifact, "debug_trace"),
                (context_artifact, "tool_request"),
                (summary_artifact, "execution_summary"),
                (prompt_artifact, "tool_request"),
                (log_artifact, "codex_log"),
                (raw_events_artifact, "debug_trace"),
            ],
        )
        input_tokens, output_tokens = extract_codex_usage(raw_events_path)
        record_model_call_event(
            request.run_dir,
            run_id=request.run_id,
            agent_id=request.requesting_agent,
            provider="openai",
            model=request.model,
            purpose="status_inspection",
            prompt_ref=prompt_artifact,
            status=status,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
        )
        return StatusInspectionResult(
            status=status,
            payload=payload,
            artifact_refs=[
                *request.artifact_refs,
                context_artifact,
            ],
            result_artifact=result_artifact,
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
        run_id: int | str,
        agent_id: str,
        work_item_id: str | None,
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
        env["AGENTIC_CODEX_SANDBOX"] = "workspace-write"
        return stream_codex_exec_to_log(
            command,
            prompt,
            self.timeout_seconds,
            log_path,
            raw_events_path,
            env=env,
            codex_execution_id=codex_execution_id,
            trace_run_dir=run_dir,
            trace_run_id=run_id,
            trace_agent_id=agent_id,
            trace_work_item_id=work_item_id,
        )


def build_status_inspection_prompt(
    request: StatusInspectionRequest,
    *,
    context_path: Path,
    result_path: Path,
) -> str:
    artifact_refs = "\n".join(f"- {artifact}" for artifact in request.artifact_refs)
    schema_hint = _schema_hint(request.scope)
    return f"""You are a Codex status inspector for agentic-company.

Requesting agent: {request.requesting_agent}
Inspection scope: {request.scope}
Run directory: {request.run_dir}
Context JSON: {context_path}
Required output JSON: {result_path}

Purpose:
{request.purpose}

Artifact references:
{artifact_refs or "- None"}

Rules:
- Read the context JSON first. It is the source of truth for current state.
- You may read referenced artifacts in the run workspace when needed.
- Do not modify product code, planning artifacts, QA artifacts, deployment
  artifacts, or handoff artifacts.
- Write exactly one machine-readable status JSON object to the Required output JSON path.
- Write the JSON file as UTF-8 without BOM. If you use Windows PowerShell,
  prefer `[System.IO.File]::WriteAllText($path, $json, [System.Text.UTF8Encoding]::new($false))`
  instead of `Set-Content -Encoding UTF8`.
- The JSON must include a concise status summary, a task/sprint table or list,
  worker calls observed, status gates, evidence refs when available, blockers,
  and completion booleans.
- Do not invent work item ids or sprint ids. Use ids from the context/artifacts only.
- Do not recommend tools, owners, routing, or next actions. The requesting
  coordinator owns routing decisions.
- Preserve task status from the context unless referenced artifacts prove a more
  advanced status. A pending task with no owner execution evidence must remain
  pending, not implemented or QA-ready.
- Do not mark a sprint or delivery complete unless the context proves all
  required work and gates are done.
- Include a status_legend explaining the status meanings used.

Required JSON shape:
```json
{schema_hint}
```

After writing the JSON file, return a short human-readable summary only.
"""


def _schema_hint(scope: str) -> str:
    if scope == "delivery":
        return json.dumps(
            {
                "status": "inspected",
                "scope": "delivery",
                "delivery_status": "running|ready_for_next_sprint|ready_to_complete|blocked",
                "sprints": [
                    {
                        "id": "sprint-01",
                        "status": "not_started|running|handoff_ready|complete|blocked",
                        "done_work_items": [],
                        "pending_work_items": [],
                        "blockers": [],
                    }
                ],
                "workers_called": [],
                "gates": {
                    "planning_done": False,
                    "all_sprints_done": False,
                    "deployment_done": False,
                    "final_handoff_ready": False,
                },
                "can_complete_delivery": False,
                "status_summary": "",
                "status_legend": {},
            },
            indent=2,
        )
    return json.dumps(
        {
            "status": "inspected",
            "scope": "sprint",
            "sprint_id": "sprint-01",
            "sprint_status": "not_started|running|ready_for_handoff|ready_to_complete|blocked",
            "tasks": [
                {
                    "id": "F1",
                    "status": (
                        "pending|assigned|in_progress|implemented|in_qa|qa_passed|qa_failed|blocked"
                    ),
                    "owner_agent": "fullstack-agent",
                    "evidence_refs": [],
                    "blockers": [],
                }
            ],
            "workers_called": [],
            "gates": {
                "implementation_done": False,
                "qa_passed": False,
                "deployment_done": False,
                "handoff_ready": False,
            },
            "can_complete_sprint": False,
            "status_summary": "",
            "status_legend": {},
        },
        indent=2,
    )


def _load_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = read_json_object_artifact(path, normalize_bom=True)
    except json.JSONDecodeError:
        return {}
    return dict(payload)


def _relative_path(path: Path, request: StatusInspectionRequest) -> str:
    return path.relative_to(request.run_dir).as_posix()


def _inspector_agent_id(requesting_agent: str) -> str:
    if requesting_agent.endswith("-agent"):
        return requesting_agent.removesuffix("-agent") + "-status-inspector"
    return requesting_agent + "-status-inspector"


def _inspector_artifact_owner(requesting_agent: str) -> str:
    return requesting_agent.removesuffix("-agent") or "status-inspector"


def _register_status_artifacts(
    request: StatusInspectionRequest,
    artifacts: list[tuple[str, str]],
) -> None:
    for relative_path, artifact_type in artifacts:
        if not (request.run_dir / relative_path).is_file():
            continue
        record_artifact_link(
            request.run_dir,
            ArtifactRegistrationRequest(
                artifact_id=artifact_id_for(request.run_id, relative_path),
                artifact_type=artifact_type,
                visibility="developer",
                owner_agent=request.requesting_agent,
                source_tool="status_inspection",
                label=Path(relative_path).name,
                relative_path=relative_path,
                run_id=request.run_id,
            ),
        )
