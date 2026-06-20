"""Codex worker adapter for the provider-neutral worker port."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Protocol

from agentic_company.integrations.codex import codex_usage_from_artifacts
from agentic_company.platform.db.models import AgentRunResult
from agentic_company.platform.status.status import WorkItemStatus, classify_work_item_status
from agentic_company.ports.worker import UsageTotals, WorkerPort, WorkRequest, WorkResult


class LegacyCodexRunner(Protocol):
    """Existing Codex runner contract kept behind the new worker port."""

    def run(self, run_dir: Path) -> AgentRunResult:
        """Run the existing Codex-backed specialist."""


class CodexWorkerAdapter:
    """Thin WorkerPort adapter over an existing specialist Codex runner."""

    provider = "codex"

    def __init__(self, runner: LegacyCodexRunner) -> None:
        self.runner = runner

    def run(self, request: WorkRequest) -> WorkResult:
        result = self.runner.run(request.run_dir)
        usage = _usage_from_result(request.run_dir, result)
        success = classify_work_item_status(result.status) is WorkItemStatus.DONE
        return WorkResult(
            success=success,
            summary=result.summary,
            output_artifacts=list(result.output_artifacts),
            error="" if success else result.summary,
            worker_session_id=result.codex_thread_id,
            provider=self.provider,
            usage=usage,
            status=result.status,
            metadata={"agent_run_result": result},
        )


class WorkerBackedAgentRunner:
    """Back-compat runner facade that executes through WorkerPort."""

    def __init__(self, worker: WorkerPort, *, agent_id: str, stage: str = "") -> None:
        self.worker = worker
        self.agent_id = agent_id
        self.stage = stage

    def run(self, run_dir: Path) -> AgentRunResult:
        result = self.worker.run(
            WorkRequest(
                run_dir=run_dir,
                agent_id=self.agent_id,
                stage=self.stage,
            )
        )
        return agent_run_result_from_work_result(result, fallback_agent_id=self.agent_id)


def agent_run_result_from_work_result(
    result: WorkResult,
    *,
    fallback_agent_id: str,
) -> AgentRunResult:
    """Convert provider-neutral WorkResult to the existing AgentRunResult contract."""

    legacy = result.metadata.get("agent_run_result")
    if isinstance(legacy, AgentRunResult):
        if legacy.codex_thread_id == result.worker_session_id:
            return legacy
        return replace(legacy, codex_thread_id=result.worker_session_id)
    return AgentRunResult(
        agent_id=fallback_agent_id,
        status=result.status or ("completed" if result.success else "failed"),
        output_artifacts=list(result.output_artifacts),
        summary=result.summary,
        codex_thread_id=result.worker_session_id,
    )


def _usage_from_result(run_dir: Path, result: AgentRunResult) -> UsageTotals | None:
    input_tokens, output_tokens = codex_usage_from_artifacts(run_dir, result.output_artifacts)
    if input_tokens is None and output_tokens is None:
        return None
    return UsageTotals(input_tokens=input_tokens, output_tokens=output_tokens)
