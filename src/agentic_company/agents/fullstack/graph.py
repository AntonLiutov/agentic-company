"""Internal LangGraph for the fullstack agent."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NotRequired, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from agentic_company.agents.base import artifact_refs, extend_artifacts
from agentic_company.platform.artifacts import load_execution_request
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import DeliveryState, mark_node_completed

FULLSTACK_AGENT_GRAPH_NODE_ORDER = [
    "prepare_context",
    "run_codex",
    "apply_result",
]


class FullstackRunnerLike(Protocol):
    """Runner contract used by the fullstack graph."""

    def run(self, run_dir: Path) -> AgentRunResult:
        """Run an implementation backend."""


class FullstackAgentGraphState(TypedDict):
    """Internal state for the fullstack agent subgraph."""

    run_dir: str
    delivery_state: DeliveryState
    result: NotRequired[AgentRunResult]
    results: NotRequired[list[AgentRunResult]]


def build_fullstack_agent_graph(
    runner: FullstackRunnerLike,
    *,
    node_order: Sequence[str] | None = None,
):
    """Build the fullstack agent internal graph."""

    order = list(FULLSTACK_AGENT_GRAPH_NODE_ORDER if node_order is None else node_order)
    if not order:
        raise ValueError("Fullstack agent graph requires at least one node.")

    graph = StateGraph(FullstackAgentGraphState)
    node_map = {
        "prepare_context": _prepare_context,
        "run_codex": _run_codex(runner),
        "apply_result": _apply_result,
    }
    for name in order:
        graph.add_node(name, node_map[name])

    graph.add_edge(START, order[0])
    for current, next_node in zip(order, order[1:], strict=False):
        graph.add_edge(current, next_node)
    graph.add_edge(order[-1], END)
    return graph.compile()


def run_fullstack_agent_graph(
    delivery_state: DeliveryState,
    runner: FullstackRunnerLike,
) -> DeliveryState:
    """Run the fullstack agent subgraph and return updated delivery state."""

    graph_state: FullstackAgentGraphState = {
        "delivery_state": delivery_state,
        "run_dir": delivery_state["run_dir"],
    }
    result = build_fullstack_agent_graph(runner).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_fullstack_agent_graph_mermaid() -> str:
    """Render the fullstack agent subgraph as Mermaid text."""

    class NoopRunner:
        def run(self, run_dir: Path) -> AgentRunResult:
            raise RuntimeError("Runner is not available in graph rendering.")

    return (
        build_fullstack_agent_graph(cast(FullstackRunnerLike, NoopRunner()))
        .get_graph()
        .draw_mermaid()
    )


def _prepare_context(state: FullstackAgentGraphState) -> FullstackAgentGraphState:
    return state


def _run_codex(runner: FullstackRunnerLike):
    def run(state: FullstackAgentGraphState) -> FullstackAgentGraphState:
        delivery_state = state["delivery_state"]
        feature_queue = _ordered_feature_queue(delivery_state)
        if delivery_state.get("project_archetype") != "api-web-compose" or not feature_queue:
            result = runner.run(Path(state["run_dir"]))
            return {**state, "result": result, "results": [result]}

        run_dir = Path(state["run_dir"])
        base_request = load_execution_request(run_dir)
        active_feature = _active_feature(delivery_state)
        updated_delivery_state = {**delivery_state}
        if active_feature is None:
            result = AgentRunResult(
                agent_id=base_request.agent_id,
                status="already_completed",
                output_artifacts=[],
                summary="No active Fullstack feature to execute.",
            )
            return {
                **state,
                "delivery_state": cast(DeliveryState, updated_delivery_state),
                "result": result,
                "results": [result],
            }

        completed_feature_ids = list(delivery_state.get("completed_feature_ids", []))
        updated_delivery_state["active_feature_id"] = str(active_feature["id"])
        _write_feature_execution_request(
            run_dir,
            base_request,
            active_feature,
            completed_feature_ids,
        )
        result = runner.run(run_dir)
        return {
            **state,
            "delivery_state": cast(DeliveryState, updated_delivery_state),
            "result": result,
            "results": [result],
        }

    return run


def _apply_result(state: FullstackAgentGraphState) -> FullstackAgentGraphState:
    results = state.get("results") or ([state["result"]] if "result" in state else [])
    if not results:
        raise ValueError("Fullstack agent graph result is missing.")

    delivery_state = state["delivery_state"]
    result = results[-1]
    updated = mark_node_completed(
        delivery_state,
        node_name="fullstack",
        stage="fullstack",
        status=result.status,
    )
    feature_queue = _ordered_feature_queue(updated)
    if updated.get("project_archetype") == "api-web-compose" and feature_queue:
        active_feature_id = updated.get("active_feature_id")
        if result.status in {"codex_completed", "already_completed"} and active_feature_id:
            feature_statuses = dict(updated.get("feature_statuses", {}))
            feature_statuses[str(active_feature_id)] = "implemented"
            updated["feature_statuses"] = feature_statuses
            updated["status"] = "fullstack_feature_implemented"
        elif result.status not in {"codex_completed", "already_completed"}:
            updated["status"] = "fullstack_feature_failed"
            updated["blockers"] = [
                *updated.get("blockers", []),
                (
                    f"Fullstack feature {updated.get('active_feature_id') or 'unknown'} "
                    "did not complete successfully."
                ),
            ]
    extend_artifacts(
        updated,
        artifact_refs(
            [artifact for item in results for artifact in item.output_artifacts],
            kind="execution",
            owner_agent=result.agent_id,
        ),
    )
    return {**state, "delivery_state": updated}


def _ordered_feature_queue(state: DeliveryState) -> list[dict[str, Any]]:
    feature_queue = list(state.get("feature_queue", []))
    return sorted(feature_queue, key=lambda feature: int(feature.get("delivery_order", 0)))


def _active_feature(state: DeliveryState) -> dict[str, Any] | None:
    active_feature_id = state.get("active_feature_id")
    feature_queue = _ordered_feature_queue(state)
    if active_feature_id:
        for feature in feature_queue:
            if feature.get("id") == active_feature_id:
                return feature
    completed = set(state.get("completed_feature_ids", []))
    for feature in feature_queue:
        if str(feature["id"]) not in completed:
            return feature
    return None


def _write_feature_execution_request(
    run_dir: Path,
    base_request,
    feature: dict[str, Any],
    completed_feature_ids: list[str],
) -> None:
    feature_id = str(feature["id"])
    fix_request_artifacts = _feature_fix_request_artifacts(run_dir, feature_id)
    request = {
        **base_request.to_dict(),
        "active_feature": feature,
        "completed_feature_ids": completed_feature_ids,
        "input_artifacts": [
            *base_request.input_artifacts,
            *fix_request_artifacts,
        ],
        "instructions": [
            *base_request.instructions,
            (
                f"Active feature for this run: {feature_id} - {feature['title']}. "
                "Implement only this active feature in this Codex run."
            ),
            (
                "Acceptance criteria for the active feature: "
                + "; ".join(str(item) for item in feature.get("acceptance_criteria", []))
            ),
            (
                "Already completed features before this run: "
                + (", ".join(completed_feature_ids) if completed_feature_ids else "none")
                + ". Preserve their behavior."
            ),
            *(
                [
                    (
                        "This is a repair run. Read the QA fix request and evidence "
                        f"before editing: {', '.join(fix_request_artifacts)}."
                    )
                ]
                if fix_request_artifacts
                else []
            ),
        ],
    }
    (run_dir / "06-execution-request.json").write_text(
        json.dumps(request, indent=2) + "\n",
        encoding="utf-8",
    )


def _feature_fix_request_artifacts(run_dir: Path, feature_id: str) -> list[str]:
    return [
        path.name
        for path in [
            run_dir / f"10-fix-request-{feature_id}.md",
            run_dir / f"10-fix-request-{feature_id}.json",
        ]
        if path.exists()
    ]
