"""Head Agent tool implementations exposed to the AgentExecutor."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from agentic_company.agents.head.contracts import HeadDecision, HeadToolName
from agentic_company.agents.registry import agent_by_id, route_for_node
from agentic_company.integrations.codex import DEFAULT_CODEX_MODEL
from agentic_company.platform.agent_runtime import agent_env_value
from agentic_company.platform.artifact_registry import artifact_id_for
from agentic_company.platform.codex_review import (
    CodexReviewRequest,
    CodexReviewResult,
    CodexReviewRunner,
)
from agentic_company.platform.events import write_event
from agentic_company.platform.executions import build_agent_execution_id, short_hash
from agentic_company.platform.messages import AgentMessage, AgentMessageStore
from agentic_company.platform.run_trace import record_tool_call_event
from agentic_company.platform.runtime_db import (
    artifact_links_for_paths,
    artifact_paths_by_owner,
    artifact_paths_by_type,
    blocked_work_items,
    count_tool_call_events,
    materialize_planning_items,
    next_sprint_to_run,
    record_artifact_link,
    record_work_item_transition,
    sprint_completion_state,
    sprint_ids,
)
from agentic_company.platform.state import (
    DeliveryState,
    codex_resume_thread_id,
    mark_node_completed,
    record_codex_thread,
    write_delivery_state,
)
from agentic_company.platform.status_inspector import (
    StatusInspectionRequest,
    StatusInspectorLike,
    StatusInspectorRunner,
)
from agentic_company.platform.tool_contracts import (
    ArtifactRegistrationRequest,
    ToolCallResult,
    ToolDashboardUpdate,
    ToolExecutionRecord,
    dashboard_status_from_runtime_status,
    failure_mode_from_status,
    normalize_external_reference,
)
from agentic_company.platform.work_item_contracts import HEAD_WORK_ITEM_BY_NODE

HEAD_AGENT_ID = "head-agent"
HEAD_CODEX_REVIEW_AGENT_ID = "head-codex-review"
HEAD_STATUS_INSPECTOR_AGENT_ID = "head-status-inspector"
HeadWorker = Callable[[DeliveryState], DeliveryState]


@dataclass(slots=True)
class HeadWorkers:
    """Planning specialists exposed as bounded tools to Head Agent."""

    business_analyst: HeadWorker
    architect: HeadWorker
    project_manager: HeadWorker
    team_lead: HeadWorker


@dataclass(slots=True)
class HeadExecutorResult:
    """Result returned by the Head Agent executor node."""

    delivery_state: DeliveryState
    history: list[dict[str, Any]]


class CodexReviewerLike(Protocol):
    def run(self, request: CodexReviewRequest) -> CodexReviewResult:
        """Run a read-only review."""


@dataclass(slots=True)
class HeadToolbox:
    """Stateful tools exposed to the LangChain executor."""

    delivery_state: DeliveryState
    workers: HeadWorkers
    max_steps: int
    codex_reviewer: CodexReviewerLike | None = None
    status_inspector: StatusInspectorLike | None = None
    history: list[dict[str, Any]] | None = None
    initial_tool_call_count: int = 0
    current_tool_name: str = ""

    def __post_init__(self) -> None:
        if self.history is None:
            self.history = []
        materialize_planning_items(str(self.delivery_state["run_id"]))
        self.initial_tool_call_count = count_tool_call_events(
            str(self.delivery_state["run_id"]),
            agent_id=HEAD_AGENT_ID,
        )

    def run_business_analyst(
        self,
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        return self._run_worker(
            tool="run_business_analyst",
            node_name="business_analyst",
            correlation_id="PLAN-01",
            reason=reason,
            message=message,
            artifact_refs=artifact_refs,
            external_reference=external_reference,
            worker=self.workers.business_analyst,
        )

    def run_architect(
        self,
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        return self._run_worker(
            tool="run_architect",
            node_name="architecture",
            correlation_id="PLAN-02",
            reason=reason,
            message=message,
            artifact_refs=artifact_refs,
            external_reference=external_reference,
            worker=self.workers.architect,
        )

    def run_project_manager(
        self,
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        return self._run_worker(
            tool="run_project_manager",
            node_name="project_management",
            correlation_id="PLAN-03",
            reason=reason,
            message=message,
            artifact_refs=artifact_refs,
            external_reference=external_reference,
            worker=self.workers.project_manager,
        )

    def run_team_lead(
        self,
        sprint_id: str,
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        sprint_id = str(sprint_id or "").strip()
        if not sprint_id:
            self._record(
                "run_team_lead",
                "missing-sprint-target",
                reason or "Team Lead requested without an explicit PM sprint target.",
                message,
                result_status="head_waiting_for_explicit_sprint_target",
            )
            return self._tool_response(
                "Team Lead was not started because run_team_lead requires explicit sprint_id."
            )
        sprint_state = sprint_completion_state(str(self.delivery_state["run_id"]), sprint_id)
        if not sprint_state.has_items:
            self._record(
                "run_team_lead",
                sprint_id,
                reason or "Team Lead requested a sprint with no DB work items.",
                message,
                result_status="head_waiting_for_db_work_items",
            )
            return self._tool_response(
                f"Team Lead was not started because sprint {sprint_id} has no DB work items."
            )
        if sprint_state.is_complete:
            next_sprint_id = next_sprint_to_run(str(self.delivery_state["run_id"]))
            self._record(
                "run_team_lead",
                sprint_id,
                reason or "Team Lead requested an already completed sprint.",
                message,
                result_status="sprint_already_complete",
            )
            return self._tool_response(
                (
                    f"Sprint {sprint_id} is already complete in DB."
                    + (f" Next sprint to run: {next_sprint_id}." if next_sprint_id else "")
                ),
                input_summary={
                    "sprint_id": sprint_id,
                    "sprint_state": sprint_state.to_dict(),
                    "next_sprint_id": next_sprint_id,
                },
            )
        if sprint_state.is_blocked:
            self._record(
                "run_team_lead",
                sprint_id,
                reason or "Team Lead requested a blocked sprint.",
                message,
                result_status="sprint_blocked",
            )
            return self._tool_response(
                f"Team Lead was not started because sprint {sprint_id} is blocked.",
                input_summary={"sprint_id": sprint_id, "sprint_state": sprint_state.to_dict()},
            )
        return self._run_worker(
            tool="run_team_lead",
            node_name="team_lead",
            correlation_id=sprint_id,
            reason=reason,
            message=message,
            artifact_refs=artifact_refs,
            external_reference=external_reference,
            worker=self.workers.team_lead,
        )

    def complete_delivery(
        self,
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
    ) -> str:
        started = time.perf_counter()
        try:
            explicit_refs = _validated_artifact_refs(
                str(self.delivery_state["run_id"]),
                artifact_refs,
            )
        except ValueError as exc:
            self.current_tool_name = "complete_delivery"
            return self._tool_response(
                "complete_delivery contract error: " + str(exc),
                input_summary={
                    "correlation_id": "company-delivery",
                    "work_item_id": "PLAN-04",
                    "reason": reason,
                    "message": message,
                },
            )
        if limit_response := self._limit_response(message):
            return limit_response
        self.delivery_state = mark_node_completed(
            self.delivery_state,
            node_name="head",
            stage="head",
            status="head_delivery_completed",
        )
        write_head_event(
            self.delivery_state,
            "head_delivery_completed",
            {"reason": reason, "message": message},
        )
        self._record("complete_delivery", "PLAN-04", reason, message)
        return self._tool_response(
            "Head Agent completed BA -> Architect -> PM -> Team Lead.",
            artifact_refs=explicit_refs,
            duration_ms=_duration_ms(started),
            input_summary={
                "correlation_id": "company-delivery",
                "work_item_id": "PLAN-04",
                "reason": reason,
                "message": message,
                "artifact_refs": explicit_refs,
            },
        )

    def inspect_delivery_status(
        self,
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
    ) -> str:
        started = time.perf_counter()
        try:
            explicit_refs = _validated_artifact_refs(
                str(self.delivery_state["run_id"]),
                artifact_refs,
            )
        except ValueError as exc:
            self.current_tool_name = "inspect_delivery_status"
            return self._tool_response(
                "inspect_delivery_status contract error: " + str(exc),
                input_summary={
                    "correlation_id": "company-delivery",
                    "work_item_id": "PLAN-04",
                    "reason": reason,
                    "message": message,
                },
            )
        if limit_response := self._limit_response(message):
            return limit_response
        refs = _unique_paths([*_delivery_status_artifact_refs(self.delivery_state), *explicit_refs])
        inspection_request = StatusInspectionRequest(
            run_id=self.delivery_state["run_id"],
            run_dir=Path(self.delivery_state["run_dir"]),
            requesting_agent=HEAD_AGENT_ID,
            scope="delivery",
            purpose=(
                reason
                or "Inspect all PM-planned sprints, worker calls, gates, evidence, blockers, "
                "and completion readiness. Do not choose coordinator routing."
            ),
            status_context=_delivery_status_context(self.delivery_state, self.history or []),
            artifact_refs=refs,
            correlation_id="PLAN-04",
            model=agent_env_value("HEAD_STATUS_INSPECTOR_CODEX_MODEL", self.delivery_state)
            or agent_env_value("AGENT_CODEX_MODEL", self.delivery_state)
            or DEFAULT_CODEX_MODEL,
            execution_id=build_agent_execution_id(
                run_id=str(self.delivery_state["run_id"]),
                agent_id=HEAD_AGENT_ID,
                correlation_id="PLAN-04",
                intent="inspect_delivery_status",
                message_id=message or reason or "delivery-status",
            ),
            codex_resume_thread_id=codex_resume_thread_id(
                self.delivery_state, HEAD_STATUS_INSPECTOR_AGENT_ID
            ),
        )
        inspector = self.status_inspector or StatusInspectorRunner()
        result = inspector.run(inspection_request)
        self.delivery_state["last_delivery_status_inspection"] = result.payload
        record_codex_thread(
            self.delivery_state,
            HEAD_STATUS_INSPECTOR_AGENT_ID,
            result.codex_thread_id,
        )
        extend_refs = [
            result.result_artifact,
            result.summary_artifact,
            result.prompt_artifact,
            result.log_artifact,
            result.raw_events_artifact,
        ]
        self.delivery_state["last_delivery_status_inspection_artifacts"] = [
            ref for ref in extend_refs if ref
        ]
        _register_head_tool_artifacts(
            self.delivery_state,
            extend_refs,
            artifact_type="status_inspection",
            source_tool="inspect_delivery_status",
            work_item_id="PLAN-04",
        )
        self._record(
            "inspect_delivery_status",
            "PLAN-04",
            reason,
            message,
            result_status=str(result.payload.get("delivery_status") or result.status),
        )
        return self._tool_response(
            f"Delivery status inspection {result.status}.",
            downstream_response={
                "from_agent": "codex-status-inspector",
                "intent": "inspect_delivery_status",
                "content": result.payload,
                "artifact_refs": [
                    result.result_artifact,
                    result.summary_artifact,
                    result.prompt_artifact,
                    result.log_artifact,
                    result.raw_events_artifact,
                ],
                "correlation_id": "PLAN-04",
                "execution_id": result.execution_id,
                "codex_thread_id": result.codex_thread_id,
            },
            duration_ms=_duration_ms(started),
            input_summary={
                "correlation_id": "company-delivery",
                "work_item_id": "PLAN-04",
                "reason": reason,
                "message": message,
                "artifact_refs": explicit_refs,
                "execution_id": result.execution_id,
            },
        )

    def codex_review(
        self,
        target_agent: str = "",
        purpose: str = "",
        question: str = "",
        artifact_refs: str = "",
        intent: str = "review_feedback",
        correlation_id: str = "upstream-planning",
        reason: str = "",
        message: str = "",
    ) -> str:
        started = time.perf_counter()
        if limit_response := self._limit_response(message or question):
            return limit_response
        refs = _split_artifact_refs(artifact_refs)
        review_request = CodexReviewRequest(
            run_id=self.delivery_state["run_id"],
            run_dir=Path(self.delivery_state["run_dir"]),
            requesting_agent=HEAD_AGENT_ID,
            target_agent=target_agent or None,
            correlation_id=correlation_id,
            purpose=purpose or reason or "Review referenced planning artifacts.",
            question=question
            or message
            or reason
            or "Review the referenced artifacts according to the requesting agent's purpose.",
            artifact_refs=refs,
            execution_id=build_agent_execution_id(
                run_id=str(self.delivery_state["run_id"]),
                agent_id=HEAD_AGENT_ID,
                correlation_id=correlation_id,
                intent="codex_review",
                message_id=question or message or reason or target_agent,
            ),
            codex_resume_thread_id=codex_resume_thread_id(
                self.delivery_state,
                HEAD_CODEX_REVIEW_AGENT_ID,
            ),
        )
        reviewer = self.codex_reviewer or CodexReviewRunner()
        result = reviewer.run(review_request)
        record_codex_thread(
            self.delivery_state,
            HEAD_CODEX_REVIEW_AGENT_ID,
            result.codex_thread_id,
        )
        sent_message = None
        known_target_agent = _known_agent_id(target_agent)
        if known_target_agent:
            sent_message = AgentMessageStore(self.delivery_state["run_dir"]).append(
                AgentMessage(
                    from_agent=HEAD_AGENT_ID,
                    to_agent=known_target_agent,
                    intent=intent or "review_feedback",
                    content=result.content,
                    artifact_refs=refs,
                    correlation_id=correlation_id,
                    execution_id=result.execution_id or None,
                )
            )
        _register_head_tool_artifacts(
            self.delivery_state,
            [
                result.summary_artifact,
                result.prompt_artifact,
                result.log_artifact,
                result.raw_events_artifact,
            ],
            artifact_type="review_output",
            source_tool="codex_review",
            work_item_id="PLAN-04",
        )
        self._record("codex_review", target_agent or correlation_id, reason, message)
        return self._tool_response(
            f"Codex review {result.status}.",
            downstream_response={
                "from_agent": "codex-review",
                "intent": "codex_review",
                "content": result.content,
                "artifact_refs": [
                    result.summary_artifact,
                    result.prompt_artifact,
                    result.log_artifact,
                    result.raw_events_artifact,
                ],
                "message_id": sent_message.message_id if sent_message else None,
                "to_agent": known_target_agent or None,
                "correlation_id": correlation_id,
                "execution_id": result.execution_id,
                "codex_thread_id": result.codex_thread_id,
                "review_authority": "advisory_only",
                "can_block_delivery": False,
                "message_delivery": (
                    "sent_to_target_agent"
                    if sent_message
                    else "not_sent_no_known_target_agent"
                    if target_agent
                    else "not_sent_head_only_review"
                ),
            },
            duration_ms=_duration_ms(started),
            input_summary={
                "target_agent": target_agent,
                "correlation_id": correlation_id,
                "work_item_id": _head_review_work_item_id(correlation_id),
                "purpose": purpose,
                "question": question,
                "artifact_refs": refs,
                "intent": intent,
                "reason": reason,
                "message": message,
                "execution_id": result.execution_id,
            },
        )

    def block_planning(
        self,
        reason: str,
        correlation_id: str = "upstream-planning",
        message: str = "",
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        started = time.perf_counter()
        try:
            explicit_refs = _validated_artifact_refs(
                str(self.delivery_state["run_id"]),
                artifact_refs,
            )
        except ValueError as exc:
            self.current_tool_name = "block_planning"
            return self._tool_response(
                "block_planning contract error: " + str(exc),
                input_summary={
                    "correlation_id": correlation_id,
                    "reason": reason,
                    "message": message,
                },
            )
        try:
            external_ref = normalize_external_reference(external_reference)
        except ValueError as exc:
            self.current_tool_name = "block_planning"
            return self._tool_response(
                "block_planning contract error: " + str(exc),
                input_summary={
                    "correlation_id": correlation_id,
                    "reason": reason,
                    "message": message,
                },
            )
        self.delivery_state = mark_node_completed(
            self.delivery_state,
            node_name="head",
            stage="head",
            status="head_planning_blocked",
        )
        self.delivery_state["blockers"] = [*self.delivery_state.get("blockers", []), reason]
        write_head_event(
            self.delivery_state,
            "head_planning_blocked",
            HeadDecision("block_planning", reason, correlation_id, message).to_dict(),
        )
        self._record("block_planning", correlation_id, reason, message)
        return self._tool_response(
            "Planning blocked: " + reason,
            artifact_refs=explicit_refs,
            duration_ms=_duration_ms(started),
            input_summary={
                "correlation_id": correlation_id,
                "reason": reason,
                "message": message,
                "artifact_refs": explicit_refs,
                **({"external_reference": external_ref} if external_ref else {}),
            },
        )

    def result(self) -> HeadExecutorResult:
        write_history_artifact(self.delivery_state, self.history or [])
        checkpoint_delivery_state(self.delivery_state)
        return HeadExecutorResult(self.delivery_state, list(self.history or []))

    def tool_calls_made(self) -> bool:
        return (
            count_tool_call_events(str(self.delivery_state["run_id"]), agent_id=HEAD_AGENT_ID)
            > self.initial_tool_call_count
        )

    def reached_terminal_state(self) -> bool:
        if self.delivery_state.get("blockers"):
            return True
        status = str(self.delivery_state.get("status") or "")
        return status in {
            "head_delivery_completed",
            "head_planning_blocked",
        }

    def block_incomplete_execution(self) -> None:
        run_id = str(self.delivery_state["run_id"])
        next_sprint_id = next_sprint_to_run(run_id)
        status = str(self.delivery_state.get("status") or "")
        reason = (
            "Head Agent executor stopped before reaching a terminal delivery state."
            + (f" Next DB sprint still pending: {next_sprint_id}." if next_sprint_id else "")
            + f" Current status: {status or 'unknown'}."
        )
        self.block_planning(
            reason=reason,
            correlation_id="PLAN-04",
            message="Head must continue sprint scheduling or explicitly block with a real reason.",
        )
        record_work_item_transition(
            ToolExecutionRecord(
                run_id=run_id,
                work_item_id="PLAN-04",
                sprint_id="planning",
                owner_agent=HEAD_AGENT_ID,
                tool_name="block_planning",
                tool_call_id=_tool_call_id(
                    self.delivery_state,
                    "block_planning",
                    len(self.history or []),
                ),
                attempt_id="incomplete-execution",
                status="blocked",
                activity_message=reason,
            )
        )

    def _run_worker(
        self,
        *,
        tool: HeadToolName,
        node_name: str,
        correlation_id: str,
        reason: str,
        message: str,
        worker: HeadWorker,
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        started = time.perf_counter()
        try:
            external_ref = normalize_external_reference(external_reference)
        except ValueError as exc:
            self.current_tool_name = tool
            return self._tool_response(
                f"{tool} contract error: {exc}",
                input_summary={
                    "tool": tool,
                    "node_name": node_name,
                    "correlation_id": correlation_id,
                    "reason": reason,
                    "message": message,
                },
            )
        if limit_response := self._limit_response(message):
            return limit_response
        try:
            explicit_refs = _validated_artifact_refs(
                str(self.delivery_state["run_id"]),
                artifact_refs,
            )
        except ValueError as exc:
            self.current_tool_name = tool
            return self._tool_response(
                f"{tool} contract error: {exc}",
                input_summary={
                    "tool": tool,
                    "node_name": node_name,
                    "correlation_id": correlation_id,
                    "reason": reason,
                    "message": message,
                },
            )
        updated = {**self.delivery_state}
        if node_name == "team_lead":
            updated["team_lead_sprint_id"] = correlation_id
        item_id = HEAD_WORK_ITEM_BY_NODE.get(node_name, correlation_id)
        outbound = append_agent_call_message(
            updated,
            node_name=node_name,
            correlation_id=correlation_id,
            reason=reason,
            message=message,
            artifact_refs=explicit_refs,
        )
        execution_id = outbound.execution_id or ""
        updated["agent_execution_id"] = execution_id
        updated["agent_execution_intent"] = outbound.intent
        updated["agent_execution_agent_id"] = outbound.to_agent
        self.delivery_state = cast(DeliveryState, updated)
        record_work_item_transition(
            ToolExecutionRecord(
                run_id=str(self.delivery_state["run_id"]),
                work_item_id=item_id,
                sprint_id="planning",
                owner_agent=outbound.to_agent,
                tool_name=tool,
                tool_call_id=execution_id or _tool_call_id(self.delivery_state, tool, 0),
                attempt_id="start",
                status="in_progress",
                activity_message=f"{outbound.to_agent} started {item_id}.",
            )
        )
        write_request(
            self.delivery_state,
            kind=f"{node_name}_request",
            target_agent=outbound.to_agent,
            payload={
                "correlation_id": correlation_id,
                "work_item_id": item_id,
                "message": message,
                "message_id": outbound.message_id,
                "message_intent": outbound.intent,
                "execution_id": execution_id,
                "artifact_refs": outbound.artifact_refs,
                "external_reference": external_ref,
            },
        )
        write_head_event(
            self.delivery_state,
            "head_worker_started",
            {
                "node": node_name,
                "target_agent": outbound.to_agent,
                "reason": reason,
                "execution_id": execution_id,
            },
        )
        checkpoint_delivery_state(self.delivery_state)
        self.delivery_state = worker(self.delivery_state)
        item_status = self._worker_item_finish_status(
            node_name=node_name,
            correlation_id=correlation_id,
        )
        finish_message = (
            f"{outbound.to_agent} completed {item_id}."
            if item_status == "done"
            else f"{outbound.to_agent} updated {item_id}."
        )
        record_work_item_transition(
            ToolExecutionRecord(
                run_id=str(self.delivery_state["run_id"]),
                work_item_id=item_id,
                sprint_id="planning",
                owner_agent=outbound.to_agent,
                tool_name=tool,
                tool_call_id=execution_id or _tool_call_id(self.delivery_state, tool, 0),
                attempt_id="finish",
                status=item_status,
                activity_message=finish_message,
            )
        )
        downstream_response = latest_downstream_response(
            self.delivery_state,
            from_agent=outbound.to_agent,
            correlation_id=correlation_id,
        )
        write_head_event(
            self.delivery_state,
            "head_worker_completed",
            {
                "node": node_name,
                "target_agent": outbound.to_agent,
                "stage": self.delivery_state.get("stage"),
                "status": self.delivery_state.get("status"),
                "execution_id": execution_id,
            },
        )
        self._record(tool, correlation_id, reason, message)
        return self._tool_response(
            f"{tool} completed with status {self.delivery_state.get('status')}.",
            downstream_response=downstream_response,
            duration_ms=_duration_ms(started),
            input_summary={
                "tool": tool,
                "node_name": node_name,
                "correlation_id": correlation_id,
                "work_item_id": item_id,
                "reason": reason,
                "message": message,
                "artifact_refs": explicit_refs,
                "execution_id": execution_id,
                "target_agent": outbound.to_agent,
                **({"external_reference": external_ref} if external_ref else {}),
            },
        )

    def _worker_item_finish_status(self, *, node_name: str, correlation_id: str) -> str:
        if self.delivery_state.get("blockers"):
            return "blocked"
        if node_name != "team_lead":
            return "done"
        sprint_state = sprint_completion_state(str(self.delivery_state["run_id"]), correlation_id)
        if sprint_state.is_blocked:
            return "blocked"
        if sprint_state.is_final and sprint_state.is_complete:
            return "done"
        return "in_progress"

    def _record(
        self,
        tool: HeadToolName,
        correlation_id: str | None,
        reason: str,
        message: str,
        *,
        result_status: str | None = None,
    ) -> None:
        step = len(self.history or []) + 1
        decision = HeadDecision(tool, reason or "No reason provided.", correlation_id, message)
        decision_path = write_decision_artifact(self.delivery_state, step, decision)
        write_head_event(
            self.delivery_state,
            "head_decision",
            {"step": step, "decision": decision.to_dict(), "artifact": decision_path},
        )
        status = result_status or str(self.delivery_state.get("status") or "")
        self.current_tool_name = tool
        history = self.history or []
        history.append(
            {
                "step": step,
                "tool": tool,
                "correlation_id": correlation_id,
                "reason": reason,
                "message": message,
                "result_status": status,
                "stage": self.delivery_state.get("stage"),
                "blockers": self.delivery_state.get("blockers", []),
            }
        )
        self.history = history
        write_history_artifact(self.delivery_state, history)
        write_head_event(
            self.delivery_state,
            "head_tool_completed",
            {"tool": tool, "status": status},
        )
        checkpoint_delivery_state(self.delivery_state)

    def _step_limit_reached(self) -> bool:
        return len(self.history or []) >= self.max_steps

    def _limit_response(self, message: str) -> str:
        if not self._step_limit_reached():
            return ""
        return self.block_planning(reason="Head Agent exceeded max tool calls.", message=message)

    def _tool_response(
        self,
        message: str,
        *,
        downstream_response: dict[str, Any] | None = None,
        artifact_refs: list[str] | None = None,
        duration_ms: int | None = None,
        input_summary: dict[str, Any] | None = None,
    ) -> str:
        status = str(self.delivery_state.get("status") or "")
        output_artifacts = _response_artifact_refs(
            artifact_refs=artifact_refs,
            downstream_response=downstream_response,
        )
        tool_name = self.current_tool_name
        if not tool_name:
            raise RuntimeError("Head tool response requires explicit current tool name.")
        failure_mode = failure_mode_from_status(status, self.delivery_state.get("blockers", []))
        dashboard_status = dashboard_status_from_runtime_status(status)
        artifact_links = artifact_links_for_paths(
            str(self.delivery_state["run_id"]),
            output_artifacts,
        )
        structured = ToolCallResult(
            tool_name=tool_name,
            status=status,
            business_summary=message,
            tool_call_id=_tool_call_id(self.delivery_state, tool_name, len(self.history or [])),
            developer_diagnostics={
                "stage": self.delivery_state.get("stage"),
                "completed_nodes": self.delivery_state.get("completed_nodes", []),
                "blockers": self.delivery_state.get("blockers", []),
                "team_lead_sprint_id": self.delivery_state.get("team_lead_sprint_id"),
            },
            output_artifacts=artifact_links,
            failure_mode=failure_mode,
            recommended_next_action=_recommended_next_action(status, failure_mode),
            dashboard_update=ToolDashboardUpdate(
                status=dashboard_status,
                summary=message,
                comment=message,
                artifact_links=artifact_links,
                labels=(failure_mode,) if failure_mode else (),
            ),
        )
        snapshot: dict[str, Any] = {
            **structured.to_dict(),
            "message": message,
            "status": self.delivery_state.get("status"),
            "stage": self.delivery_state.get("stage"),
            "completed_nodes": self.delivery_state.get("completed_nodes", []),
            "blockers": self.delivery_state.get("blockers", []),
        }
        if artifact_refs is not None:
            snapshot["artifact_refs"] = artifact_refs
        if downstream_response is not None:
            snapshot["downstream_response"] = downstream_response
        record_tool_call_event(
            Path(self.delivery_state["run_dir"]),
            run_id=str(self.delivery_state.get("run_id") or ""),
            agent_id=HEAD_AGENT_ID,
            tool_name=tool_name,
            tool_call_id=structured.tool_call_id,
            work_item_id=_head_work_item_id(self.delivery_state, input_summary),
            input_summary=input_summary
            or {
                "latest_history_tool": tool_name,
                "stage": self.delivery_state.get("stage"),
            },
            output_summary=structured.to_dict(),
            artifact_ids=[
                ref.artifact_id
                for ref in structured.output_artifacts
                if getattr(ref, "artifact_id", "")
            ],
            status=status,
            failure_mode=failure_mode,
            duration_ms=duration_ms,
        )
        return json.dumps(snapshot, sort_keys=True)


def append_agent_call_message(
    state: DeliveryState,
    *,
    node_name: str,
    correlation_id: str,
    reason: str,
    message: str,
    artifact_refs: list[str] | None = None,
) -> AgentMessage:
    target_agent = target_agent_id(node_name)
    intent = agent_message_intent(node_name)
    content = _agent_call_message(message=message, reason=reason)
    message_id = f"msg-{uuid4().hex}"
    execution_id = build_agent_execution_id(
        run_id=str(state["run_id"]),
        agent_id=target_agent,
        correlation_id=correlation_id,
        intent=intent,
        message_id=message_id,
    )
    outbound = AgentMessageStore(state["run_dir"]).append(
        AgentMessage(
            from_agent=HEAD_AGENT_ID,
            to_agent=target_agent,
            intent=intent,
            content=content,
            artifact_refs=_unique_paths(
                [*_agent_call_artifacts(node_name, state), *(artifact_refs or [])]
            ),
            message_id=message_id,
            correlation_id=correlation_id,
            execution_id=execution_id,
        )
    )
    state["agent_call_message_id"] = outbound.message_id
    state["agent_call_correlation_id"] = correlation_id
    state["agent_execution_id"] = execution_id
    return outbound


def latest_downstream_response(
    state: DeliveryState,
    *,
    from_agent: str,
    correlation_id: str,
) -> dict[str, Any] | None:
    messages = AgentMessageStore(state["run_dir"]).read(
        from_agent=from_agent,
        to_agent=HEAD_AGENT_ID,
        intent="agent_response",
        correlation_id=correlation_id,
        limit=1,
    )
    if not messages:
        return None
    message = messages[-1]
    return {
        "from_agent": message.from_agent,
        "intent": message.intent,
        "content": message.content,
        "artifact_refs": message.artifact_refs,
        "message_id": message.message_id,
        "correlation_id": message.correlation_id,
        "execution_id": message.execution_id,
    }


def _delivery_status_context(
    state: DeliveryState,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    sprint_ids = _db_sprint_ids(str(state["run_id"]))
    return {
        "run_id": state.get("run_id"),
        "stage": state.get("stage"),
        "status": state.get("status"),
        "team_lead_sprint_id": state.get("team_lead_sprint_id"),
        "next_sprint_to_run": next_sprint_to_run(str(state["run_id"])),
        "sprints": [
            sprint_completion_state(str(state["run_id"]), sprint_id).to_dict()
            for sprint_id in sprint_ids
        ],
        "completed_nodes": state.get("completed_nodes", []),
        "qa_status": state.get("qa_status"),
        "deployment_status": state.get("deployment_status"),
        "post_deploy_qa_status": state.get("post_deploy_qa_status"),
        "public_url": state.get("public_url"),
        "public_urls": state.get("public_urls", []),
        "blockers": state.get("blockers", []),
        "head_history": history,
        "messages": _recent_messages(state, limit=30),
        "artifact_refs": _delivery_status_artifact_refs(state),
        "status_rules": {
            "team_lead_sprint_handoff_ready": (
                "Sprint-level handoff evidence only; Head still needs inspector confirmation "
                "before company delivery can complete."
            ),
            "can_complete_delivery": (
                "true only when every DB-planned work item, sprint handoff, and required "
                "final delivery gate is done or explicitly out of scope."
            ),
        },
    }


def _db_sprint_ids(run_id: str) -> list[str]:
    return sprint_ids(run_id)


def _delivery_status_artifact_refs(state: DeliveryState) -> list[str]:
    return _unique_paths(
        [
        "00-requirements.md",
        "head/planning-history.json",
        "upstream-planning/project-management/release-plan.md",
        "upstream-planning/project-management/release-plan.json",
        "upstream-planning/project-management/planned-work-items.json",
        "upstream-planning/project-management/roadmap.csv",
        "upstream-planning/project-management/risks-and-dependencies.md",
        *artifact_paths_by_type(
            str(state["run_id"]),
            {"handoff", "release_report", "deployment_summary", "qa_report"},
        ),
        ]
    )


def _recent_messages(state: DeliveryState, *, limit: int) -> list[dict[str, Any]]:
    try:
        messages = AgentMessageStore(state["run_dir"]).read(limit=limit)
    except Exception:
        return []
    return [
        {
            "from_agent": message.from_agent,
            "to_agent": message.to_agent,
            "intent": message.intent,
            "correlation_id": message.correlation_id,
            "execution_id": message.execution_id,
            "artifact_refs": message.artifact_refs,
            "content": message.content[:1000],
        }
        for message in messages
    ]


def target_agent_id(node_name: str) -> str:
    return route_for_node(node_name)[0]


def agent_message_intent(node_name: str) -> str:
    return route_for_node(node_name)[1]


def _known_agent_id(agent_id: str) -> str:
    candidate = agent_id.strip()
    if not candidate:
        return ""
    try:
        return agent_by_id(candidate).agent_id
    except KeyError:
        return ""


def _agent_call_message(*, message: str, reason: str) -> str:
    content = message.strip() or reason.strip()
    return content or "Please handle the assigned planning task and report the result."


def _agent_call_artifacts(node_name: str, state: DeliveryState) -> list[str]:
    if node_name == "business_analyst":
        return _existing_paths(state, ["00-requirements.md"])
    if node_name == "architecture":
        return _unique_paths(
            [
                "00-requirements.md",
                *_artifact_paths_by_owner(state, "business-analyst-agent"),
                "upstream-planning/business-analysis.md",
                "upstream-planning/business-analysis.json",
            ]
        )
    if node_name == "project_management":
        return _unique_paths(
            [
                "00-requirements.md",
                *_artifact_paths_by_owner(state, "business-analyst-agent"),
                *_artifact_paths_by_owner(state, "architect-agent"),
                "upstream-planning/business-analysis.md",
                "upstream-planning/business-analysis.json",
                "upstream-planning/architecture.md",
                "upstream-planning/architecture.json",
                "upstream-planning/architecture.mmd",
            ]
        )
    if node_name == "team_lead":
        return _unique_paths(
            [
                "00-requirements.md",
                *_artifact_paths_by_owner(state, "business-analyst-agent"),
                *_artifact_paths_by_owner(state, "architect-agent"),
                *_artifact_paths_by_owner(state, "project-manager-agent"),
                "upstream-planning/business-analysis.md",
                "upstream-planning/business-analysis.json",
                "upstream-planning/architecture.md",
                "upstream-planning/architecture.json",
                "upstream-planning/architecture.mmd",
                "upstream-planning/project-management/release-plan.md",
                "upstream-planning/project-management/release-plan.json",
                "upstream-planning/project-management/planned-work-items.json",
                "upstream-planning/project-management/risks-and-dependencies.md",
                "upstream-planning/project-management/roadmap.csv",
            ]
        )
    return []


def _split_artifact_refs(value: str) -> list[str]:
    refs: list[str] = []
    for raw in value.replace(";", ",").replace("\n", ",").split(","):
        item = raw.strip()
        if item and item not in refs:
            refs.append(item)
    return refs


def _validated_artifact_refs(run_id: str, artifact_refs: str) -> list[str]:
    refs = _split_artifact_refs(artifact_refs)
    if refs:
        artifact_links_for_paths(run_id, refs)
    return refs


def _artifact_paths_by_owner(state: DeliveryState, owner_agent: str) -> list[str]:
    return artifact_paths_by_owner(str(state["run_id"]), owner_agent)


def _existing_paths(state: DeliveryState, paths: list[str]) -> list[str]:
    run_dir = Path(state["run_dir"])
    return [path for path in paths if (run_dir / path).exists()]


def _unique_paths(paths: list[str]) -> list[str]:
    unique: list[str] = []
    for path in paths:
        if path and path not in unique:
            unique.append(path)
    return unique


def _head_work_item_for_request_kind(kind: str) -> str:
    node = kind.removesuffix("_request")
    return HEAD_WORK_ITEM_BY_NODE.get(node, "PLAN-04")


def _head_work_item_for_tool(tool: str) -> str:
    return {
        "run_business_analyst": "PLAN-01",
        "run_architect": "PLAN-02",
        "run_project_manager": "PLAN-03",
        "run_team_lead": "PLAN-04",
    }.get(tool, "PLAN-04")


def _head_review_work_item_id(correlation_id: str) -> str:
    normalized = str(correlation_id or "").strip()
    return normalized if normalized in {"PLAN-01", "PLAN-02", "PLAN-03", "PLAN-04"} else "PLAN-04"


def write_request(
    state: DeliveryState,
    *,
    kind: str,
    target_agent: str,
    payload: dict[str, Any],
) -> Path:
    run_dir = Path(state["run_dir"])
    request_dir = run_dir / "head" / "requests"
    request_dir.mkdir(parents=True, exist_ok=True)
    execution_id = str(payload.get("execution_id") or state.get("agent_execution_id") or "")
    suffix = f"-{short_hash(execution_id)}" if execution_id else ""
    path = request_dir / f"{kind}{suffix}.json"
    body = {
        "kind": kind,
        "source_agent": HEAD_AGENT_ID,
        "target_agent": target_agent,
        "run_id": state["run_id"],
        "stage": state["stage"],
        "status": state["status"],
        **payload,
    }
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _register_head_artifact(
        state,
        path.relative_to(run_dir).as_posix(),
        artifact_type="tool_request",
        visibility="internal",
        source_tool="head_request",
        work_item_id=_head_work_item_for_request_kind(kind),
    )
    return path


def write_decision_artifact(state: DeliveryState, step: int, decision: HeadDecision) -> str:
    relative_path = f"head/decisions/{step:03d}-{decision.tool}.json"
    write_json_artifact(state, relative_path, decision.to_dict())
    _register_head_artifact(
        state,
        relative_path,
        artifact_type="debug_trace",
        visibility="internal",
        source_tool=decision.tool,
        work_item_id=_head_work_item_for_tool(decision.tool),
    )
    return relative_path


def write_history_artifact(state: DeliveryState, history: list[dict[str, Any]]) -> None:
    relative_path = "head/planning-history.json"
    write_json_artifact(state, relative_path, {"steps": history})
    _register_head_artifact(
        state,
        relative_path,
        artifact_type="debug_trace",
        visibility="developer",
        source_tool="head_history",
        work_item_id="PLAN-04",
    )


def write_head_result(state: DeliveryState, history: list[dict[str, Any]]) -> None:
    blockers = _db_head_blockers(state)
    result = {
        "status": state.get("status"),
        "stage": state.get("stage"),
        "completed_nodes": state.get("completed_nodes", []),
        "blockers": blockers,
        "history_artifact": "head/planning-history.json",
    }
    relative_path = "head/result.json"
    write_json_artifact(state, relative_path, result)
    _register_head_artifact(
        state,
        relative_path,
        artifact_type="execution_summary",
        visibility="developer",
        source_tool="head_result",
        work_item_id="PLAN-04",
    )
    write_head_event(state, "head_agent_completed", {"result": result, "steps": len(history)})


def _db_head_blockers(state: DeliveryState) -> list[str]:
    run_id = str(state["run_id"])
    blockers: list[str] = []
    for item in blocked_work_items(run_id):
        detail = item.blocker.strip()
        if detail:
            blockers.append(f"{item.work_item_id}: {detail}")
        else:
            blockers.append(f"{item.work_item_id} is blocked.")
    for sprint_id in sprint_ids(run_id):
        sprint_state = sprint_completion_state(run_id, sprint_id)
        if sprint_state.is_blocked:
            blockers.append(f"{sprint_id} is blocked.")
    return _unique_paths(blockers)


def write_json_artifact(state: DeliveryState, relative_path: str, payload: dict[str, Any]) -> Path:
    path = Path(state["run_dir"]) / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_head_event(state: DeliveryState, event: str, data: dict[str, Any]) -> None:
    write_event(
        Path(state["run_dir"]),
        state["run_id"],
        HEAD_AGENT_ID,
        event,
        data,
    )


def checkpoint_delivery_state(state: DeliveryState) -> None:
    write_delivery_state(state)


def _register_head_artifact(
    state: DeliveryState,
    relative_path: str,
    *,
    artifact_type: str,
    visibility: str,
    source_tool: str,
    work_item_id: str,
) -> None:
    record_artifact_link(
        Path(state["run_dir"]),
        ArtifactRegistrationRequest(
            artifact_id=artifact_id_for(str(state["run_id"]), relative_path),
            artifact_type=artifact_type,
            visibility=visibility,
            owner_agent=HEAD_AGENT_ID,
            source_tool=source_tool,
            label=Path(relative_path).name,
            relative_path=relative_path,
            run_id=str(state["run_id"]),
            work_item_id=work_item_id,
            task_scoped=True,
        ),
    )


def _register_head_tool_artifacts(
    state: DeliveryState,
    relative_paths: list[str],
    *,
    artifact_type: str,
    source_tool: str,
    work_item_id: str,
) -> None:
    run_dir = Path(state["run_dir"])
    for relative_path in _unique_paths([path for path in relative_paths if path]):
        if not (run_dir / relative_path).is_file():
            continue
        _register_head_artifact(
            state,
            relative_path,
            artifact_type=artifact_type,
            visibility="internal",
            source_tool=source_tool,
            work_item_id=work_item_id,
        )


def _response_artifact_refs(
    *,
    artifact_refs: list[str] | None = None,
    downstream_response: dict[str, Any] | None = None,
) -> list[str]:
    refs = list(artifact_refs or [])
    if downstream_response:
        for ref in downstream_response.get("artifact_refs", []):
            if ref:
                refs.append(str(ref))
    return _unique_paths(refs)


def _tool_call_id(state: DeliveryState, tool_name: str, step: int) -> str:
    return f"{state.get('run_id', '')}:head-agent:{tool_name}:{step}"


def _recommended_next_action(status: str, failure_mode: str | None) -> str:
    if failure_mode in {"blocked", "failed"}:
        return (
            "Inspect blockers and decide whether operator intervention or a repair run is needed."
        )
    if failure_mode == "needs_repair":
        return "Route a bounded repair to the owning downstream agent."
    if "completed" in status or "ready" in status:
        return "Continue to the next PM-planned stage or complete delivery when inspector confirms."
    return "Inspect delivery status and route the next useful planned action."


def _head_work_item_id(
    state: DeliveryState,
    input_summary: dict[str, Any] | None,
) -> str | None:
    if input_summary:
        for key in ("work_item_id", "node_name"):
            if input_summary.get(key):
                return str(input_summary[key])
    return None


def _duration_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
