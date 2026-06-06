"""LangGraph shell for the Head AgentExecutor."""

from __future__ import annotations

from typing import Any, NotRequired, Protocol, TypedDict, cast

from agentic_company.agents.head.contracts import HEAD_TOOLS
from agentic_company.agents.head.executor import LangChainHeadExecutor
from agentic_company.agents.head.tools import (
    HeadExecutorResult,
    HeadWorkers,
    write_head_event,
    write_head_result,
)
from agentic_company.platform.agent_runtime import build_agent_executor_graph
from agentic_company.platform.runtime_db import materialize_planning_items
from agentic_company.platform.state import DeliveryState

HEAD_AGENT_GRAPH_NODE_ORDER: tuple[str, ...] = (
    "prepare_context",
    "run_agent_executor",
    "apply_result",
)


class HeadGraphState(TypedDict):
    """Internal Head Agent graph state."""

    delivery_state: DeliveryState
    history: NotRequired[list[dict[str, Any]]]
    max_steps: NotRequired[int]


class HeadExecutor(Protocol):
    """Tool-calling Head runtime boundary."""

    def run(
        self,
        *,
        delivery_state: DeliveryState,
        workers: HeadWorkers,
        max_steps: int,
    ) -> HeadExecutorResult:
        """Coordinate upstream planning through bounded specialist tools."""


def build_head_agent_graph(
    workers: HeadWorkers,
    *,
    executor: HeadExecutor | None = None,
    max_steps: int = 100,
):
    """Build the Head Agent graph with one AgentExecutor node."""

    return build_agent_executor_graph(
        HeadGraphState,
        prepare_node=_prepare_context(max_steps),
        run_agent_executor_node=_run_agent_executor(workers, executor),
        apply_result_node=_apply_result,
        node_order=HEAD_AGENT_GRAPH_NODE_ORDER,
    )


def run_head_agent_graph(
    delivery_state: DeliveryState,
    *,
    workers: HeadWorkers,
    executor: HeadExecutor | None = None,
    max_steps: int = 100,
) -> DeliveryState:
    """Run the Head Agent subgraph and return updated delivery state."""

    graph_state: HeadGraphState = {"delivery_state": delivery_state}
    result = build_head_agent_graph(
        workers,
        executor=executor,
        max_steps=max_steps,
    ).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_head_agent_graph_mermaid() -> str:
    """Render the Head Agent subgraph as Mermaid text."""

    def unavailable(state: DeliveryState) -> DeliveryState:
        raise RuntimeError("Worker is not available in graph rendering.")

    workers = HeadWorkers(
        business_analyst=unavailable,
        architect=unavailable,
        project_manager=unavailable,
        team_lead=unavailable,
    )
    return build_head_agent_graph(workers, executor=_NoopExecutor()).get_graph().draw_mermaid()


def _prepare_context(max_steps: int):
    def run(state: HeadGraphState) -> HeadGraphState:
        delivery_state = state["delivery_state"]
        updated = {**delivery_state}
        updated["stage"] = "head"
        updated["status"] = "head_planning_started"
        materialize_planning_items(str(updated["run_id"]))
        seeded = cast(DeliveryState, updated)
        write_head_event(
            seeded,
            "head_planning_started",
            {"available_tools": list(HEAD_TOOLS)},
        )
        return {
            **state,
            "delivery_state": seeded,
            "history": [],
            "max_steps": max_steps,
        }

    return run


def _run_agent_executor(
    workers: HeadWorkers,
    executor: HeadExecutor | None,
):
    def run(state: HeadGraphState) -> HeadGraphState:
        selected_executor = executor or LangChainHeadExecutor()
        result = selected_executor.run(
            delivery_state=state["delivery_state"],
            workers=workers,
            max_steps=int(state.get("max_steps", 100)),
        )
        return {**state, "delivery_state": result.delivery_state, "history": result.history}

    return run


def _apply_result(state: HeadGraphState) -> HeadGraphState:
    delivery_state = state["delivery_state"]
    history = list(state.get("history", []))
    write_head_result(delivery_state, history)
    return {**state, "delivery_state": delivery_state}


class _NoopExecutor:
    def run(
        self,
        *,
        delivery_state: DeliveryState,
        workers: HeadWorkers,
        max_steps: int,
    ) -> HeadExecutorResult:
        return HeadExecutorResult(delivery_state, [])
