"""Internal LangGraph for the QA Agent."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, NotRequired, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from agentic_company.agents.base import artifact_refs, extend_artifacts
from agentic_company.agents.quality.codex_cli import QualityCodexRunner
from agentic_company.platform.events import write_event
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import DeliveryState, mark_node_completed

QUALITY_AGENT_ID = "qa-agent"

QUALITY_AGENT_GRAPH_NODE_ORDER: tuple[str, ...] = (
    "prepare_context",
    "codex_quality_execution",
    "parse_quality_contract",
    "apply_quality_result",
)


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

    graph = StateGraph(QualityAgentGraphState)
    node_map = {
        "prepare_context": _prepare_context,
        "codex_quality_execution": _codex_quality_execution(runner),
        "parse_quality_contract": _parse_quality_contract,
        "apply_quality_result": _apply_quality_result,
    }
    for name in order:
        graph.add_node(name, node_map[name])

    graph.add_edge(START, order[0])
    for current, next_node in zip(order, order[1:], strict=False):
        graph.add_edge(current, next_node)
    graph.add_edge(order[-1], END)
    return graph.compile()


def run_quality_agent_graph(
    delivery_state: DeliveryState,
    *,
    runner: FeatureQaRunner | None = None,
) -> DeliveryState:
    """Run the QA Agent subgraph and return updated delivery state."""

    graph_state: QualityAgentGraphState = {
        "delivery_state": delivery_state,
        "run_dir": delivery_state["run_dir"],
    }
    result = build_quality_agent_graph(runner).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_quality_agent_graph_mermaid() -> str:
    """Render the QA Agent subgraph as Mermaid text."""

    class NoopRunner:
        def run(self, run_dir: Path) -> AgentRunResult:
            raise RuntimeError("Runner is not available in graph rendering.")

    return build_quality_agent_graph(cast(FeatureQaRunner, NoopRunner())).get_graph().draw_mermaid()


def _prepare_context(state: QualityAgentGraphState) -> QualityAgentGraphState:
    delivery_state = state["delivery_state"]
    if not _ordered_feature_queue(delivery_state):
        return {**state, "feature_id": None}

    active_feature = _active_feature(delivery_state)
    if active_feature is None:
        return {**state, "delivery_state": _pause_after_all_features(delivery_state)}
    return {**state, "feature_id": str(active_feature["id"])}


def _codex_quality_execution(runner: FeatureQaRunner | None):
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
        result = (runner or QualityCodexRunner()).run(run_dir)
        return {**state, "result": result}

    return run


def _parse_quality_contract(state: QualityAgentGraphState) -> QualityAgentGraphState:
    result = state.get("result")
    if result is None:
        return state
    return {**state, "status": _normalize_qa_status(result.status)}


def _apply_quality_result(state: QualityAgentGraphState) -> QualityAgentGraphState:
    result = state.get("result")
    feature_id = state.get("feature_id")
    if result is None or "feature_id" not in state:
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

    if feature_id:
        if status == "passed":
            updated = _mark_feature_passed(updated, feature_id)
        else:
            updated = _mark_feature_failed(updated, feature_id)
    return {**state, "delivery_state": updated}


def _primary_report_artifact(feature_id: str, artifacts: list[str]) -> str:
    expected = f"08-qa-report-{feature_id}.md"
    return expected if expected in artifacts else (artifacts[0] if artifacts else expected)


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
    feature_statuses = dict(updated.get("feature_statuses", {}))
    feature_statuses[feature_id] = "qa_failed"
    updated["feature_statuses"] = feature_statuses
    updated["active_feature_id"] = feature_id

    if attempts[feature_id] >= updated.get("max_repair_attempts", 3):
        updated["status"] = "qa_feature_failed_blocked"
        updated["blockers"] = [
            *updated.get("blockers", []),
            f"QA failed feature {feature_id} after {attempts[feature_id]} attempts.",
        ]
    else:
        updated["status"] = "qa_feature_failed_repair_ready"
    return updated


def _pause_after_all_features(state: DeliveryState) -> DeliveryState:
    updated = mark_node_completed(
        state,
        node_name="qa",
        stage="qa",
        status="feature_queue_qa_completed_deployment_ready",
    )
    updated["status"] = "feature_queue_qa_completed_deployment_ready"
    updated["qa_status"] = "passed"
    return updated


def _active_feature(state: DeliveryState) -> dict[str, Any] | None:
    active_feature_id = state.get("active_feature_id")
    for feature in _ordered_feature_queue(state):
        if feature.get("id") == active_feature_id:
            return feature
    return _next_feature(state)


def _next_feature(state: DeliveryState) -> dict[str, Any] | None:
    completed = set(state.get("completed_feature_ids", []))
    for feature in _ordered_feature_queue(state):
        if str(feature["id"]) not in completed:
            return feature
    return None


def _ordered_feature_queue(state: DeliveryState) -> list[dict[str, Any]]:
    feature_queue = list(state.get("feature_queue", []))
    return sorted(feature_queue, key=lambda feature: int(feature.get("delivery_order", 0)))
