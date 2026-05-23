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
from agentic_company.platform.agent_runtime import agent_env_value
from agentic_company.platform.artifact_registry import register_artifact
from agentic_company.platform.artifacts import artifact_ref
from agentic_company.platform.codex_review import (
    CodexReviewRequest,
    CodexReviewResult,
    CodexReviewRunner,
)
from agentic_company.platform.events import write_event
from agentic_company.platform.executions import build_agent_execution_id, short_hash
from agentic_company.platform.messages import AgentMessage, AgentMessageStore
from agentic_company.platform.run_trace import record_tool_call_event
from agentic_company.platform.sprints import (
    HEAD_WORK_ITEM_BY_NODE,
    seed_head_work_board,
    set_work_item_status,
    sync_work_board,
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
    ToolCallResult,
    ToolDashboardUpdate,
    artifact_refs_from_paths,
    dashboard_status_from_runtime_status,
    failure_mode_from_status,
)

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

    def __post_init__(self) -> None:
        if self.history is None:
            self.history = []
        if not self.delivery_state.get("feature_queue"):
            self.delivery_state = seed_head_work_board(self.delivery_state)

    def run_business_analyst(
        self,
        target: str | None = None,
        reason: str = "",
        message: str = "",
    ) -> str:
        return self._run_worker(
            tool="run_business_analyst",
            node_name="business_analyst",
            target=target or "requirements",
            reason=reason,
            message=message,
            worker=self.workers.business_analyst,
        )

    def run_architect(
        self,
        target: str | None = None,
        reason: str = "",
        message: str = "",
    ) -> str:
        return self._run_worker(
            tool="run_architect",
            node_name="architecture",
            target=target or "architecture",
            reason=reason,
            message=message,
            worker=self.workers.architect,
        )

    def run_project_manager(
        self,
        target: str | None = None,
        reason: str = "",
        message: str = "",
    ) -> str:
        return self._run_worker(
            tool="run_project_manager",
            node_name="project_management",
            target=target or "project-management",
            reason=reason,
            message=message,
            worker=self.workers.project_manager,
        )

    def run_team_lead(
        self,
        target: str | None = None,
        reason: str = "",
        message: str = "",
    ) -> str:
        promoted = promote_candidate_feature_queue(self.delivery_state)
        if not promoted.get("feature_queue"):
            self.delivery_state = promoted
            self._record(
                "run_team_lead",
                target or "sprint-delivery",
                reason or "Team Lead requested before Project Manager produced a feature queue.",
                message,
                result_status="head_waiting_for_feature_queue",
            )
            return self._tool_response(
                "Team Lead was not started because no candidate feature queue is available."
            )
        self.delivery_state = promoted
        if not target:
            self._record(
                "run_team_lead",
                "missing-sprint-target",
                reason or "Team Lead requested without an explicit PM sprint target.",
                message,
                result_status="head_waiting_for_explicit_sprint_target",
            )
            return self._tool_response(
                "Team Lead was not started because run_team_lead requires an explicit "
                "target sprint id from the PM artifacts, for example target='sprint-02'."
            )
        return self._run_worker(
            tool="run_team_lead",
            node_name="team_lead",
            target=target,
            reason=reason,
            message=message,
            worker=self.workers.team_lead,
        )

    def complete_delivery(
        self,
        target: str | None = None,
        reason: str = "",
        message: str = "",
    ) -> str:
        started = time.perf_counter()
        if limit_response := self._limit_response(message):
            return limit_response
        promoted = promote_candidate_feature_queue(self.delivery_state)
        self.delivery_state = mark_node_completed(
            promoted,
            node_name="head",
            stage="head",
            status="head_delivery_completed",
        )
        write_head_event(
            self.delivery_state,
            "head_delivery_completed",
            {"reason": reason, "message": message},
        )
        self._record("complete_delivery", target or "company-delivery", reason, message)
        return self._tool_response(
            "Head Agent completed BA -> Architect -> PM -> Team Lead.",
            duration_ms=_duration_ms(started),
            input_summary={
                "target": target or "company-delivery",
                "reason": reason,
                "message": message,
            },
        )

    def inspect_delivery_status(
        self,
        target: str | None = None,
        reason: str = "",
        message: str = "",
    ) -> str:
        started = time.perf_counter()
        if limit_response := self._limit_response(message):
            return limit_response
        promoted = promote_candidate_feature_queue(self.delivery_state)
        refs = _delivery_status_artifact_refs(promoted)
        inspection_request = StatusInspectionRequest(
            run_id=promoted["run_id"],
            run_dir=Path(promoted["run_dir"]),
            requesting_agent=HEAD_AGENT_ID,
            scope="delivery",
            purpose=(
                reason
                or "Inspect all PM-planned sprints, worker calls, gates, evidence, blockers, "
                "and completion readiness. Do not choose coordinator routing."
            ),
            status_context=_delivery_status_context(promoted, self.history or []),
            artifact_refs=refs,
            correlation_id=target or "company-delivery",
            model=agent_env_value("HEAD_STATUS_INSPECTOR_CODEX_MODEL", promoted)
            or agent_env_value("AGENT_CODEX_MODEL", promoted)
            or "gpt-5.3-codex",
            execution_id=build_agent_execution_id(
                run_id=str(promoted["run_id"]),
                agent_id=HEAD_AGENT_ID,
                target=target or "company-delivery",
                intent="inspect_delivery_status",
                message_id=message or reason or target or "delivery-status",
            ),
            codex_resume_thread_id=codex_resume_thread_id(promoted, HEAD_STATUS_INSPECTOR_AGENT_ID),
        )
        inspector = self.status_inspector or StatusInspectorRunner()
        result = inspector.run(inspection_request)
        self.delivery_state = promoted
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
        self._record(
            "inspect_delivery_status",
            target or "company-delivery",
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
                    *result.artifact_refs,
                    result.result_artifact,
                    result.summary_artifact,
                    result.prompt_artifact,
                    result.log_artifact,
                    result.raw_events_artifact,
                ],
                "correlation_id": target or "company-delivery",
                "execution_id": result.execution_id,
                "codex_thread_id": result.codex_thread_id,
            },
            duration_ms=_duration_ms(started),
            input_summary={
                "target": target or "company-delivery",
                "reason": reason,
                "message": message,
                "artifact_refs": refs,
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
        target: str | None = None,
        reason: str = "",
        message: str = "",
    ) -> str:
        started = time.perf_counter()
        if limit_response := self._limit_response(message or question):
            return limit_response
        review_item_id = _review_work_item_id(self.delivery_state, target)
        if review_item_id:
            self.delivery_state = set_work_item_status(
                self.delivery_state,
                review_item_id,
                "review",
                active=True,
                sprint_id=str(self.delivery_state.get("team_lead_sprint_id") or ""),
            )
            checkpoint_delivery_state(self.delivery_state)
        refs = _split_artifact_refs(artifact_refs)
        review_request = CodexReviewRequest(
            run_id=self.delivery_state["run_id"],
            run_dir=Path(self.delivery_state["run_dir"]),
            requesting_agent=HEAD_AGENT_ID,
            target_agent=target_agent or None,
            correlation_id=target or "upstream-planning",
            purpose=purpose or reason or "Review referenced planning artifacts.",
            question=question
            or message
            or reason
            or "Review the referenced artifacts according to the requesting agent's purpose.",
            artifact_refs=refs,
            execution_id=build_agent_execution_id(
                run_id=str(self.delivery_state["run_id"]),
                agent_id=HEAD_AGENT_ID,
                target=target or "upstream-planning",
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
                    correlation_id=target or "upstream-planning",
                    execution_id=result.execution_id or None,
                )
            )
        self._record("codex_review", target_agent or target or "upstream-planning", reason, message)
        return self._tool_response(
            f"Codex review {result.status}.",
            downstream_response={
                "from_agent": "codex-review",
                "intent": "codex_review",
                "content": result.content,
                "artifact_refs": [
                    *refs,
                    result.summary_artifact,
                    result.prompt_artifact,
                    result.log_artifact,
                    result.raw_events_artifact,
                ],
                "message_id": sent_message.message_id if sent_message else None,
                "to_agent": known_target_agent or None,
                "correlation_id": target or "upstream-planning",
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
                "target": target or "upstream-planning",
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
        target: str | None = None,
        message: str = "",
    ) -> str:
        started = time.perf_counter()
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
            HeadDecision("block_planning", reason, target, message).to_dict(),
        )
        self._record("block_planning", target or "upstream-planning", reason, message)
        return self._tool_response(
            "Planning blocked: " + reason,
            duration_ms=_duration_ms(started),
            input_summary={
                "target": target or "upstream-planning",
                "reason": reason,
                "message": message,
            },
        )

    def result(self) -> HeadExecutorResult:
        write_history_artifact(self.delivery_state, self.history or [])
        checkpoint_delivery_state(self.delivery_state)
        return HeadExecutorResult(self.delivery_state, list(self.history or []))

    def _run_worker(
        self,
        *,
        tool: HeadToolName,
        node_name: str,
        target: str,
        reason: str,
        message: str,
        worker: HeadWorker,
    ) -> str:
        started = time.perf_counter()
        if limit_response := self._limit_response(message):
            return limit_response
        updated = {**self.delivery_state}
        if node_name == "team_lead":
            updated["team_lead_sprint_id"] = target
        item_id = HEAD_WORK_ITEM_BY_NODE.get(node_name, target)
        track_head_item = node_name != "team_lead" or not updated.get("feature_queue")
        outbound = append_agent_call_message(
            updated,
            node_name=node_name,
            target=target,
            reason=reason,
            message=message,
        )
        execution_id = outbound.execution_id or ""
        updated["agent_execution_id"] = execution_id
        updated["agent_execution_intent"] = outbound.intent
        updated["agent_execution_agent_id"] = outbound.to_agent
        if track_head_item:
            self.delivery_state = set_work_item_status(
                cast(DeliveryState, updated),
                item_id,
                "in_progress",
                active=True,
                sprint_id=str(updated.get("team_lead_sprint_id") or ""),
            )
        else:
            self.delivery_state = sync_work_board(
                cast(DeliveryState, updated),
                sprint_id=str(updated.get("team_lead_sprint_id") or ""),
            )
        write_request(
            self.delivery_state,
            kind=f"{node_name}_request",
            target_agent=outbound.to_agent,
            payload={
                "target": target,
                "message": message,
                "message_id": outbound.message_id,
                "message_intent": outbound.intent,
                "execution_id": execution_id,
                "artifact_refs": outbound.artifact_refs,
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
        if track_head_item:
            item_status = "blocked" if self.delivery_state.get("blockers") else "done"
            self.delivery_state = set_work_item_status(
                self.delivery_state,
                item_id,
                item_status,
                active=False,
                sprint_id=str(self.delivery_state.get("team_lead_sprint_id") or ""),
            )
        else:
            self.delivery_state = sync_work_board(
                self.delivery_state,
                sprint_id=str(self.delivery_state.get("team_lead_sprint_id") or ""),
            )
        downstream_response = latest_downstream_response(
            self.delivery_state,
            from_agent=outbound.to_agent,
            correlation_id=target,
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
        self._record(tool, target, reason, message)
        return self._tool_response(
            f"{tool} completed with status {self.delivery_state.get('status')}.",
            downstream_response=downstream_response,
            duration_ms=_duration_ms(started),
            input_summary={
                "tool": tool,
                "node_name": node_name,
                "target": target,
                "reason": reason,
                "message": message,
                "execution_id": execution_id,
                "target_agent": outbound.to_agent,
            },
        )

    def _record(
        self,
        tool: HeadToolName,
        target: str | None,
        reason: str,
        message: str,
        *,
        result_status: str | None = None,
    ) -> None:
        step = len(self.history or []) + 1
        decision = HeadDecision(tool, reason or "No reason provided.", target, message)
        decision_path = write_decision_artifact(self.delivery_state, step, decision)
        write_head_event(
            self.delivery_state,
            "head_decision",
            {"step": step, "decision": decision.to_dict(), "artifact": decision_path},
        )
        status = result_status or str(self.delivery_state.get("status") or "")
        history = self.history or []
        history.append(
            {
                "step": step,
                "tool": tool,
                "target": target,
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
        tool_name = _latest_history_tool(self.history or [])
        failure_mode = failure_mode_from_status(status, self.delivery_state.get("blockers", []))
        dashboard_status = dashboard_status_from_runtime_status(status)
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
                "active_feature_id": self.delivery_state.get("active_feature_id"),
            },
            output_artifacts=artifact_refs_from_paths(output_artifacts),
            failure_mode=failure_mode,
            recommended_next_action=_recommended_next_action(status, failure_mode),
            dashboard_update=ToolDashboardUpdate(
                status=dashboard_status,
                summary=message,
                comment=message,
                artifact_links=artifact_refs_from_paths(output_artifacts),
                labels=(failure_mode,) if failure_mode else (),
            ),
            implicit_resolution_warnings=(),
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
    target: str,
    reason: str,
    message: str,
) -> AgentMessage:
    target_agent = target_agent_id(node_name)
    intent = agent_message_intent(node_name)
    content = _agent_call_message(message=message, reason=reason)
    message_id = f"msg-{uuid4().hex}"
    execution_id = build_agent_execution_id(
        run_id=str(state["run_id"]),
        agent_id=target_agent,
        target=target,
        intent=intent,
        message_id=message_id,
    )
    outbound = AgentMessageStore(state["run_dir"]).append(
        AgentMessage(
            from_agent=HEAD_AGENT_ID,
            to_agent=target_agent,
            intent=intent,
            content=content,
            artifact_refs=_agent_call_artifacts(node_name, state),
            message_id=message_id,
            correlation_id=target,
            execution_id=execution_id,
        )
    )
    state["agent_call_message_id"] = outbound.message_id
    state["agent_call_correlation_id"] = target
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
    promoted = promote_candidate_feature_queue(state)
    feature_queue = list(promoted.get("feature_queue", []))
    feature_statuses = dict(promoted.get("feature_statuses", {}))
    completed = {str(feature_id) for feature_id in promoted.get("completed_feature_ids", [])}
    sprints: dict[str, dict[str, Any]] = {}
    for feature in feature_queue:
        if not isinstance(feature, dict):
            continue
        sprint_id = str(feature.get("sprint_id") or "sprint-01")
        feature_id = str(feature.get("id") or feature.get("feature_id") or "")
        if not feature_id:
            continue
        status = str(feature_statuses.get(feature_id) or feature.get("status") or "pending")
        if feature_id in completed and status == "pending":
            status = "done"
        sprint = sprints.setdefault(
            sprint_id,
            {"id": sprint_id, "features": [], "done_features": [], "pending_features": []},
        )
        item = {
            "id": feature_id,
            "title": feature.get("title"),
            "status": status,
            "owner_agent": feature.get("suggested_owner_agent"),
            "delivery_order": feature.get("delivery_order"),
        }
        sprint["features"].append(item)
        if feature_id in completed or status in {"qa_passed", "done", "deployed", "handoff_ready"}:
            sprint["done_features"].append(feature_id)
        else:
            sprint["pending_features"].append(feature_id)

    return {
        "run_id": promoted.get("run_id"),
        "stage": promoted.get("stage"),
        "status": promoted.get("status"),
        "team_lead_sprint_id": promoted.get("team_lead_sprint_id"),
        "active_feature_id": promoted.get("active_feature_id"),
        "completed_nodes": promoted.get("completed_nodes", []),
        "completed_feature_ids": promoted.get("completed_feature_ids", []),
        "feature_statuses": feature_statuses,
        "qa_status": promoted.get("qa_status"),
        "deployment_status": promoted.get("deployment_status"),
        "post_deploy_qa_status": promoted.get("post_deploy_qa_status"),
        "public_url": promoted.get("public_url"),
        "public_urls": promoted.get("public_urls", []),
        "blockers": promoted.get("blockers", []),
        "pending_feature_ids": _pending_feature_ids(promoted),
        "sprints": list(sprints.values()),
        "head_history": history,
        "team_lead_results": _json_artifacts_under(promoted, "team-lead/*-result.json"),
        "team_lead_histories": _json_artifacts_under(promoted, "team-lead/*-history.json"),
        "messages": _recent_messages(promoted, limit=30),
        "artifact_refs": _delivery_status_artifact_refs(promoted),
        "status_rules": {
            "team_lead_sprint_handoff_ready": (
                "Sprint-level handoff evidence only; Head still needs inspector confirmation "
                "before company delivery can complete."
            ),
            "can_complete_delivery": (
                "true only when every PM-planned feature, sprint handoff, and required final "
                "delivery gate is done or explicitly out of scope."
            ),
        },
    }


def _delivery_status_artifact_refs(state: DeliveryState) -> list[str]:
    run_dir = Path(state["run_dir"])
    discovered = [
        "00-requirements.md",
        "head/planning-history.json",
        "upstream-planning/project-management/release-plan.md",
        "upstream-planning/project-management/release-plan.json",
        "upstream-planning/project-management/candidate-feature-queue.json",
        "upstream-planning/project-management/roadmap.csv",
        "upstream-planning/project-management/risks-and-dependencies.md",
    ]
    for pattern in [
        "upstream-planning/project-management/sprint-*.json",
        "upstream-planning/project-management/sprint-*.md",
        "team-lead/*-history.json",
        "team-lead/*-result.json",
        "handoff/**/*.md",
        "handoff/**/*.json",
        "deployment/**/*.json",
        "qa/**/*.json",
    ]:
        discovered.extend(path.relative_to(run_dir).as_posix() for path in run_dir.glob(pattern))
    discovered.extend(str(artifact.get("path")) for artifact in state.get("artifacts", []))
    return _unique_paths([path for path in discovered if path and (run_dir / path).exists()])


def _json_artifacts_under(state: DeliveryState, pattern: str) -> list[dict[str, Any]]:
    run_dir = Path(state["run_dir"])
    payloads: list[dict[str, Any]] = []
    for path in sorted(run_dir.glob(pattern)):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payloads.append(
                {
                    "path": path.relative_to(run_dir).as_posix(),
                    "content": payload,
                }
            )
    return payloads


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
                "upstream-planning/project-management/candidate-feature-queue.json",
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


def _review_work_item_id(state: DeliveryState, target: str | None) -> str:
    candidate = str(target or "").strip()
    if not candidate:
        return ""
    board = state.get("work_board", {})
    items = board.get("items", []) if isinstance(board, dict) else []
    item_ids = {
        str(item.get("item_id") or "")
        for item in items
        if isinstance(item, dict) and item.get("item_id")
    }
    return candidate if candidate in item_ids else ""


def _artifact_paths_by_owner(state: DeliveryState, owner_agent: str) -> list[str]:
    return [
        str(artifact.get("path"))
        for artifact in state.get("artifacts", [])
        if artifact.get("owner_agent") == owner_agent and artifact.get("path")
    ]


def _existing_paths(state: DeliveryState, paths: list[str]) -> list[str]:
    run_dir = Path(state["run_dir"])
    return [path for path in paths if (run_dir / path).exists()]


def _unique_paths(paths: list[str]) -> list[str]:
    unique: list[str] = []
    for path in paths:
        if path and path not in unique:
            unique.append(path)
    return unique


def _pending_feature_ids(state: DeliveryState) -> list[str]:
    completed = {str(feature_id) for feature_id in state.get("completed_feature_ids", [])}
    statuses = {
        str(feature_id): str(status)
        for feature_id, status in dict(state.get("feature_statuses", {})).items()
    }
    done_statuses = {"qa_passed", "done", "deployed", "handoff_ready"}
    pending: list[str] = []
    for feature in sorted(
        list(state.get("feature_queue", [])),
        key=lambda item: int(item.get("delivery_order", 0) or 0),
    ):
        feature_id = str(feature.get("id") or feature.get("feature_id") or "")
        if (
            feature_id
            and feature_id not in completed
            and statuses.get(feature_id) not in done_statuses
        ):
            pending.append(feature_id)
    return pending


def promote_candidate_feature_queue(state: DeliveryState) -> DeliveryState:
    """Promote PM's candidate feature queue into the active delivery queue."""

    if state.get("feature_queue"):
        return sync_work_board(
            state,
            sprint_id=str(state.get("team_lead_sprint_id") or ""),
        )
    candidate_queue = [
        feature
        for feature in list(state.get("candidate_feature_queue", []))
        if isinstance(feature, dict)
    ]
    if not candidate_queue:
        return state

    updated = {**state}
    updated["feature_queue"] = candidate_queue
    next_feature = sorted(
        candidate_queue,
        key=lambda feature: int(feature.get("delivery_order", 0) or 0),
    )[0]
    if next_feature.get("id"):
        updated["active_feature_id"] = str(next_feature["id"])
    return sync_work_board(
        cast(DeliveryState, updated),
        sprint_id=str(updated.get("team_lead_sprint_id") or ""),
    )


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
    )


def write_head_result(state: DeliveryState, history: list[dict[str, Any]]) -> None:
    result = {
        "status": state.get("status"),
        "stage": state.get("stage"),
        "completed_nodes": state.get("completed_nodes", []),
        "blockers": state.get("blockers", []),
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
    )
    state["artifacts"] = [
        *state.get("artifacts", []),
        artifact_ref(
            relative_path,
            kind="internal",
            owner_agent=HEAD_AGENT_ID,
            visibility="developer",
        ),
    ]
    write_head_event(state, "head_agent_completed", {"result": result, "steps": len(history)})


def write_json_artifact(state: DeliveryState, relative_path: str, payload: dict[str, Any]) -> Path:
    path = Path(state["run_dir"]) / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_head_event(state: DeliveryState, event: str, data: dict[str, Any]) -> None:
    write_event(
        Path(state["run_dir"]) / "events.jsonl",
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
) -> None:
    try:
        register_artifact(
            Path(state["run_dir"]),
            relative_path=relative_path,
            run_id=state.get("run_id"),
            project_id=state.get("project_id"),
            owner_agent=HEAD_AGENT_ID,
            artifact_type=artifact_type,
            visibility=visibility,
            source_tool=source_tool,
        )
    except Exception:
        return


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


def _latest_history_tool(history: list[dict[str, Any]]) -> str:
    if not history:
        return "unknown"
    return str(history[-1].get("tool") or "unknown")


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
        for key in ("work_item_id", "target", "node_name"):
            if input_summary.get(key):
                return str(input_summary[key])
    return str(state.get("active_feature_id") or "") or None


def _duration_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
