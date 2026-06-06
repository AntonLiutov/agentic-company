"""Team Lead tool implementations backed by explicit DB work-item contracts."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from agentic_company.agents.handoff.contracts import (
    FINAL_PROJECT_REPORT_SCOPE,
    handoff_contract_paths_for_scope,
)
from agentic_company.agents.registry import route_for_node
from agentic_company.agents.team_lead.contracts import TeamLeadDecision, TeamLeadToolName
from agentic_company.platform.artifact_registry import artifact_id_for
from agentic_company.platform.codex_review import (
    CodexReviewRequest,
    CodexReviewResult,
    CodexReviewRunner,
)
from agentic_company.platform.events import write_event
from agentic_company.platform.executions import build_agent_execution_id, short_hash
from agentic_company.platform.messages import (
    AgentMessage,
    AgentMessageStore,
    append_agent_response,
)
from agentic_company.platform.run_trace import record_tool_call_event
from agentic_company.platform.runtime_db import (
    artifact_links_for_paths,
    artifact_paths_by_type,
    completed_work_item_ids,
    count_tool_call_events,
    get_work_item,
    list_sprint_work_items,
    mark_sprint_blocked,
    mark_sprint_done,
    mark_sprint_started,
    next_work_item,
    record_artifact_link,
    record_work_item_transition,
    sprint_completion_state,
    sprint_is_final,
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
)

TEAM_LEAD_AGENT_ID = "team-lead-agent"
TEAM_LEAD_CODEX_REVIEW_AGENT_ID = "team-lead-codex-review"
TEAM_LEAD_STATUS_INSPECTOR_AGENT_ID = "team-lead-status-inspector"
TeamLeadWorker = Callable[[DeliveryState], DeliveryState]


@dataclass(slots=True)
class TeamLeadWorkers:
    """Specialist agents exposed as bounded tools to Team Lead."""

    fullstack: TeamLeadWorker
    qa: TeamLeadWorker
    deployment: TeamLeadWorker
    handoff: TeamLeadWorker


class CodexReviewerLike(Protocol):
    def run(self, request: CodexReviewRequest) -> CodexReviewResult:
        """Run a read-only review."""


@dataclass(slots=True)
class TeamLeadExecutorResult:
    """Result returned by the Team Lead executor node."""

    delivery_state: DeliveryState
    history: list[dict[str, Any]]


@dataclass(slots=True)
class TeamLeadToolbox:
    """Stateful strict-contract tools exposed to the LangChain executor."""

    delivery_state: DeliveryState
    sprint: dict[str, Any]
    workers: TeamLeadWorkers
    max_steps: int
    codex_reviewer: CodexReviewerLike | None = None
    status_inspector: StatusInspectorLike | None = None
    history: list[dict[str, Any]] | None = None
    initial_tool_call_count: int = 0

    def __post_init__(self) -> None:
        if self.history is None:
            self.history = []
        self.initial_tool_call_count = count_tool_call_events(
            str(self.delivery_state["run_id"]),
            agent_id=TEAM_LEAD_AGENT_ID,
        )
        try:
            mark_sprint_started(str(self.delivery_state["run_id"]), self.sprint_id)
        except ValueError:
            pass

    @property
    def sprint_id(self) -> str:
        return str(self.sprint.get("sprint_id") or self.sprint.get("id") or "sprint-01")

    def run_fullstack(self, work_item_id: str, reason: str = "", message: str = "") -> str:
        return self._run_worker(
            tool="run_fullstack",
            node_name="fullstack",
            work_item_id=work_item_id,
            reason=reason,
            message=message,
            worker=self.workers.fullstack,
        )

    def run_qa(self, work_item_id: str, reason: str = "", message: str = "") -> str:
        return self._run_worker(
            tool="run_qa",
            node_name="qa",
            work_item_id=work_item_id,
            reason=reason,
            message=message,
            worker=self.workers.qa,
        )

    def run_deployment(self, work_item_id: str, reason: str = "", message: str = "") -> str:
        return self._run_worker(
            tool="run_deployment",
            node_name="deployment",
            work_item_id=work_item_id,
            reason=reason,
            message=message,
            worker=self.workers.deployment,
        )

    def run_post_deploy_qa(
        self,
        work_item_id: str,
        reason: str = "",
        message: str = "",
    ) -> str:
        return self._run_worker(
            tool="run_post_deploy_qa",
            node_name="qa",
            work_item_id=work_item_id,
            reason=reason,
            message=message,
            worker=self.workers.qa,
        )

    def run_handoff(
        self,
        work_item_id: str,
        handoff_scope: str,
        sprint_id: str = "",
        reason: str = "",
        message: str = "",
    ) -> str:
        item_id = _clean_work_item_id(work_item_id)
        if error := self._contract_error("run_handoff", item_id, reason, message):
            return error
        try:
            contract_paths = handoff_contract_paths_for_scope(handoff_scope, sprint_id=sprint_id)
        except ValueError as exc:
            return self._contract_error_response("run_handoff", item_id, str(exc), message)
        if handoff_scope == FINAL_PROJECT_REPORT_SCOPE and not _final_project_report_allowed(
            self.delivery_state,
            self.sprint_id,
        ):
            return self._contract_error_response(
                "run_handoff",
                item_id,
                "Final project report is not allowed before planned sprint completion.",
                message,
                status="team_lead_final_handoff_not_ready",
            )

        updated = {**self.delivery_state}
        updated["handoff_scope"] = handoff_scope
        updated["handoff_sprint_id"] = sprint_id
        updated["handoff_output_dir"] = str(Path(contract_paths.html).parent)
        updated["handoff_expected_outputs"] = contract_paths.as_list()
        self.delivery_state = cast(DeliveryState, updated)
        return self._run_worker(
            tool="run_handoff",
            node_name="handoff",
            work_item_id=item_id,
            reason=reason,
            message=message,
            worker=self.workers.handoff,
        )

    def codex_review(
        self,
        work_item_id: str,
        target_agent: str = "",
        purpose: str = "",
        question: str = "",
        artifact_refs: str = "",
        intent: str = "review_feedback",
        reason: str = "",
        message: str = "",
    ) -> str:
        item_id = _clean_work_item_id(work_item_id)
        if error := self._contract_error("codex_review", item_id, reason or purpose, message):
            return error
        if limit_response := self._limit_response("codex_review", item_id, message or question):
            return limit_response
        started = time.perf_counter()
        item = get_work_item(str(self.delivery_state["run_id"]), item_id)
        refs = _split_artifact_refs(artifact_refs)
        self._transition(
            item_id,
            "codex_review",
            TEAM_LEAD_CODEX_REVIEW_AGENT_ID,
            "review",
            reason or purpose or "Review requested.",
        )
        review_request = CodexReviewRequest(
            run_id=self.delivery_state["run_id"],
            run_dir=Path(self.delivery_state["run_dir"]),
            requesting_agent=TEAM_LEAD_AGENT_ID,
            target_agent=target_agent or None,
            correlation_id=item.work_item_id,
            purpose=purpose or reason or "Review referenced delivery artifacts.",
            question=question
            or message
            or reason
            or "Review the referenced artifacts against the explicit work item.",
            artifact_refs=refs,
            execution_id=build_agent_execution_id(
                run_id=str(self.delivery_state["run_id"]),
                agent_id=TEAM_LEAD_AGENT_ID,
                correlation_id=item.work_item_id,
                intent="codex_review",
                message_id=question or message or reason or target_agent,
            ),
            codex_resume_thread_id=codex_resume_thread_id(
                self.delivery_state, TEAM_LEAD_CODEX_REVIEW_AGENT_ID
            ),
        )
        result = (self.codex_reviewer or CodexReviewRunner()).run(review_request)
        record_codex_thread(
            self.delivery_state, TEAM_LEAD_CODEX_REVIEW_AGENT_ID, result.codex_thread_id
        )
        sent_message = None
        if target_agent:
            sent_message = AgentMessageStore(self.delivery_state["run_dir"]).append(
                AgentMessage(
                    from_agent=TEAM_LEAD_AGENT_ID,
                    to_agent=target_agent,
                    intent=intent or "review_feedback",
                    content=result.content,
                    artifact_refs=refs,
                    correlation_id=item.work_item_id,
                    execution_id=result.execution_id or None,
                )
            )
        _register_team_lead_tool_artifacts(
            self.delivery_state,
            [
                result.summary_artifact,
                result.prompt_artifact,
                result.log_artifact,
                result.raw_events_artifact,
            ],
            artifact_type="review_output",
            source_tool="codex_review",
            work_item_id=item.work_item_id,
        )
        self._record(
            "codex_review",
            item.work_item_id,
            reason or purpose,
            message or question,
            result_status=result.status,
        )
        self._transition(
            item.work_item_id,
            "codex_review",
            TEAM_LEAD_AGENT_ID,
            "review",
            f"Review {result.status}.",
        )
        return self._tool_response(
            "codex_review",
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
                "to_agent": target_agent or None,
                "correlation_id": item.work_item_id,
                "execution_id": result.execution_id,
                "codex_thread_id": result.codex_thread_id,
            },
            duration_ms=int((time.perf_counter() - started) * 1000),
            work_item_id=item.work_item_id,
        )

    def inspect_sprint_status(
        self,
        work_item_id: str,
        reason: str = "",
        message: str = "",
    ) -> str:
        item_id = _clean_work_item_id(work_item_id)
        if error := self._contract_error("inspect_sprint_status", item_id, reason, message):
            return error
        if limit_response := self._limit_response("inspect_sprint_status", item_id, message):
            return limit_response
        started = time.perf_counter()
        item = get_work_item(str(self.delivery_state["run_id"]), item_id)
        refs = _sprint_status_artifact_refs(self.delivery_state, self.sprint_id)
        request = StatusInspectionRequest(
            run_id=self.delivery_state["run_id"],
            run_dir=Path(self.delivery_state["run_dir"]),
            requesting_agent=TEAM_LEAD_AGENT_ID,
            scope="sprint",
            purpose=reason or "Inspect explicit DB work items, blockers, and handoff readiness.",
            status_context=_sprint_status_context(self.delivery_state, self.sprint_id),
            artifact_refs=refs,
            correlation_id=item.work_item_id,
            model="gpt-5.3-codex",
            execution_id=build_agent_execution_id(
                run_id=str(self.delivery_state["run_id"]),
                agent_id=TEAM_LEAD_AGENT_ID,
                correlation_id=item.work_item_id,
                intent="inspect_sprint_status",
                message_id=message or reason or item.work_item_id,
            ),
            codex_resume_thread_id=codex_resume_thread_id(
                self.delivery_state,
                TEAM_LEAD_STATUS_INSPECTOR_AGENT_ID,
            ),
        )
        result = (self.status_inspector or StatusInspectorRunner()).run(request)
        self.delivery_state["last_sprint_status_inspection"] = result.payload
        self.delivery_state["last_sprint_status_inspection_artifacts"] = [
            ref
            for ref in [
                result.result_artifact,
                result.summary_artifact,
                result.prompt_artifact,
                result.log_artifact,
                result.raw_events_artifact,
            ]
            if ref
        ]
        _register_team_lead_tool_artifacts(
            self.delivery_state,
            [
                result.result_artifact,
                result.summary_artifact,
                result.prompt_artifact,
                result.log_artifact,
                result.raw_events_artifact,
            ],
            artifact_type="status_inspection",
            source_tool="inspect_sprint_status",
            work_item_id=item.work_item_id,
        )
        record_codex_thread(
            self.delivery_state,
            TEAM_LEAD_STATUS_INSPECTOR_AGENT_ID,
            result.codex_thread_id,
        )
        self._record(
            "inspect_sprint_status",
            item.work_item_id,
            reason,
            message,
            result_status=str(result.payload.get("sprint_status") or result.status),
        )
        self._transition(
            item.work_item_id,
            "inspect_sprint_status",
            TEAM_LEAD_AGENT_ID,
            "review",
            f"Sprint status inspection {result.status}.",
        )
        return self._tool_response(
            "inspect_sprint_status",
            f"Sprint status inspection {result.status}.",
            downstream_response={
                "from_agent": "codex-status-inspector",
                "intent": "inspect_sprint_status",
                "content": result.payload,
                "artifact_refs": [
                    result.result_artifact,
                    result.summary_artifact,
                    result.prompt_artifact,
                    result.log_artifact,
                    result.raw_events_artifact,
                ],
                "correlation_id": item.work_item_id,
                "execution_id": result.execution_id,
                "codex_thread_id": result.codex_thread_id,
            },
            duration_ms=int((time.perf_counter() - started) * 1000),
            work_item_id=item.work_item_id,
        )

    def complete_sprint(self, work_item_id: str, reason: str = "", message: str = "") -> str:
        item_id = _clean_work_item_id(work_item_id)
        if error := self._contract_error("complete_sprint", item_id, reason, message):
            return error
        completion = sprint_completion_state(str(self.delivery_state["run_id"]), self.sprint_id)
        if not completion.has_items:
            return self._contract_error_response(
                "complete_sprint",
                item_id,
                f"Sprint {self.sprint_id} has no DB work items.",
                message or reason,
                status="sprint_empty",
            )
        if not completion.is_complete:
            return self._contract_error_response(
                "complete_sprint",
                item_id,
                f"Sprint {self.sprint_id} still has pending or blocked DB work items.",
                message or reason,
                status="sprint_not_complete",
            )
        self.delivery_state = mark_node_completed(
            self.delivery_state,
            node_name="team_lead",
            stage="team_lead",
            status="team_lead_sprint_handoff_ready",
        )
        write_team_lead_event(
            self.delivery_state,
            "team_lead_complete_sprint_requested",
            {"reason": reason, "message": message, "work_item_id": item_id},
        )
        mark_sprint_done(str(self.delivery_state["run_id"]), self.sprint_id)
        final_status = (
            "done"
            if sprint_is_final(str(self.delivery_state["run_id"]), self.sprint_id)
            else "in_progress"
        )
        self._transition(
            item_id,
            "complete_sprint",
            TEAM_LEAD_AGENT_ID,
            final_status,
            reason or "Sprint completed.",
        )
        self._record("complete_sprint", item_id, reason, message)
        return self._tool_response(
            "complete_sprint",
            "Sprint completed.",
            artifact_refs=_team_lead_completion_artifact_refs(self.delivery_state, self.sprint_id),
            work_item_id=item_id,
        )

    def block_sprint(self, reason: str, work_item_id: str, message: str = "") -> str:
        item_id = _clean_work_item_id(work_item_id)
        if error := self._contract_error("block_sprint", item_id, reason, message):
            return error
        blockers = [*self.delivery_state.get("blockers", []), reason]
        self.delivery_state = cast(
            DeliveryState,
            {
                **self.delivery_state,
                "status": "team_lead_sprint_blocked",
                "blockers": blockers,
            },
        )
        write_team_lead_event(
            self.delivery_state,
            "team_lead_blocked_sprint",
            {"reason": reason, "message": message, "work_item_id": item_id},
        )
        mark_sprint_blocked(str(self.delivery_state["run_id"]), self.sprint_id)
        self._transition(item_id, "block_sprint", TEAM_LEAD_AGENT_ID, "blocked", reason)
        self._record("block_sprint", item_id, reason, message, result_status="blocked")
        return self._tool_response(
            "block_sprint",
            f"Sprint blocked: {reason}",
            status_override="blocked",
            work_item_id=item_id,
        )

    def result(self) -> TeamLeadExecutorResult:
        write_history_artifact(self.delivery_state, self.sprint_id, self.history or [])
        return TeamLeadExecutorResult(self.delivery_state, self.history or [])

    def tool_calls_made(self) -> bool:
        return (
            count_tool_call_events(str(self.delivery_state["run_id"]), agent_id=TEAM_LEAD_AGENT_ID)
            > self.initial_tool_call_count
        )

    def reached_terminal_state(self) -> bool:
        if self.delivery_state.get("blockers"):
            return True
        status = str(self.delivery_state.get("status") or "")
        sprint_state = sprint_completion_state(str(self.delivery_state["run_id"]), self.sprint_id)
        return status == "team_lead_sprint_handoff_ready" or sprint_state.status in {
            "done",
            "blocked",
        }

    def block_incomplete_execution(self) -> None:
        sprint_state = sprint_completion_state(str(self.delivery_state["run_id"]), self.sprint_id)
        next_item = (
            f" Next DB work item still pending: {sprint_state.next_work_item_id}."
            if sprint_state.next_work_item_id
            else ""
        )
        status = str(self.delivery_state.get("status") or "")
        self.block_sprint(
            reason=(
                "Team Lead AgentExecutor stopped before reaching a terminal sprint state."
                + next_item
                + f" Current status: {status or 'unknown'}."
            ),
            work_item_id="PLAN-04",
            message="Team Lead must continue explicit DB work-item routing or block with evidence.",
        )

    def _run_worker(
        self,
        *,
        tool: TeamLeadToolName,
        node_name: str,
        work_item_id: str,
        reason: str,
        message: str,
        worker: TeamLeadWorker,
    ) -> str:
        item_id = _clean_work_item_id(work_item_id)
        if error := self._contract_error(tool, item_id, reason, message):
            return error
        if limit_response := self._limit_response(tool, item_id, message):
            return limit_response
        started = time.perf_counter()
        item = get_work_item(str(self.delivery_state["run_id"]), item_id)
        target_agent = target_agent_id(node_name)
        self._transition(item.work_item_id, tool, target_agent, "in_progress", reason or message)
        outbound = append_agent_call_message(
            self.delivery_state,
            node_name=node_name,
            work_item_id=item.work_item_id,
            reason=reason,
            message=message,
        )
        write_request(
            self.delivery_state,
            kind=f"{tool}_request",
            target_agent=target_agent,
            work_item_id=item.work_item_id,
            payload={
                "sprint_id": item.sprint_id,
                "work_item_id": item.work_item_id,
                "work_item": item.to_dict(),
                "message": message,
                "reason": reason,
                "message_id": outbound.message_id,
                "message_intent": outbound.intent,
                "artifact_refs": outbound.artifact_refs,
            },
        )
        write_team_lead_event(
            self.delivery_state,
            "team_lead_worker_started",
            {"work_item_id": item.work_item_id, "node": node_name, "tool": tool},
        )
        checked = worker(self.delivery_state)
        self.delivery_state = checked
        status = str(checked.get("status") or checked.get("stage") or "")
        final_status = _status_for_tool_result(tool, status)
        self._transition(
            item.work_item_id,
            tool,
            target_agent,
            final_status,
            _worker_activity_message(tool, item.work_item_id, final_status),
        )
        downstream_response = latest_downstream_response(
            self.delivery_state,
            from_agent=target_agent,
            correlation_id=item.work_item_id,
        )
        self._record(tool, item.work_item_id, reason, message, result_status=status)
        write_team_lead_event(
            self.delivery_state,
            "team_lead_worker_completed",
            {"work_item_id": item.work_item_id, "node": node_name, "tool": tool, "status": status},
        )
        return self._tool_response(
            tool,
            f"{tool} completed with status {status or final_status}.",
            downstream_response=downstream_response,
            duration_ms=int((time.perf_counter() - started) * 1000),
            input_summary={
                "work_item_id": item.work_item_id,
                "sprint_id": item.sprint_id,
                "reason": reason,
                "message": message,
            },
            status_override=status or final_status,
            work_item_id=item.work_item_id,
        )

    def _contract_error(
        self,
        tool: str,
        work_item_id: str,
        reason: str,
        message: str,
    ) -> str:
        if work_item_id:
            try:
                get_work_item(str(self.delivery_state["run_id"]), work_item_id)
            except ValueError as exc:
                return self._contract_error_response(tool, work_item_id, str(exc), message)
            return ""
        return self._contract_error_response(
            tool,
            "",
            "work_item_id is required; target/feature/active item Repair is disabled.",
            message or reason,
        )

    def _contract_error_response(
        self,
        tool: str,
        work_item_id: str,
        reason: str,
        message: str,
        *,
        status: str = "contract_error",
    ) -> str:
        self._record(tool, work_item_id, reason, message, result_status=status)
        return self._tool_response(
            tool,
            f"{tool} contract error: {reason}",
            status_override=status,
            work_item_id=work_item_id,
        )

    def _limit_response(self, tool: str, work_item_id: str, message: str) -> str | None:
        if self.max_steps <= 0:
            return self._tool_response(
                tool,
                "Team Lead tool limit reached before this action.",
                status_override="blocked",
                work_item_id=work_item_id,
            )
        self.max_steps -= 1
        return None

    def _record(
        self,
        tool: str,
        work_item_id: str,
        reason: str,
        message: str,
        *,
        result_status: str = "",
    ) -> None:
        history = self.history if self.history is not None else []
        self.history = history
        step = len(history) + 1
        decision = TeamLeadDecision(
            cast(TeamLeadToolName, tool),
            reason or "No reason provided.",
            work_item_id,
            message,
        )
        artifact = write_decision_artifact(self.delivery_state, step, decision)
        history_entry = {
            **decision.to_dict(),
            "step": step,
            "artifact": artifact,
            "result_status": result_status,
            "work_item_id": work_item_id,
        }
        history.append(history_entry)
        write_history_artifact(self.delivery_state, self.sprint_id, history)
        write_team_lead_event(
            self.delivery_state,
            "team_lead_decision",
            {"step": step, "decision": decision.to_dict(), "artifact": artifact},
        )

    def _transition(
        self,
        work_item_id: str,
        tool: str,
        owner_agent: str,
        status: str,
        message: str,
    ) -> None:
        item = get_work_item(str(self.delivery_state["run_id"]), work_item_id)
        record = ToolExecutionRecord(
            run_id=str(self.delivery_state["run_id"]),
            work_item_id=item.work_item_id,
            sprint_id=item.sprint_id,
            owner_agent=owner_agent,
            tool_name=tool,
            tool_call_id=_tool_call_id(self.delivery_state, tool, len(self.history or []) + 1),
            attempt_id="1",
            status=status,
            activity_message=message or f"{tool} updated {item.work_item_id}.",
        )
        record_work_item_transition(record)

    def _tool_response(
        self,
        tool_name: str,
        business_summary: str,
        *,
        downstream_response: dict[str, Any] | None = None,
        artifact_refs: list[str] | None = None,
        duration_ms: int | None = None,
        input_summary: dict[str, Any] | None = None,
        status_override: str | None = None,
        work_item_id: str = "",
    ) -> str:
        status = status_override or str(self.delivery_state.get("status") or "running")
        refs = _response_artifact_refs(
            artifact_refs=artifact_refs,
            downstream_response=downstream_response,
        )
        result = ToolCallResult(
            tool_name=tool_name,
            tool_call_id=_tool_call_id(
                self.delivery_state,
                tool_name,
                len(self.history or []),
            ),
            status=status,
            business_summary=business_summary,
            developer_diagnostics={
                "sprint_id": self.sprint_id,
                "downstream_response": downstream_response,
            },
            output_artifacts=_tool_artifact_refs(self.delivery_state, refs),
            failure_mode=failure_mode_from_status(status, self.delivery_state.get("blockers", [])),
            recommended_next_action=_recommended_next_action(
                status,
                failure_mode_from_status(status, self.delivery_state.get("blockers", [])),
            ),
            dashboard_update=ToolDashboardUpdate(
                status=dashboard_status_from_runtime_status(status),
                summary=business_summary,
                comment=business_summary,
                artifact_links=_tool_artifact_refs(self.delivery_state, refs),
            ),
        )
        record_tool_call_event(
            Path(self.delivery_state["run_dir"]),
            run_id=self.delivery_state["run_id"],
            agent_id=TEAM_LEAD_AGENT_ID,
            tool_name=result.tool_name,
            tool_call_id=result.tool_call_id,
            status=status,
            work_item_id=work_item_id or None,
            input_summary=input_summary or {"work_item_id": work_item_id},
            output_summary=result.to_dict(),
            duration_ms=duration_ms,
            failure_mode=result.failure_mode,
        )
        checkpoint_delivery_state(self.delivery_state)
        return result.to_json()


def apply_team_lead_result(state: DeliveryState, sprint_id: str) -> DeliveryState:
    """Persist the Team Lead sprint result from DB-backed state."""

    status = str(state.get("status") or "")
    blocked = "blocked" in status.lower() or bool(state.get("blockers"))
    if not status:
        status = "team_lead_sprint_blocked" if blocked else "team_lead_sprint_handoff_ready"
    result = {
        "sprint_id": sprint_id,
        "status": status,
        "completed_work_item_ids": completed_work_item_ids(str(state["run_id"]), sprint_id),
        "blockers": list(state.get("blockers", [])),
        "handoff_status": state.get("handoff_status"),
        "deployment_status": state.get("deployment_status"),
        "artifact_refs": _team_lead_completion_artifact_refs(state, sprint_id),
    }
    relative_path = f"team-lead/{sprint_id}-result.json"
    write_json_artifact(state, relative_path, result)
    _register_team_lead_artifact(
        state,
        relative_path,
        artifact_type="team_lead_result",
        visibility="developer",
        source_tool="apply_team_lead_result",
        work_item_id="PLAN-04",
    )
    write_team_lead_event(state, "team_lead_sprint_completed", result)
    _append_team_lead_response_to_head(
        state,
        result,
        _team_lead_completion_artifact_refs(state, sprint_id),
    )
    return mark_node_completed(state, node_name="team_lead", stage="team_lead", status=status)


def checkpoint_delivery_state(state: DeliveryState) -> None:
    write_delivery_state(state)


def write_json_artifact(state: DeliveryState, relative_path: str, payload: dict[str, Any]) -> Path:
    path = Path(state["run_dir"]) / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_team_lead_event(state: DeliveryState, event: str, data: dict[str, Any]) -> None:
    write_event(Path(state["run_dir"]), state["run_id"], TEAM_LEAD_AGENT_ID, event, data)


def write_history_artifact(
    state: DeliveryState,
    sprint_id: str,
    history: list[dict[str, Any]],
) -> None:
    relative_path = f"team-lead/{sprint_id}-history.json"
    write_json_artifact(state, relative_path, {"steps": history})
    _register_team_lead_artifact(
        state,
        relative_path,
        artifact_type="debug_trace",
        visibility="developer",
        source_tool="team_lead_history",
        work_item_id="PLAN-04",
    )


def write_sprint_plan_artifact(
    state: DeliveryState,
    sprint_id: str,
    sprint: dict[str, Any],
) -> None:
    relative_path = f"team-lead/{sprint_id}-plan.json"
    write_json_artifact(state, relative_path, sprint)
    _register_team_lead_artifact(
        state,
        relative_path,
        artifact_type="team_lead_plan",
        visibility="developer",
        source_tool="prepare_sprint",
        work_item_id="PLAN-04",
    )


def write_decision_artifact(state: DeliveryState, step: int, decision: TeamLeadDecision) -> str:
    relative_path = f"team-lead/decisions/{step:03d}-{decision.tool}.json"
    write_json_artifact(state, relative_path, decision.to_dict())
    _register_team_lead_artifact(
        state,
        relative_path,
        artifact_type="debug_trace",
        visibility="internal",
        source_tool=decision.tool,
        work_item_id=decision.work_item_id or "PLAN-04",
    )
    return relative_path


def write_request(
    state: DeliveryState,
    *,
    kind: str,
    target_agent: str,
    work_item_id: str,
    payload: dict[str, Any],
) -> Path:
    run_dir = Path(state["run_dir"])
    request_dir = run_dir / "team-lead" / "requests"
    request_dir.mkdir(parents=True, exist_ok=True)
    execution_id = str(payload.get("execution_id") or state.get("agent_execution_id") or "")
    suffix = f"-{short_hash(execution_id)}" if execution_id else ""
    path = request_dir / f"{kind}-{work_item_id}{suffix}.json"
    body = {
        "kind": kind,
        "source_agent": TEAM_LEAD_AGENT_ID,
        "target_agent": target_agent,
        "run_id": state["run_id"],
        "stage": state["stage"],
        "status": state["status"],
        "work_item_id": work_item_id,
        **payload,
    }
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _register_team_lead_artifact(
        state,
        path.relative_to(run_dir).as_posix(),
        artifact_type="tool_request",
        visibility="internal",
        source_tool="team_lead_request",
        work_item_id=work_item_id,
    )
    return path


def append_agent_call_message(
    state: DeliveryState,
    *,
    node_name: str,
    work_item_id: str,
    reason: str,
    message: str,
) -> AgentMessage:
    target_agent = target_agent_id(node_name)
    intent = agent_message_intent(node_name)
    content = _agent_call_message(
        state=state,
        node_name=node_name,
        work_item_id=work_item_id,
        message=message,
        reason=reason,
    )
    message_id = f"msg-{uuid4().hex}"
    execution_id = build_agent_execution_id(
        run_id=str(state["run_id"]),
        agent_id=target_agent,
        correlation_id=work_item_id,
        intent=intent,
        message_id=message_id,
    )
    outbound = AgentMessageStore(state["run_dir"]).append(
        AgentMessage(
            from_agent=TEAM_LEAD_AGENT_ID,
            to_agent=target_agent,
            intent=intent,
            content=content,
            artifact_refs=_agent_call_artifacts(node_name, state),
            message_id=message_id,
            correlation_id=work_item_id,
            execution_id=execution_id,
        )
    )
    state["agent_call_message_id"] = outbound.message_id
    state["agent_call_correlation_id"] = work_item_id
    state["agent_execution_id"] = execution_id
    state["agent_execution_agent_id"] = target_agent
    return outbound


def latest_downstream_response(
    state: DeliveryState,
    *,
    from_agent: str,
    correlation_id: str,
) -> dict[str, Any] | None:
    messages = AgentMessageStore(state["run_dir"]).read(
        from_agent=from_agent,
        to_agent=TEAM_LEAD_AGENT_ID,
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


def next_work_item_for_state(
    state: DeliveryState,
    sprint_id: str | None = None,
) -> dict[str, Any] | None:
    """Return the next DB work item for the current sprint."""

    item = next_work_item(str(state["run_id"]), sprint_id or str(state.get("team_lead_sprint_id")))
    return item.to_dict() if item else None


def work_items_not_qa_passed(state: DeliveryState, sprint_id: str | None = None) -> list[str]:
    item = next_work_item(str(state["run_id"]), sprint_id or str(state.get("team_lead_sprint_id")))
    return [item.work_item_id] if item else []


def target_agent_id(node_name: str) -> str:
    return route_for_node(node_name)[0]


def agent_message_intent(node_name: str) -> str:
    return route_for_node(node_name)[1]


def _clean_work_item_id(work_item_id: str | None) -> str:
    return str(work_item_id or "").strip()


def _status_for_tool_result(tool: str, runtime_status: str) -> str:
    normalized = runtime_status.lower()
    if any(token in normalized for token in ("failed", "blocked", "repair", "error")):
        return "blocked"
    if tool in {"run_qa", "run_deployment", "run_handoff", "run_post_deploy_qa"}:
        return "done"
    return "review"


def _worker_activity_message(tool: str, work_item_id: str, status: str) -> str:
    if status == "blocked":
        return f"{tool} reported a blocker for {work_item_id}."
    if status == "done":
        return f"{tool} completed {work_item_id}."
    return f"{tool} moved {work_item_id} into review."


def _agent_call_message(
    *,
    state: DeliveryState,
    node_name: str,
    work_item_id: str,
    message: str,
    reason: str,
) -> str:
    item = get_work_item(str(state["run_id"]), work_item_id)
    content = message.strip() or reason.strip()
    coordinator_note = content or "Please handle the delegated agent task and report the result."
    packet = {
        "work_item_id": item.work_item_id,
        "title": item.title,
        "sprint_id": item.sprint_id,
        "delivery_order": item.delivery_order,
        "owner_agent": item.owner_agent,
        "source_refs": item.source_refs,
        "current_status": item.status,
    }
    return (
        f"{coordinator_note}\n\n"
        "Canonical DB work item packet:\n"
        f"{json.dumps(packet, indent=2, sort_keys=True)}\n\n"
        "Contract precedence:\n"
        "- Treat the DB packet and explicitly cited artifacts as the source of truth.\n"
        "- Do not repair work identity from target text, active feature state, filenames, "
        "or artifact paths.\n"
        f"- This request is for `{node_name}` ownership of work item `{work_item_id}`."
    )


def _agent_call_artifacts(node_name: str, state: DeliveryState) -> list[str]:
    base = _upstream_planning_artifacts(state)
    if node_name == "fullstack":
        return base
    if node_name == "qa":
        return base
    if node_name == "deployment":
        return base
    if node_name == "handoff":
        return base
    return base


def _upstream_planning_artifacts(state: DeliveryState) -> list[str]:
    return _unique_paths(
        [
            "00-requirements.md",
            "upstream-planning/business-analysis.json",
            "upstream-planning/architecture.json",
            "upstream-planning/project-management/release-plan.json",
            "upstream-planning/project-management/planned-work-items.json",
        ]
    )


def _sprint_status_context(state: DeliveryState, sprint_id: str) -> dict[str, Any]:
    completion = sprint_completion_state(str(state["run_id"]), sprint_id)
    return {
        "run_id": state.get("run_id"),
        "sprint_id": sprint_id,
        "status": state.get("status"),
        "sprint_db_state": completion.to_dict(),
        "work_items": [
            item.to_dict()
            for item in list_sprint_work_items(str(state["run_id"]), sprint_id)
        ],
        "next_work_item": next_work_item_for_state(state, sprint_id),
        "completed_work_item_ids": completed_work_item_ids(str(state["run_id"]), sprint_id),
        "blockers": list(state.get("blockers", [])),
    }


def _sprint_status_artifact_refs(state: DeliveryState, sprint_id: str) -> list[str]:
    run_dir = Path(state["run_dir"])
    refs = [
        "00-requirements.md",
        "upstream-planning/project-management/release-plan.json",
        "upstream-planning/project-management/planned-work-items.json",
        f"team-lead/{sprint_id}-result.json",
    ]
    return _unique_paths([ref for ref in refs if (run_dir / ref).exists()])


def _final_project_report_allowed(state: DeliveryState, sprint_id: str) -> bool:
    return sprint_is_final(str(state["run_id"]), sprint_id)


def _team_lead_completion_artifact_refs(state: DeliveryState, sprint_id: str) -> list[str]:
    return _unique_paths(_latest_handoff_artifact_refs(state))


def _latest_handoff_artifact_refs(state: DeliveryState) -> list[str]:
    return artifact_paths_by_type(str(state["run_id"]), {"handoff", "release_report"})


def _append_team_lead_response_to_head(
    state: DeliveryState,
    result: dict[str, Any],
    artifact_refs: list[str],
) -> None:
    messages = AgentMessageStore(state["run_dir"]).read(
        from_agent="head-agent",
        to_agent=TEAM_LEAD_AGENT_ID,
        intent="request_sprint_delivery",
        limit=1,
    )
    parent = messages[-1] if messages else None
    append_agent_response(
        state["run_dir"],
        from_agent=TEAM_LEAD_AGENT_ID,
        to_agent=parent.from_agent if parent else "head-agent",
        status=str(result.get("status") or state.get("status") or ""),
        content=json.dumps(result, indent=2, sort_keys=True),
        artifact_refs=_unique_paths(artifact_refs),
        correlation_id=parent.correlation_id if parent else str(state.get("team_lead_sprint_id")),
        parent_message_id=parent.message_id if parent else None,
        execution_id=str(state.get("agent_execution_id") or "") or None,
    )


def _response_artifact_refs(
    *,
    artifact_refs: list[str] | None,
    downstream_response: dict[str, Any] | None,
) -> list[str]:
    refs: list[str] = []
    if artifact_refs:
        refs.extend(artifact_refs)
    if downstream_response:
        refs.extend(
            str(ref)
            for ref in downstream_response.get("artifact_refs", [])
            if isinstance(ref, str) and ref
        )
    return _unique_paths(refs)


def _tool_artifact_refs(state: DeliveryState, paths: list[str]) -> tuple[Any, ...]:
    return artifact_links_for_paths(str(state["run_id"]), _unique_paths(paths))


def _tool_call_id(state: DeliveryState, tool_name: str, step: int) -> str:
    run_id = str(state.get("run_id") or "run")
    return f"{run_id}:team-lead-agent:{tool_name}:{step}"


def _recommended_next_action(status: str, failure_mode: str | None) -> str:
    if failure_mode == "needs_repair":
        return "Route the findings to the owning specialist, then rerun the relevant check."
    if failure_mode:
        return "Inspect diagnostics and blocker evidence before retrying or escalating."
    if dashboard_status_from_runtime_status(status) == "done":
        return "Inspect sprint status and proceed to the next required gate."
    return "Inspect sprint status before choosing the next tool."


def _split_artifact_refs(value: str) -> list[str]:
    refs: list[str] = []
    for raw in (value or "").replace(";", "\n").replace(",", "\n").splitlines():
        item = raw.strip()
        if item and item not in refs:
            refs.append(item)
    return refs


def _register_team_lead_artifact(
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
            owner_agent=TEAM_LEAD_AGENT_ID,
            source_tool=source_tool,
            label=Path(relative_path).name,
            relative_path=relative_path,
            run_id=str(state["run_id"]),
            work_item_id=work_item_id,
            task_scoped=True,
        ),
    )


def _register_team_lead_tool_artifacts(
    state: DeliveryState,
    relative_paths: list[str],
    *,
    artifact_type: str,
    source_tool: str,
    work_item_id: str,
) -> None:
    for relative_path in _unique_paths([path for path in relative_paths if path]):
        _register_team_lead_artifact(
            state,
            relative_path,
            artifact_type=artifact_type,
            visibility="internal",
            source_tool=source_tool,
            work_item_id=work_item_id,
        )


def _unique_paths(paths: list[str]) -> list[str]:
    unique: list[str] = []
    for path in paths:
        if path and path not in unique:
            unique.append(path)
    return unique
