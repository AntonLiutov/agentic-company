"""Internal LangGraph for the planning agent."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from agentic_company.agents.base import blocked_state, extend_artifacts
from agentic_company.platform.artifacts import artifact_ref
from agentic_company.platform.state import DeliveryState, mark_node_completed

PLANNING_AGENT_GRAPH_NODE_ORDER = [
    "prepare_context",
    "run_planning_pipeline",
    "apply_result",
]

PLANNING_ARTIFACTS = [
    ("01-intake-brief.json", "intake-agent"),
    ("02-project-classification.json", "project-classifier"),
    ("03-staffing-decision.json", "team-assembler-agent"),
    ("04-workflow-plan.json", "workflow-planner"),
    ("05-implementation-brief.md", "tech-lead-agent"),
    ("06-execution-request.json", "fullstack-agent"),
]


class PlanningRunnerLike(Protocol):
    """Runner contract used by the planning graph."""

    def __call__(
        self,
        requirements_path: Path,
        output_root: Path,
        run_id: str | None = None,
    ) -> Path:
        """Run planning and return the output directory."""


class PlanningAgentGraphState(TypedDict):
    """Internal state for the planning agent subgraph."""

    delivery_state: DeliveryState
    requirements_path: NotRequired[str]
    output_dir: NotRequired[str]


def build_planning_agent_graph(
    runner: PlanningRunnerLike,
    *,
    node_order: Sequence[str] | None = None,
):
    """Build the planning agent internal graph."""

    order = list(PLANNING_AGENT_GRAPH_NODE_ORDER if node_order is None else node_order)
    if not order:
        raise ValueError("Planning agent graph requires at least one node.")

    graph = StateGraph(PlanningAgentGraphState)
    node_map = {
        "prepare_context": _prepare_context,
        "run_planning_pipeline": _run_planning_pipeline(runner),
        "apply_result": _apply_result,
    }
    for name in order:
        graph.add_node(name, node_map[name])

    graph.add_edge(START, order[0])
    for current, next_node in zip(order, order[1:], strict=False):
        graph.add_edge(current, next_node)
    graph.add_edge(order[-1], END)
    return graph.compile()


def run_planning_agent_graph(
    delivery_state: DeliveryState,
    runner: PlanningRunnerLike,
) -> DeliveryState:
    """Run the planning agent subgraph and return updated delivery state."""

    graph_state: PlanningAgentGraphState = {"delivery_state": delivery_state}
    result = build_planning_agent_graph(runner).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_planning_agent_graph_mermaid() -> str:
    """Render the planning agent subgraph as Mermaid text."""

    def noop_runner(requirements_path: Path, output_root: Path, run_id: str | None = None) -> Path:
        raise RuntimeError("Runner is not available in graph rendering.")

    return build_planning_agent_graph(noop_runner).get_graph().draw_mermaid()


def _prepare_context(state: PlanningAgentGraphState) -> PlanningAgentGraphState:
    delivery_state = state["delivery_state"]
    requirements_path = delivery_state.get("requirements_path")
    if not requirements_path:
        blocked = blocked_state(
            delivery_state,
            node_name="planning",
            stage="planning",
            reason="requirements_path is required for planning.",
        )
        return {**state, "delivery_state": blocked}
    return {**state, "requirements_path": str(requirements_path)}


def _run_planning_pipeline(runner: PlanningRunnerLike):
    def run(state: PlanningAgentGraphState) -> PlanningAgentGraphState:
        delivery_state = state["delivery_state"]
        requirements_path = state.get("requirements_path")
        if not requirements_path:
            return state

        run_dir = Path(delivery_state["run_dir"])
        output_dir = runner(
            Path(requirements_path),
            run_dir.parent,
            run_id=delivery_state["run_id"],
        )
        return {**state, "output_dir": str(output_dir)}

    return run


def _apply_result(state: PlanningAgentGraphState) -> PlanningAgentGraphState:
    output_dir = state.get("output_dir")
    if not output_dir:
        return state

    delivery_state = state["delivery_state"]
    output_path = Path(output_dir)
    updated = mark_node_completed(delivery_state, node_name="planning", stage="planning")
    updated["run_dir"] = str(output_path)
    updated["target_project_dir"] = str(output_path / "generated-project")
    extend_artifacts(
        updated,
        [
            artifact_ref(path, kind="planning", owner_agent=owner_agent)
            for path, owner_agent in PLANNING_ARTIFACTS
        ],
    )
    return {**state, "delivery_state": updated}
