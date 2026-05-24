"""Internal LangGraph for the QA Agent."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NotRequired, Protocol, TypedDict, cast

from agentic_company.agents.quality.codex_cli import (
    QUALITY_CODEX_AGENT_ID,
    QualityCodexRunner,
)
from agentic_company.platform.agent_contracts import (
    append_downstream_response,
    artifact_refs,
    extend_artifacts,
)
from agentic_company.platform.agent_runtime import (
    AGENT_EXECUTOR_GRAPH_NODE_ORDER,
    SpecialistAgentExecutor,
    SpecialistAgentRequest,
    agent_env_value,
    build_agent_executor_graph,
)
from agentic_company.platform.artifacts import (
    build_execution_request_payload,
    update_execution_request_context,
    write_execution_request,
)
from agentic_company.platform.events import write_event
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.sprints import features_for_sprint
from agentic_company.platform.state import (
    DeliveryState,
    codex_resume_thread_id,
    mark_node_completed,
)

QUALITY_AGENT_ID = "qa-agent"

QUALITY_AGENT_GRAPH_NODE_ORDER = AGENT_EXECUTOR_GRAPH_NODE_ORDER
QUALITY_AGENT_SYSTEM_PROMPT = """You are the QA Agent for agentic-company.

You own validation work only through the available tools. Call `codex_exec` to
run the Codex QA worker for the assigned feature, deployment, or release check.
Do not claim QA is complete without calling a tool.
"""


class FeatureQaRunner(Protocol):
    """Feature-scoped Codex QA execution boundary."""

    def run(self, run_dir: Path) -> AgentRunResult:
        """Run QA for the active feature and return the parsed QA result."""


class QualityAgentGraphState(TypedDict):
    """Internal state for the QA Agent subgraph."""

    delivery_state: DeliveryState
    run_dir: str
    feature_id: NotRequired[str | None]
    result: NotRequired[AgentRunResult]
    status: NotRequired[str]


def build_quality_agent_graph(
    runner: FeatureQaRunner | None = None,
    *,
    agent_executor: SpecialistAgentExecutor,
    node_order: Sequence[str] | None = None,
):
    """Build the QA Agent internal graph.

    The graph is intentionally generic. It does not encode concrete QA commands,
    endpoint names, browser scripts, or Docker checks. Those choices belong to the
    Codex QA specialist inside the `codex_quality_execution` node.
    """

    order = list(QUALITY_AGENT_GRAPH_NODE_ORDER if node_order is None else node_order)
    if not order:
        raise ValueError("Quality agent graph requires at least one node.")

    node_map = {
        "prepare_context": _prepare_context,
        "run_agent_executor": _run_agent_executor(runner, agent_executor),
        "apply_result": _apply_quality_result,
    }
    return build_agent_executor_graph(
        QualityAgentGraphState,
        prepare_node=node_map[order[0]],
        run_agent_executor_node=node_map[order[1]],
        apply_result_node=node_map[order[2]],
        node_order=tuple(order),
    )


def run_quality_agent_graph(
    delivery_state: DeliveryState,
    *,
    runner: FeatureQaRunner | None = None,
    agent_executor: SpecialistAgentExecutor,
) -> DeliveryState:
    """Run the QA Agent subgraph and return updated delivery state."""

    graph_state: QualityAgentGraphState = {
        "delivery_state": delivery_state,
        "run_dir": delivery_state["run_dir"],
    }
    result = build_quality_agent_graph(runner, agent_executor=agent_executor).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_quality_agent_graph_mermaid() -> str:
    """Render the QA Agent subgraph as Mermaid text."""

    class NoopRunner:
        def run(self, run_dir: Path) -> AgentRunResult:
            raise RuntimeError("Runner is not available in graph rendering.")

    return (
        build_quality_agent_graph(
            cast(FeatureQaRunner, NoopRunner()),
            agent_executor=cast(SpecialistAgentExecutor, object()),
        )
        .get_graph()
        .draw_mermaid()
    )


def _prepare_context(state: QualityAgentGraphState) -> QualityAgentGraphState:
    delivery_state = state["delivery_state"]
    if not _ordered_feature_queue(delivery_state):
        result = AgentRunResult(
            agent_id=QUALITY_AGENT_ID,
            status="qa_no_active_target",
            output_artifacts=[],
            summary="QA was requested, but no sprint work item or release target is available.",
            execution_id=str(delivery_state.get("agent_execution_id") or ""),
            recommended_next_action=(
                "Team Lead should send QA a concrete work item or release target."
            ),
        )
        return {**state, "feature_id": None, "result": result}

    active_feature = _active_feature(delivery_state)
    if active_feature is None:
        result = AgentRunResult(
            agent_id=QUALITY_AGENT_ID,
            status="qa_no_active_target",
            output_artifacts=[],
            summary="QA was requested, but all sprint work items appear complete.",
            execution_id=str(delivery_state.get("agent_execution_id") or ""),
            recommended_next_action=(
                "Team Lead should choose handoff, deployment, or a concrete QA target."
            ),
        )
        return {**state, "feature_id": None, "result": result}
    run_dir = Path(state["run_dir"])
    _write_quality_execution_request(run_dir, delivery_state, active_feature)
    update_execution_request_context(
        run_dir,
        execution_id=str(delivery_state.get("agent_execution_id") or ""),
        execution_intent=str(delivery_state.get("agent_execution_intent") or ""),
        parent_message_id=str(delivery_state.get("agent_call_message_id") or ""),
        codex_resume_thread_id=codex_resume_thread_id(delivery_state, QUALITY_CODEX_AGENT_ID),
        feature_queue=list(delivery_state.get("feature_queue", [])),
        active_feature=active_feature,
        completed_feature_ids=list(delivery_state.get("completed_feature_ids", [])),
    )
    return {**state, "feature_id": str(active_feature["id"])}


def _write_quality_execution_request(
    run_dir: Path,
    delivery_state: DeliveryState,
    active_feature: dict[str, Any],
) -> None:
    request = build_execution_request_payload(
        delivery_state,
        agent_id=QUALITY_AGENT_ID,
        model=(
            agent_env_value("QUALITY_CODEX_MODEL", delivery_state)
            or agent_env_value("AGENT_CODEX_MODEL", delivery_state)
            or "gpt-5.3-codex"
        ),
        input_artifacts=_quality_input_artifacts(delivery_state),
        expected_outputs=[
            f"08-qa-report-{active_feature['id']}.md",
            f"qa/results-{active_feature['id']}.json",
        ],
        instructions=[
            (
                "Read the current work item, upstream planning artifacts, "
                "implementation summary, and QA evidence before testing."
            ),
            (
                "Validate the assigned feature or release target against its "
                "acceptance criteria and definition of done."
            ),
            "Keep QA focused on evidence and do not perform implementation work.",
            "Return explicit artifact refs, test evidence, defects, and QA status.",
        ],
        constraints=[
            (
                "Do not change product code unless the upstream request explicitly asks "
                "for a QA-owned repair artifact."
            ),
            "Do not invent passing evidence; report blocked or failed when evidence is missing.",
            "Keep checks proportional to the assigned work item.",
        ],
        active_feature=active_feature,
        codex_resume_thread_id=codex_resume_thread_id(delivery_state, QUALITY_CODEX_AGENT_ID),
    )
    write_execution_request(run_dir, request)


def _quality_input_artifacts(delivery_state: DeliveryState) -> list[str]:
    paths = [
        "00-requirements.md",
        *[
            str(artifact.get("path"))
            for artifact in delivery_state.get("artifacts", [])
            if artifact.get("path") and "/codex/" not in str(artifact.get("path"))
        ],
    ]
    return _unique_paths(paths)


def _run_agent_executor(runner: FeatureQaRunner | None, agent_executor: SpecialistAgentExecutor):
    def run(state: QualityAgentGraphState) -> QualityAgentGraphState:
        feature_id = state.get("feature_id")
        if "feature_id" not in state:
            return state

        delivery_state = state["delivery_state"]
        run_dir = Path(state["run_dir"])
        event_log = run_dir / "events.jsonl"
        event_log.parent.mkdir(parents=True, exist_ok=True)
        event_data = {"feature_id": feature_id} if feature_id else {}
        write_event(event_log, delivery_state["run_id"], QUALITY_AGENT_ID, "qa_started", event_data)
        result = agent_executor.run(
            SpecialistAgentRequest(
                agent_id=QUALITY_AGENT_ID,
                agent_name="QA Agent",
                stage="qa",
                system_prompt=QUALITY_AGENT_SYSTEM_PROMPT,
                user_prompt=_quality_user_prompt(delivery_state, feature_id),
                runner=runner or QualityCodexRunner(),
                run_dir=run_dir,
                delivery_state=delivery_state,
            )
        )
        return {**state, "result": result, "status": _normalize_qa_status(result.status)}

    return run


def _apply_quality_result(state: QualityAgentGraphState) -> QualityAgentGraphState:
    result = state.get("result")
    feature_id = state.get("feature_id")
    if result is None:
        return state

    delivery_state = state["delivery_state"]
    status = state.get("status") or _normalize_qa_status(result.status)
    event_log = Path(state["run_dir"]) / "events.jsonl"
    artifact = (
        _primary_report_artifact(str(feature_id), result.output_artifacts)
        if feature_id
        else (result.output_artifacts[0] if result.output_artifacts else "08-qa-report.md")
    )
    event_data: dict[str, object] = {"artifact": artifact, "status": status}
    if feature_id:
        event_data["feature_id"] = feature_id
    write_event(
        event_log,
        delivery_state["run_id"],
        QUALITY_AGENT_ID,
        "artifact_written",
        event_data,
    )
    completion_data: dict[str, object] = {"status": status}
    if feature_id:
        completion_data["feature_id"] = feature_id
    write_event(
        event_log,
        delivery_state["run_id"],
        QUALITY_AGENT_ID,
        "qa_completed",
        completion_data,
    )

    updated = mark_node_completed(
        delivery_state,
        node_name=f"qa:{feature_id}" if feature_id else "qa",
        stage="qa",
        status=f"qa_{status}" if feature_id else result.status,
    )
    updated["qa_status"] = status
    extend_artifacts(
        updated,
        artifact_refs(
            result.output_artifacts,
            kind="qa",
            owner_agent=QUALITY_AGENT_ID,
        ),
    )
    append_downstream_response(updated, from_agent=QUALITY_AGENT_ID, result=result)

    if feature_id:
        if status == "passed":
            updated = _mark_feature_passed(updated, feature_id)
        else:
            updated = _mark_feature_failed(updated, feature_id)
    return {**state, "delivery_state": updated}


def _quality_user_prompt(state: DeliveryState, feature_id: str | None) -> str:
    return json.dumps(
        {
            "task": "Run the assigned QA Codex task.",
            "run_dir": state["run_dir"],
            "feature_id": feature_id,
            "deployment_status": state.get("deployment_status"),
            "public_url": state.get("public_url"),
            "agent_call_message_id": state.get("agent_call_message_id"),
            "agent_execution_id": state.get("agent_execution_id"),
        },
        indent=2,
        sort_keys=True,
    )


def _primary_report_artifact(feature_id: str, artifacts: list[str]) -> str:
    for artifact in artifacts:
        if artifact.lower().endswith(".md") and "qa-report" in artifact.lower():
            return artifact
    return artifacts[0] if artifacts else f"qa-report-{feature_id}.md"


def _normalize_qa_status(status: str) -> str:
    normalized = status.removeprefix("qa_").removeprefix("codex_")
    return "passed" if normalized == "passed" else "failed"


def _mark_feature_passed(state: DeliveryState, feature_id: str) -> DeliveryState:
    updated = {**state}
    completed = [*updated.get("completed_feature_ids", [])]
    if feature_id not in completed:
        completed.append(feature_id)
    updated["completed_feature_ids"] = completed
    feature_statuses = dict(updated.get("feature_statuses", {}))
    feature_statuses[feature_id] = "qa_passed"
    updated["feature_statuses"] = feature_statuses

    next_feature = _next_feature(updated)
    updated["active_feature_id"] = str(next_feature["id"]) if next_feature else None
    if next_feature:
        updated["status"] = "qa_feature_passed_next_feature_ready"
    else:
        updated["status"] = "feature_queue_qa_completed_deployment_ready"
    return updated


def _mark_feature_failed(state: DeliveryState, feature_id: str) -> DeliveryState:
    updated = {**state}
    attempts = dict(updated.get("feature_repair_attempts", {}))
    attempts[feature_id] = attempts.get(feature_id, 0) + 1
    updated["feature_repair_attempts"] = attempts
    signatures, repeated_signature = _record_failure_signature(updated, feature_id)
    if signatures:
        updated["feature_failure_signatures"] = signatures
    feature_statuses = dict(updated.get("feature_statuses", {}))
    feature_statuses[feature_id] = "qa_failed"
    updated["feature_statuses"] = feature_statuses
    updated["active_feature_id"] = feature_id

    if repeated_signature:
        updated["status"] = "qa_feature_failed_blocked"
        updated["blockers"] = [
            *updated.get("blockers", []),
            f"QA repeated the same failure signature for feature {feature_id}: "
            f"{repeated_signature}.",
        ]
    elif attempts[feature_id] >= updated.get("max_repair_attempts", 5):
        updated["status"] = "qa_feature_failed_blocked"
        updated["blockers"] = [
            *updated.get("blockers", []),
            f"QA failed feature {feature_id} after {attempts[feature_id]} attempts.",
        ]
    else:
        updated["status"] = "qa_feature_failed_repair_ready"
    return updated


def _record_failure_signature(
    state: DeliveryState,
    feature_id: str,
) -> tuple[dict[str, list[str]], str]:
    signature = _latest_failure_signature(Path(state["run_dir"]), feature_id)
    current = state.get("feature_failure_signatures", {})
    signatures = (
        {
            str(key): [str(item) for item in value]
            for key, value in current.items()
            if isinstance(value, list)
        }
        if isinstance(current, dict)
        else {}
    )
    if not signature:
        return signatures, ""
    seen = [*signatures.get(feature_id, []), signature]
    signatures[feature_id] = seen
    repeated = signature if seen.count(signature) >= 2 else ""
    return signatures, repeated


def _latest_failure_signature(run_dir: Path, feature_id: str) -> str:
    path = run_dir / f"10-fix-request-{feature_id}.json"
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("failure_signature") or "")


def _active_feature(state: DeliveryState) -> dict[str, Any] | None:
    active_feature_id = state.get("active_feature_id")
    for feature in _ordered_feature_queue(state):
        if feature.get("id") == active_feature_id:
            return feature
    return _next_feature(state)


def _next_feature(state: DeliveryState) -> dict[str, Any] | None:
    completed = set(state.get("completed_feature_ids", []))
    sprint_id = str(state.get("team_lead_sprint_id") or "sprint-01")
    for feature in features_for_sprint(state, sprint_id):
        if str(feature["id"]) not in completed:
            return feature
    return None


def _ordered_feature_queue(state: DeliveryState) -> list[dict[str, Any]]:
    feature_queue = list(state.get("feature_queue", []))
    return sorted(feature_queue, key=lambda feature: int(feature.get("delivery_order", 0)))


def _unique_paths(paths: list[str]) -> list[str]:
    unique: list[str] = []
    for path in paths:
        if path and path not in unique:
            unique.append(path)
    return unique
