"""Team Lead tool implementations exposed to the AgentExecutor."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from agentic_company.agents.handoff.contracts import (
    FINAL_PROJECT_REPORT_SCOPE,
    SPRINT_HANDOFF_SCOPE,
    handoff_contract_paths_for_scope,
)
from agentic_company.agents.registry import route_for_node
from agentic_company.agents.team_lead.contracts import (
    TeamLeadDecision,
    TeamLeadToolName,
    env_value,
)
from agentic_company.platform.agent_contracts import extend_artifacts
from agentic_company.platform.artifacts import (
    EXECUTION_REQUEST_ARTIFACT,
    artifact_ref,
    read_json_object_artifact,
)
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
from agentic_company.platform.sprints import (
    TeamLeadResult,
    features_for_sprint,
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
    """Stateful tools exposed to the LangChain executor."""

    delivery_state: DeliveryState
    sprint: dict[str, Any]
    workers: TeamLeadWorkers
    max_steps: int
    codex_reviewer: CodexReviewerLike | None = None
    status_inspector: StatusInspectorLike | None = None
    history: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.history is None:
            self.history = _read_history_artifact(self.delivery_state, self.sprint_id)

    def run_fullstack(
        self,
        target: str | None = None,
        reason: str = "",
        message: str = "",
    ) -> str:
        feature_id = self._feature_target(target)
        if not feature_id:
            self._record(
                "run_fullstack",
                target,
                reason or "Fullstack requested without a valid feature.",
                message,
                result_status="team_lead_fullstack_waiting_for_feature",
            )
            return self._tool_response(
                "Fullstack not run because no active sprint feature could be resolved from "
                "target/message/state."
            )
        if self._feature_repair_blocked(feature_id):
            self._record(
                "run_fullstack",
                feature_id,
                reason or "Fullstack repair requested after QA blocked the feature.",
                message,
                result_status="team_lead_repair_blocked",
            )
            return self._tool_response(
                "Fullstack repair not run because this feature is already QA blocked. "
                "A human/operator must resume or override the blocked feature."
            )
        return self._run_worker(
            "run_fullstack", feature_id, reason, message, self.workers.fullstack
        )

    def run_qa(self, target: str | None = None, reason: str = "", message: str = "") -> str:
        feature_id = self._feature_target(target)
        if not feature_id:
            self._record(
                "run_qa",
                target,
                reason or "QA requested without a valid feature.",
                message,
                result_status="team_lead_qa_waiting_for_feature",
            )
            return self._tool_response(
                "QA not run because no active sprint feature could be resolved from "
                "target/message/state."
            )
        if self._feature_repair_blocked(feature_id):
            self._record(
                "run_qa",
                feature_id,
                reason or "QA rerun requested after the feature was blocked.",
                message,
                result_status="team_lead_qa_blocked",
            )
            return self._tool_response(
                "QA not run because this feature is already blocked after the allowed repair "
                "attempts. A human/operator must resume or override the blocked feature."
            )
        return self._run_worker("run_qa", feature_id, reason, message, self.workers.qa)

    def run_deployment(
        self,
        target: str | None = None,
        reason: str = "",
        message: str = "",
    ) -> str:
        if limit_response := self._limit_response(message):
            return limit_response
        return self._run_worker(
            "run_deployment",
            target or self.sprint_id,
            reason,
            message,
            self.workers.deployment,
        )

    def run_post_deploy_qa(
        self,
        target: str | None = None,
        reason: str = "",
        message: str = "",
    ) -> str:
        if limit_response := self._limit_response(message):
            return limit_response
        updated = {**self.delivery_state}
        post_deploy_feature = {
            "id": "post-deploy",
            "title": "Post-deployment QA for sprint release",
            "acceptance_criteria": [
                "Public deployment targets are reachable.",
                "Delivered sprint features work against the deployed runtime.",
                "No release-blocking deployment or runtime issue is visible.",
            ],
            "delivery_order": 10_000,
        }
        updated["active_feature_id"] = "post-deploy"
        updated["feature_queue"] = [
            *[
                feature
                for feature in list(updated.get("feature_queue", []))
                if str(feature.get("id")) != "post-deploy"
            ],
            post_deploy_feature,
        ]
        outbound = append_agent_call_message(
            updated,
            node_name="qa",
            target="post-deploy",
            reason=reason,
            message=message,
        )
        write_request(
            updated,
            kind="post_deploy_quality_request",
            target_agent="qa-agent",
            payload={
                "sprint_id": self.sprint_id,
                "feature": post_deploy_feature,
                "public_url": updated.get("public_url"),
                "public_urls": updated.get("public_urls", []),
                "deployment_status": updated.get("deployment_status"),
                "message": message,
                "message_id": outbound.message_id,
                "message_intent": outbound.intent,
                "artifact_refs": outbound.artifact_refs,
            },
        )
        write_temporary_execution_feature(updated, post_deploy_feature)
        write_team_lead_event(
            updated,
            "team_lead_post_deploy_qa_started",
            {"deployment_status": updated.get("deployment_status")},
        )
        checked = self.workers.qa(cast(DeliveryState, updated))
        checked["post_deploy_qa_status"] = checked.get("qa_status")
        if checked.get("post_deploy_qa_status") == "passed":
            checked["post_deploy_repair_attempts"] = 0
        else:
            attempts = int(checked.get("post_deploy_repair_attempts", 0)) + 1
            checked["post_deploy_repair_attempts"] = attempts
            if attempts >= int(checked.get("max_repair_attempts", 5)):
                checked["blockers"] = [
                    *checked.get("blockers", []),
                    f"Post-deployment QA failed after {attempts} attempts.",
                ]
        checked["feature_queue"] = [
            feature
            for feature in list(checked.get("feature_queue", []))
            if str(feature.get("id")) != "post-deploy"
        ]
        checked["completed_feature_ids"] = [
            feature_id
            for feature_id in list(checked.get("completed_feature_ids", []))
            if feature_id != "post-deploy"
        ]
        feature_statuses = dict(checked.get("feature_statuses", {}))
        feature_statuses.pop("post-deploy", None)
        checked["feature_statuses"] = feature_statuses
        if checked.get("active_feature_id") == "post-deploy":
            checked["active_feature_id"] = None
        write_team_lead_event(
            checked,
            "team_lead_post_deploy_qa_completed",
            {"status": checked.get("post_deploy_qa_status")},
        )
        self.delivery_state = sync_work_board(checked, sprint_id=self.sprint_id)
        downstream_response = latest_downstream_response(
            self.delivery_state,
            from_agent="qa-agent",
            correlation_id="post-deploy",
        )
        self._record("run_post_deploy_qa", target or "post-deploy", reason, message)
        return self._tool_response(
            f"Post-deploy QA status: {checked.get('post_deploy_qa_status')}.",
            downstream_response=downstream_response,
        )

    def run_handoff(
        self,
        handoff_scope: str,
        sprint_id: str = "",
        reason: str = "",
        message: str = "",
    ) -> str:
        if limit_response := self._limit_response(message):
            return limit_response
        try:
            contract_paths = handoff_contract_paths_for_scope(
                handoff_scope,
                sprint_id=sprint_id,
            )
        except ValueError as exc:
            self._record(
                "run_handoff",
                handoff_scope or None,
                reason or "Handoff requested with an invalid scope contract.",
                message,
                result_status="team_lead_handoff_contract_invalid",
            )
            return self._tool_response(f"Handoff not run: {exc}")

        target = sprint_id if handoff_scope == SPRINT_HANDOFF_SCOPE else FINAL_PROJECT_REPORT_SCOPE
        updated = {**self.delivery_state}
        updated["handoff_scope"] = handoff_scope
        updated["handoff_sprint_id"] = sprint_id
        updated["handoff_output_dir"] = str(Path(contract_paths.summary).parent)
        updated["handoff_expected_outputs"] = contract_paths.as_list()
        self.delivery_state = cast(DeliveryState, updated)
        return self._run_worker("run_handoff", target, reason, message, self.workers.handoff)

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
        if limit_response := self._limit_response(message or question):
            return limit_response
        review_item_id = _review_work_item_id(self.delivery_state, target)
        if review_item_id:
            self.delivery_state = set_work_item_status(
                self.delivery_state,
                review_item_id,
                "review",
                active=True,
                sprint_id=self.sprint_id,
            )
            checkpoint_delivery_state(self.delivery_state)
        refs = _split_artifact_refs(artifact_refs)
        review_request = CodexReviewRequest(
            run_id=self.delivery_state["run_id"],
            run_dir=Path(self.delivery_state["run_dir"]),
            requesting_agent=TEAM_LEAD_AGENT_ID,
            target_agent=target_agent or None,
            correlation_id=target or self.sprint_id,
            purpose=purpose or reason or "Review referenced delivery artifacts.",
            question=question
            or message
            or reason
            or "Review the referenced artifacts according to the requesting agent's purpose.",
            artifact_refs=refs,
            execution_id=build_agent_execution_id(
                run_id=str(self.delivery_state["run_id"]),
                agent_id=TEAM_LEAD_AGENT_ID,
                target=target or self.sprint_id,
                intent="codex_review",
                message_id=question or message or reason or target_agent,
            ),
            codex_resume_thread_id=codex_resume_thread_id(
                self.delivery_state, TEAM_LEAD_CODEX_REVIEW_AGENT_ID
            ),
        )
        reviewer = self.codex_reviewer or CodexReviewRunner()
        result = reviewer.run(review_request)
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
                    correlation_id=target or self.sprint_id,
                    execution_id=result.execution_id or None,
                )
            )
        self._record("codex_review", target_agent or target or self.sprint_id, reason, message)
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
                "to_agent": target_agent or None,
                "correlation_id": target or self.sprint_id,
                "execution_id": result.execution_id,
                "codex_thread_id": result.codex_thread_id,
            },
        )

    def inspect_sprint_status(
        self,
        target: str | None = None,
        reason: str = "",
        message: str = "",
    ) -> str:
        if limit_response := self._limit_response(message):
            return limit_response
        refs = _sprint_status_artifact_refs(self.delivery_state, self.sprint_id)
        inspection_request = StatusInspectionRequest(
            run_id=self.delivery_state["run_id"],
            run_dir=Path(self.delivery_state["run_dir"]),
            requesting_agent=TEAM_LEAD_AGENT_ID,
            scope="sprint",
            purpose=(
                reason
                or "Inspect the active sprint work board, worker calls, gates, evidence, "
                "blockers, and completion readiness. Do not choose Team Lead routing."
            ),
            status_context=_sprint_status_context(
                self.delivery_state,
                self.sprint,
                self.sprint_id,
                self.history or [],
            ),
            artifact_refs=refs,
            correlation_id=target or self.sprint_id,
            model=env_value("TEAM_LEAD_STATUS_INSPECTOR_CODEX_MODEL", self.delivery_state)
            or env_value("AGENT_CODEX_MODEL", self.delivery_state)
            or "gpt-5.3-codex",
            execution_id=build_agent_execution_id(
                run_id=str(self.delivery_state["run_id"]),
                agent_id=TEAM_LEAD_AGENT_ID,
                target=target or self.sprint_id,
                intent="inspect_sprint_status",
                message_id=message or reason or target or "sprint-status",
            ),
            codex_resume_thread_id=codex_resume_thread_id(
                self.delivery_state,
                TEAM_LEAD_STATUS_INSPECTOR_AGENT_ID,
            ),
        )
        inspector = self.status_inspector or StatusInspectorRunner()
        result = inspector.run(inspection_request)
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
        record_codex_thread(
            self.delivery_state,
            TEAM_LEAD_STATUS_INSPECTOR_AGENT_ID,
            result.codex_thread_id,
        )
        self._record(
            "inspect_sprint_status",
            target or self.sprint_id,
            reason,
            message,
            result_status=str(result.payload.get("sprint_status") or result.status),
        )
        return self._tool_response(
            f"Sprint status inspection {result.status}.",
            downstream_response={
                "from_agent": "codex-status-inspector",
                "intent": "inspect_sprint_status",
                "content": result.payload,
                "artifact_refs": [
                    *result.artifact_refs,
                    result.result_artifact,
                    result.summary_artifact,
                    result.prompt_artifact,
                    result.log_artifact,
                    result.raw_events_artifact,
                ],
                "correlation_id": target or self.sprint_id,
                "execution_id": result.execution_id,
                "codex_thread_id": result.codex_thread_id,
            },
        )

    def complete_sprint(
        self,
        target: str | None = None,
        reason: str = "",
        message: str = "",
    ) -> str:
        if limit_response := self._limit_response(message):
            return limit_response
        self.delivery_state = mark_node_completed(
            self.delivery_state,
            node_name="team_lead",
            stage="team_lead",
            status="team_lead_sprint_handoff_ready",
        )
        write_team_lead_event(
            self.delivery_state,
            "team_lead_complete_sprint_requested",
            {"reason": reason, "message": message},
        )
        self._record("complete_sprint", target or self.sprint_id, reason, message)
        return self._tool_response(
            "Sprint completed.",
            artifact_refs=_team_lead_completion_artifact_refs(self.delivery_state, self.sprint_id),
        )

    def block_sprint(
        self,
        reason: str,
        target: str | None = None,
        message: str = "",
    ) -> str:
        self.delivery_state = mark_node_completed(
            self.delivery_state,
            node_name="team_lead",
            stage="team_lead",
            status="team_lead_sprint_blocked",
        )
        self.delivery_state["blockers"] = [*self.delivery_state.get("blockers", []), reason]
        write_team_lead_event(
            self.delivery_state,
            "team_lead_blocked_sprint",
            TeamLeadDecision("block_sprint", reason, target, message).to_dict(),
        )
        self._record("block_sprint", target, reason, message)
        return self._tool_response("Sprint blocked: " + reason)

    @property
    def sprint_id(self) -> str:
        return str(
            self.sprint.get("sprint_id")
            or self.delivery_state.get("team_lead_sprint_id")
            or "sprint-01"
        )

    def result(self) -> TeamLeadExecutorResult:
        write_history_artifact(self.delivery_state, self.sprint_id, self.history or [])
        checkpoint_delivery_state(self.delivery_state)
        return TeamLeadExecutorResult(self.delivery_state, list(self.history or []))

    def _feature_target(self, target: str | None) -> str | None:
        feature_ids = {
            str(feature.get("id"))
            for feature in features_for_sprint(self.delivery_state, self.sprint_id)
            if feature.get("id")
        }
        candidates = [
            _extract_feature_id(str(target or ""), feature_ids),
            str(self.delivery_state.get("active_feature_id") or ""),
        ]
        next_feature = next_feature_for_state(self.delivery_state, self.sprint_id)
        if next_feature:
            candidates.append(str(next_feature["id"]))
        for feature_id in candidates:
            feature = feature_by_id(self.delivery_state, feature_id)
            if feature_id and feature and self._feature_in_current_sprint(feature):
                return feature_id
        return None

    def _feature_in_current_sprint(self, feature: dict[str, Any]) -> bool:
        current_ids = {
            str(item.get("id"))
            for item in features_for_sprint(self.delivery_state, self.sprint_id)
            if item.get("id")
        }
        return str(feature.get("id")) in current_ids

    def _run_worker(
        self,
        tool: TeamLeadToolName,
        target: str,
        reason: str,
        message: str,
        worker: TeamLeadWorker,
    ) -> str:
        if limit_response := self._limit_response(message):
            return limit_response
        updated = {**self.delivery_state}
        if target != self.sprint_id:
            if tool == "run_fullstack":
                updated = set_work_item_status(
                    cast(DeliveryState, updated),
                    target,
                    "in_progress",
                    active=True,
                    sprint_id=self.sprint_id,
                )
            elif tool == "run_qa":
                updated = set_work_item_status(
                    cast(DeliveryState, updated),
                    target,
                    "in_qa",
                    active=True,
                    sprint_id=self.sprint_id,
                )
        self.delivery_state = cast(DeliveryState, updated)
        node_name = tool.removeprefix("run_")
        outbound = append_agent_call_message(
            updated,
            node_name=node_name,
            target=target,
            reason=reason,
            message=message,
        )
        target_agent = outbound.to_agent
        execution_id = outbound.execution_id or ""
        updated["agent_execution_id"] = execution_id
        updated["agent_execution_intent"] = outbound.intent
        updated["agent_execution_agent_id"] = target_agent
        self.delivery_state = sync_work_board(
            cast(DeliveryState, updated),
            sprint_id=self.sprint_id,
        )
        write_request(
            self.delivery_state,
            kind=f"{node_name}_request",
            target_agent=target_agent,
            payload={
                "sprint_id": self.sprint_id,
                "active_feature_id": self.delivery_state.get("active_feature_id"),
                "message": message,
                "message_id": outbound.message_id,
                "message_intent": outbound.intent,
                "execution_id": execution_id,
                "artifact_refs": outbound.artifact_refs,
            },
        )
        write_team_lead_event(
            self.delivery_state,
            "team_lead_worker_started",
            {
                "node": node_name,
                "active_feature_id": self.delivery_state.get("active_feature_id"),
                "reason": reason,
                "execution_id": execution_id,
            },
        )
        checkpoint_delivery_state(self.delivery_state)
        self.delivery_state = worker(self.delivery_state)
        if tool == "run_fullstack" and str(self.delivery_state.get("status", "")).startswith(
            "fullstack_feature_implemented"
        ):
            self.delivery_state = set_work_item_status(
                self.delivery_state,
                target,
                "implemented",
                active=True,
                sprint_id=self.sprint_id,
            )
        else:
            self.delivery_state = sync_work_board(self.delivery_state, sprint_id=self.sprint_id)
        downstream_response = latest_downstream_response(
            self.delivery_state,
            from_agent=target_agent,
            correlation_id=target,
        )
        write_team_lead_event(
            self.delivery_state,
            "team_lead_worker_completed",
            {
                "node": node_name,
                "stage": self.delivery_state["stage"],
                "status": self.delivery_state["status"],
                "execution_id": execution_id,
            },
        )
        self._record(tool, target, reason, message)
        return self._tool_response(
            f"{tool} completed with status {self.delivery_state.get('status')}.",
            downstream_response=downstream_response,
        )

    def _record(
        self,
        tool: TeamLeadToolName,
        target: str | None,
        reason: str,
        message: str,
        *,
        result_status: str | None = None,
    ) -> None:
        step = len(self.history or []) + 1
        decision = TeamLeadDecision(tool, reason or "No reason provided.", target, message)
        decision_path = write_decision_artifact(self.delivery_state, step, decision)
        write_team_lead_event(
            self.delivery_state,
            "team_lead_decision",
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
                "active_feature_id": self.delivery_state.get("active_feature_id"),
                "qa_status": self.delivery_state.get("qa_status"),
                "deployment_status": self.delivery_state.get("deployment_status"),
                "post_deploy_qa_status": self.delivery_state.get("post_deploy_qa_status"),
                "blockers": self.delivery_state.get("blockers", []),
            }
        )
        self.history = history
        write_history_artifact(self.delivery_state, self.sprint_id, history)
        write_team_lead_event(
            self.delivery_state,
            "team_lead_tool_completed",
            {"tool": tool, "status": status},
        )
        self.delivery_state = sync_work_board(self.delivery_state, sprint_id=self.sprint_id)
        checkpoint_delivery_state(self.delivery_state)

    def _step_limit_reached(self) -> bool:
        return len(self.history or []) >= self.max_steps

    def _feature_repair_blocked(self, feature_id: str) -> bool:
        attempts = dict(self.delivery_state.get("feature_repair_attempts", {}))
        max_attempts = int(self.delivery_state.get("max_repair_attempts", 5))
        return (
            self.delivery_state.get("status") == "qa_feature_failed_blocked"
            and attempts.get(feature_id, 0) >= max_attempts
        )

    def _limit_response(self, message: str) -> str:
        if not self._step_limit_reached():
            return ""
        return self.block_sprint(reason="Team Lead exceeded max tool calls.", message=message)

    def _tool_response(
        self,
        message: str,
        *,
        downstream_response: dict[str, Any] | None = None,
        artifact_refs: list[str] | None = None,
    ) -> str:
        snapshot = {
            "message": message,
            "status": self.delivery_state.get("status"),
            "active_feature_id": self.delivery_state.get("active_feature_id"),
            "completed_feature_ids": self.delivery_state.get("completed_feature_ids", []),
            "feature_statuses": self.delivery_state.get("feature_statuses", {}),
            "work_board": self.delivery_state.get("work_board", {}),
            "qa_status": self.delivery_state.get("qa_status"),
            "deployment_status": self.delivery_state.get("deployment_status"),
            "post_deploy_qa_status": self.delivery_state.get("post_deploy_qa_status"),
            "blockers": self.delivery_state.get("blockers", []),
        }
        if artifact_refs is not None:
            snapshot["artifact_refs"] = artifact_refs
        if downstream_response is not None:
            snapshot["downstream_response"] = downstream_response
        return json.dumps(snapshot, sort_keys=True)


def apply_team_lead_result(state: DeliveryState, sprint_id: str) -> DeliveryState:
    state = sync_work_board(state, sprint_id=sprint_id)
    completed = [
        feature_id
        for feature_id in list(state.get("completed_feature_ids", []))
        if feature_id != "post-deploy"
    ]
    feature_statuses = dict(state.get("feature_statuses", {}))
    failed = [
        feature_id
        for feature_id, status in feature_statuses.items()
        if status in {"qa_failed", "blocked"}
    ]
    if state.get("blockers"):
        status = "blocked"
        next_action = "Resolve blockers before continuing."
    elif state.get("status") == "team_lead_sprint_handoff_ready":
        status = "handoff_ready"
        next_action = "Sprint is ready for review or the next sprint."
    elif state.get("deployment_status") == "deployed":
        status = "deployed"
        next_action = "Create or review sprint handoff."
    else:
        status = "running"
        next_action = "Continue sprint execution."

    artifact_refs = _team_lead_completion_artifact_refs(state, sprint_id)
    handoff_artifacts = _latest_handoff_artifact_refs(state)
    result = TeamLeadResult(
        sprint_id=sprint_id,
        status=status,
        completed_features=completed,
        failed_features=failed,
        blockers=list(state.get("blockers", [])),
        deployment_status=state.get("deployment_status"),
        qa_status=state.get("qa_status"),
        handoff_status="ready"
        if handoff_artifacts
        else state.get("status")
        if str(state.get("status", "")).startswith("handoff")
        else None,
        next_recommended_action=next_action,
        artifact_refs=artifact_refs,
    )
    result_path = f"team-lead/{result.sprint_id}-result.json"
    write_json_artifact(state, result_path, result.to_dict())
    extend_artifacts(
        state,
        [
            artifact_ref(
                result_path,
                kind="internal",
                owner_agent=TEAM_LEAD_AGENT_ID,
                visibility="developer",
            )
        ],
    )
    _append_team_lead_response_to_head(state, result.to_dict(), [result_path, *artifact_refs])
    write_team_lead_event(state, "team_lead_sprint_completed", result.to_dict())
    checkpoint_delivery_state(state)
    return state


def _sprint_status_context(
    state: DeliveryState,
    sprint: dict[str, Any],
    sprint_id: str,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    feature_statuses = dict(state.get("feature_statuses", {}))
    completed = {str(feature_id) for feature_id in state.get("completed_feature_ids", [])}
    tasks: list[dict[str, Any]] = []
    for feature in features_for_sprint(state, sprint_id):
        feature_id = str(feature.get("id") or feature.get("feature_id") or "")
        if not feature_id:
            continue
        evidence_refs = _artifact_refs_for_feature(state, feature_id)
        status = _status_for_sprint_inspection(
            str(feature_statuses.get(feature_id) or feature.get("status") or "pending"),
            evidence_refs=evidence_refs,
        )
        if feature_id in completed and status == "pending":
            status = "qa_passed"
        tasks.append(
            {
                "id": feature_id,
                "title": feature.get("title"),
                "status": status,
                "owner_agent": feature.get("suggested_owner_agent"),
                "evidence_refs": evidence_refs,
                "blockers": _blockers_for_feature(state, feature_id),
                "delivery_order": feature.get("delivery_order"),
                "acceptance_criteria": feature.get("acceptance_criteria", []),
                "dependencies": feature.get("dependencies", []),
                "qa_notes": feature.get("qa_notes", []),
                "deployment_notes": feature.get("deployment_notes", []),
            }
        )
    next_feature = next_feature_for_state(state, sprint_id)
    return {
        "run_id": state.get("run_id"),
        "stage": state.get("stage"),
        "status": state.get("status"),
        "sprint": sprint,
        "sprint_id": sprint_id,
        "active_feature_id": state.get("active_feature_id"),
        "tasks": tasks,
        "next_feature": next_feature,
        "features_not_qa_passed": features_not_qa_passed(state, sprint_id),
        "completed_feature_ids": list(state.get("completed_feature_ids", [])),
        "feature_statuses": feature_statuses,
        "feature_repair_attempts": state.get("feature_repair_attempts", {}),
        "qa_status": state.get("qa_status"),
        "deployment_status": state.get("deployment_status"),
        "post_deploy_qa_status": state.get("post_deploy_qa_status"),
        "public_url": state.get("public_url"),
        "public_urls": state.get("public_urls", []),
        "blockers": state.get("blockers", []),
        "handoff_artifact_refs": _latest_handoff_artifact_refs(state),
        "team_lead_history": history,
        "messages": _recent_messages(state, sprint_id=sprint_id, limit=40),
        "artifact_refs": _sprint_status_artifact_refs(state, sprint_id),
        "status_rules": {
            "implemented": "Owner work exists but QA still needs to validate the same task.",
            "qa_passed": "Feature can count as sprint work done.",
            "pending": "Feature is planned but owner implementation has not started.",
            "ready_for_handoff": (
                "All feature/gate work appears done but Handoff still owns evidence."
            ),
            "ready_to_complete": (
                "Sprint can complete only after Handoff-owned evidence refs exist and "
                "can_complete_sprint is true."
            ),
        },
    }


def _status_for_sprint_inspection(status: str, *, evidence_refs: list[str]) -> str:
    normalized = status.strip().lower()
    if normalized in {"active", "ready"}:
        return "pending"
    if normalized == "review":
        return "implemented" if evidence_refs else "pending"
    if normalized in {
        "pending",
        "assigned",
        "in_progress",
        "implemented",
        "in_qa",
        "qa_passed",
        "qa_failed",
        "blocked",
    }:
        return normalized
    if normalized in {"done", "deployed", "handoff_ready"}:
        return "qa_passed"
    return "pending"


def _artifact_refs_for_feature(state: DeliveryState, feature_id: str) -> list[str]:
    refs: list[str] = []
    for artifact in state.get("artifacts", []):
        path = str(artifact.get("path") or "")
        if path and (
            f"/{feature_id}/" in path or f"-{feature_id}" in path or path.endswith(feature_id)
        ):
            refs.append(path)
    return _unique_paths(refs)


def _blockers_for_feature(state: DeliveryState, feature_id: str) -> list[str]:
    return [str(blocker) for blocker in state.get("blockers", []) if feature_id in str(blocker)]


def _sprint_status_artifact_refs(state: DeliveryState, sprint_id: str) -> list[str]:
    run_dir = Path(state["run_dir"])
    discovered = [
        "00-requirements.md",
        "upstream-planning/project-management/release-plan.md",
        "upstream-planning/project-management/release-plan.json",
        "upstream-planning/project-management/candidate-feature-queue.json",
        "upstream-planning/project-management/roadmap.csv",
        f"upstream-planning/project-management/{sprint_id}.json",
        f"upstream-planning/project-management/{sprint_id}.md",
        f"upstream-planning/project-management/{sprint_id}-plan.json",
        f"upstream-planning/project-management/{sprint_id}-plan.md",
        f"team-lead/{sprint_id}-history.json",
        f"team-lead/{sprint_id}-result.json",
    ]
    for pattern in [
        "team-lead/requests/*.json",
        "handoff/**/*.md",
        "handoff/**/*.json",
        "qa/**/*.json",
        "deployment/**/*.json",
        "fullstack/**/*.json",
    ]:
        discovered.extend(path.relative_to(run_dir).as_posix() for path in run_dir.glob(pattern))
    discovered.extend(str(artifact.get("path")) for artifact in state.get("artifacts", []))
    return _unique_paths([path for path in discovered if path and (run_dir / path).exists()])


def _recent_messages(
    state: DeliveryState,
    *,
    sprint_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    try:
        messages = AgentMessageStore(state["run_dir"]).read(limit=limit)
    except Exception:
        return []
    recent: list[dict[str, Any]] = []
    for message in messages:
        if message.correlation_id and message.correlation_id not in {
            sprint_id,
            str(state.get("active_feature_id") or ""),
            "post-deploy",
        }:
            continue
        recent.append(
            {
                "from_agent": message.from_agent,
                "to_agent": message.to_agent,
                "intent": message.intent,
                "correlation_id": message.correlation_id,
                "execution_id": message.execution_id,
                "artifact_refs": message.artifact_refs,
                "content": message.content[:1000],
            }
        )
    return recent


def _team_lead_completion_artifact_refs(state: DeliveryState, sprint_id: str) -> list[str]:
    """Return the canonical artifacts Head should inspect for this Team Lead result."""

    return _unique_paths(
        [
            f"team-lead/{sprint_id}-history.json",
            *_latest_handoff_artifact_refs(state),
        ]
    )


def _latest_handoff_artifact_refs(state: DeliveryState) -> list[str]:
    return _unique_paths(
        [
            str(artifact.get("path"))
            for artifact in state.get("artifacts", [])
            if artifact.get("kind") == "handoff" and artifact.get("path")
        ]
    )


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


def next_feature_for_state(
    state: DeliveryState,
    sprint_id: str | None = None,
) -> dict[str, Any] | None:
    completed = set(state.get("completed_feature_ids", []))
    active_sprint_id = sprint_id or str(state.get("team_lead_sprint_id") or "sprint-01")
    feature_queue = features_for_sprint(state, active_sprint_id)
    for feature in feature_queue:
        feature_id = str(feature["id"])
        if (
            feature_id not in completed
            and dict(state.get("feature_statuses", {})).get(feature_id) != "qa_passed"
        ):
            return feature
    return None


def feature_by_id(state: DeliveryState, feature_id: str) -> dict[str, Any] | None:
    for feature in list(state.get("feature_queue", [])):
        if str(feature.get("id")) == feature_id:
            return feature
    return None


def _extract_feature_id(raw_target: str, feature_ids: set[str]) -> str:
    target = raw_target.strip()
    if target in feature_ids:
        return target
    for feature_id in sorted(feature_ids, key=len, reverse=True):
        if re.search(rf"\b{re.escape(feature_id)}\b", target):
            return feature_id
    return ""


def features_not_qa_passed(
    state: DeliveryState,
    sprint_id: str | None = None,
) -> list[str]:
    feature_statuses = dict(state.get("feature_statuses", {}))
    completed = set(state.get("completed_feature_ids", []))
    active_sprint_id = sprint_id or str(state.get("team_lead_sprint_id") or "sprint-01")
    missing: list[str] = []
    for feature in features_for_sprint(state, active_sprint_id):
        feature_id = str(feature.get("id"))
        if feature_id == "post-deploy":
            continue
        if feature_id not in completed or feature_statuses.get(feature_id) != "qa_passed":
            missing.append(feature_id)
    return missing


def target_agent_id(node_name: str) -> str:
    return route_for_node(node_name)[0]


def agent_message_intent(node_name: str) -> str:
    return route_for_node(node_name)[1]


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
    content = _agent_call_message(
        state=state,
        node_name=node_name,
        target=target,
        message=message,
        reason=reason,
    )
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
            from_agent=TEAM_LEAD_AGENT_ID,
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


def _agent_call_message(
    *,
    state: DeliveryState,
    node_name: str,
    target: str,
    message: str,
    reason: str,
) -> str:
    content = message.strip() or reason.strip()
    coordinator_note = content or "Please handle the delegated agent task and report the result."
    packet = _canonical_work_item_packet(state, target)
    if not packet:
        return coordinator_note
    return (
        f"{coordinator_note}\n\n"
        "Canonical work item packet:\n"
        f"{json.dumps(packet, indent=2, sort_keys=True)}\n\n"
        "Contract precedence:\n"
        "- Treat the canonical work item packet and referenced artifacts as the source of truth.\n"
        "- Treat this coordinator note as routing/context only.\n"
        "- Do not add stricter acceptance criteria, status codes, feature scope, deployment gates, "
        "or QA gates unless they are present in the canonical packet or cited artifacts.\n"
        "- If the coordinator note appears to conflict with the canonical packet or cited "
        "artifacts, report the mismatch instead of silently changing the contract.\n"
        f"- This request is for `{node_name}` ownership of work item `{target}`."
    )


def _canonical_work_item_packet(state: DeliveryState, target: str) -> dict[str, Any]:
    feature = feature_by_id(state, target)
    if not feature:
        return {}
    sprint_id = str(feature.get("sprint_id") or state.get("team_lead_sprint_id") or "")
    return {
        "id": str(feature.get("id") or target),
        "title": feature.get("title"),
        "description": feature.get("description"),
        "sprint_id": sprint_id,
        "delivery_order": feature.get("delivery_order"),
        "suggested_owner_agent": feature.get("suggested_owner_agent"),
        "acceptance_criteria": feature.get("acceptance_criteria", []),
        "definition_of_done": feature.get("definition_of_done", []),
        "dependencies": feature.get("dependencies", []),
        "qa_notes": feature.get("qa_notes", []),
        "deployment_notes": feature.get("deployment_notes"),
        "source_refs": feature.get("source_refs", []),
        "architecture_components": feature.get("architecture_components", []),
        "current_status": dict(state.get("feature_statuses", {})).get(str(feature.get("id"))),
        "completed_feature_ids": list(state.get("completed_feature_ids", [])),
    }


def _agent_call_artifacts(node_name: str, state: DeliveryState) -> list[str]:
    base = _upstream_planning_artifacts(state)
    if node_name == "fullstack":
        return _unique_paths(
            [
                *base,
                *_artifact_paths_by_kind(state, "qa"),
            ]
        )
    if node_name == "qa":
        return _unique_paths([*base, *_artifact_paths_by_kind(state, "execution")])
    if node_name == "deployment":
        return _unique_paths([*base, *_artifact_paths_by_kind(state, "qa")])
    if node_name == "handoff":
        return _unique_paths(
            [
                *base,
                *_artifact_paths_by_kind(state, "execution"),
                *_artifact_paths_by_kind(state, "qa"),
                *_artifact_paths_by_kind(state, "deployment"),
            ]
        )
    return base


def _split_artifact_refs(value: str) -> list[str]:
    refs: list[str] = []
    for raw in re.split(r"[\n,;]+", value or ""):
        item = raw.strip()
        if item and item not in refs:
            refs.append(item)
    return refs


def _review_work_item_id(state: DeliveryState, target: str | None) -> str:
    candidate = str(target or state.get("active_feature_id") or "").strip()
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


def _artifact_paths_by_kind(state: DeliveryState, kind: str) -> list[str]:
    return [
        str(artifact.get("path"))
        for artifact in state.get("artifacts", [])
        if artifact.get("kind") == kind and artifact.get("path")
    ]


def _upstream_planning_artifacts(state: DeliveryState) -> list[str]:
    return _unique_paths(
        [
            "00-requirements.md",
            *[
                str(artifact.get("path"))
                for artifact in state.get("artifacts", [])
                if artifact.get("kind") == "planning"
                and artifact.get("path")
                and str(artifact.get("path")).startswith("upstream-planning/")
                and "/codex/" not in str(artifact.get("path"))
            ],
        ]
    )


def _unique_paths(paths: list[str]) -> list[str]:
    unique: list[str] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def write_request(
    state: DeliveryState,
    *,
    kind: str,
    target_agent: str,
    payload: dict[str, Any],
) -> Path:
    run_dir = Path(state["run_dir"])
    request_dir = run_dir / "team-lead" / "requests"
    request_dir.mkdir(parents=True, exist_ok=True)
    feature_id = state.get("active_feature_id") or "sprint"
    execution_id = str(payload.get("execution_id") or state.get("agent_execution_id") or "")
    suffix = f"-{short_hash(execution_id)}" if execution_id else ""
    path = request_dir / f"{kind}-{feature_id}{suffix}.json"
    body = {
        "kind": kind,
        "source_agent": TEAM_LEAD_AGENT_ID,
        "target_agent": target_agent,
        "run_id": state["run_id"],
        "stage": state["stage"],
        "status": state["status"],
        **payload,
    }
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_temporary_execution_feature(state: DeliveryState, feature: dict[str, Any]) -> None:
    request_path = Path(state["run_dir"]) / EXECUTION_REQUEST_ARTIFACT
    if not request_path.exists():
        return
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    payload["active_feature"] = feature
    payload["completed_feature_ids"] = list(state.get("completed_feature_ids", []))
    input_artifacts = list(payload.get("input_artifacts", []))
    request_artifact = f"team-lead/requests/post_deploy_quality_request-{feature['id']}.json"
    if request_artifact not in input_artifacts:
        input_artifacts.append(request_artifact)
    payload["input_artifacts"] = input_artifacts
    request_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_decision_artifact(state: DeliveryState, step: int, decision: TeamLeadDecision) -> str:
    relative_path = f"team-lead/decisions/{step:03d}-{decision.tool}.json"
    write_json_artifact(state, relative_path, decision.to_dict())
    return relative_path


def write_history_artifact(
    state: DeliveryState,
    sprint_id: str,
    history: list[dict[str, Any]],
) -> None:
    write_json_artifact(state, f"team-lead/{sprint_id}-history.json", {"steps": history})


def _read_history_artifact(state: DeliveryState, sprint_id: str) -> list[dict[str, Any]]:
    path = Path(state["run_dir"]) / f"team-lead/{sprint_id}-history.json"
    if not path.exists():
        return []
    try:
        payload = read_json_object_artifact(path, normalize_bom=True)
    except (OSError, json.JSONDecodeError):
        return []
    steps = payload.get("steps", [])
    if not isinstance(steps, list):
        return []
    return [dict(step) for step in steps if isinstance(step, dict)]


def write_json_artifact(state: DeliveryState, relative_path: str, payload: dict[str, Any]) -> Path:
    path = Path(state["run_dir"]) / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_team_lead_event(state: DeliveryState, event: str, data: dict[str, Any]) -> None:
    write_event(
        Path(state["run_dir"]) / "events.jsonl",
        state["run_id"],
        TEAM_LEAD_AGENT_ID,
        event,
        data,
    )


def checkpoint_delivery_state(state: DeliveryState) -> None:
    write_delivery_state(state)
