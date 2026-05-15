"""Internal LangGraph for the Handoff Agent."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from agentic_company.agents.base import artifact_refs, extend_artifacts
from agentic_company.agents.handoff.codex_cli import HandoffCodexRunner
from agentic_company.platform.events import write_event
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import DeliveryState, mark_node_completed

HANDOFF_AGENT_ID = "documentation-handoff-agent"

HANDOFF_AGENT_GRAPH_NODE_ORDER: tuple[str, ...] = (
    "prepare_context",
    "codex_handoff_execution",
    "parse_handoff_contract",
    "apply_handoff_result",
)


class HandoffRunner(Protocol):
    """Codex-owned handoff execution boundary."""

    def run(self, run_dir: Path) -> AgentRunResult:
        """Run handoff and return the parsed handoff result."""


class HandoffAgentGraphState(TypedDict):
    """Internal state for the Handoff Agent subgraph."""

    delivery_state: DeliveryState
    run_dir: str
    result: NotRequired[AgentRunResult]
    status: NotRequired[str]


def build_handoff_agent_graph(
    runner: HandoffRunner | None = None,
    *,
    node_order: Sequence[str] | None = None,
):
    """Build the Handoff Agent internal graph.

    The graph does not render report sections or stakeholder copy. Those choices
    belong to the Codex Handoff specialist inside `codex_handoff_execution`.
    """

    order = list(HANDOFF_AGENT_GRAPH_NODE_ORDER if node_order is None else node_order)
    if not order:
        raise ValueError("Handoff agent graph requires at least one node.")

    graph = StateGraph(HandoffAgentGraphState)
    node_map = {
        "prepare_context": _prepare_context,
        "codex_handoff_execution": _codex_handoff_execution(runner),
        "parse_handoff_contract": _parse_handoff_contract,
        "apply_handoff_result": _apply_handoff_result,
    }
    for name in order:
        graph.add_node(name, node_map[name])

    graph.add_edge(START, order[0])
    for current, next_node in zip(order, order[1:], strict=False):
        graph.add_edge(current, next_node)
    graph.add_edge(order[-1], END)
    return graph.compile()


def run_handoff_agent_graph(
    delivery_state: DeliveryState,
    *,
    runner: HandoffRunner | None = None,
) -> DeliveryState:
    """Run the Handoff Agent subgraph and return updated delivery state."""

    graph_state: HandoffAgentGraphState = {
        "delivery_state": delivery_state,
        "run_dir": delivery_state["run_dir"],
    }
    result = build_handoff_agent_graph(runner).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_handoff_agent_graph_mermaid() -> str:
    """Render the Handoff Agent subgraph as Mermaid text."""

    class NoopRunner:
        def run(self, run_dir: Path) -> AgentRunResult:
            raise RuntimeError("Runner is not available in graph rendering.")

    return build_handoff_agent_graph(cast(HandoffRunner, NoopRunner())).get_graph().draw_mermaid()


def _prepare_context(state: HandoffAgentGraphState) -> HandoffAgentGraphState:
    delivery_state = state["delivery_state"]
    event_log = Path(state["run_dir"]) / "events.jsonl"
    event_log.parent.mkdir(parents=True, exist_ok=True)
    write_event(
        event_log,
        delivery_state["run_id"],
        HANDOFF_AGENT_ID,
        "handoff_started",
        {"deployment_status": delivery_state.get("deployment_status")},
    )
    return state


def _codex_handoff_execution(runner: HandoffRunner | None):
    def run(state: HandoffAgentGraphState) -> HandoffAgentGraphState:
        result = (runner or HandoffCodexRunner()).run(Path(state["run_dir"]))
        return {**state, "result": result}

    return run


def _parse_handoff_contract(state: HandoffAgentGraphState) -> HandoffAgentGraphState:
    result = state.get("result")
    if result is None:
        return state
    return {**state, "status": _normalize_handoff_status(result.status)}


def _apply_handoff_result(state: HandoffAgentGraphState) -> HandoffAgentGraphState:
    result = state.get("result")
    if result is None:
        raise ValueError("Handoff agent graph result is missing.")

    status = state.get("status") or _normalize_handoff_status(result.status)
    delivery_state = state["delivery_state"]
    event_log = Path(state["run_dir"]) / "events.jsonl"
    primary_artifact = (
        result.output_artifacts[0] if result.output_artifacts else "09-handoff-summary.md"
    )
    write_event(
        event_log,
        delivery_state["run_id"],
        HANDOFF_AGENT_ID,
        "artifact_written",
        {"artifact": primary_artifact, "status": status},
    )
    write_event(
        event_log,
        delivery_state["run_id"],
        HANDOFF_AGENT_ID,
        "handoff_completed",
        {"artifact": primary_artifact, "status": status},
    )

    updated = mark_node_completed(
        delivery_state,
        node_name="handoff",
        stage="handoff",
        status=f"handoff_{status}",
    )
    extend_artifacts(
        updated,
        artifact_refs(result.output_artifacts, kind="handoff", owner_agent=result.agent_id),
    )
    return {**state, "delivery_state": updated}


def _normalize_handoff_status(status: str) -> str:
    normalized = status.removeprefix("handoff_").removeprefix("codex_")
    return normalized if normalized in {"ready", "blocked", "failed", "unknown"} else "unknown"
