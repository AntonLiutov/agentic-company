"""LangGraph shell for the Team Lead AgentExecutor."""

from __future__ import annotations

from typing import Any, NotRequired, Protocol, TypedDict, cast

from agentic_company.agents.team_lead.contracts import TEAM_LEAD_TOOLS
from agentic_company.agents.team_lead.executor import LangChainTeamLeadExecutor
from agentic_company.agents.team_lead.tools import (
    TeamLeadExecutorResult,
    TeamLeadWorkers,
    apply_team_lead_result,
    checkpoint_delivery_state,
    write_sprint_plan_artifact,
    write_team_lead_event,
)
from agentic_company.platform.agent_runtime import build_agent_executor_graph
from agentic_company.platform.state import DeliveryState

TEAM_LEAD_AGENT_GRAPH_NODE_ORDER: tuple[str, ...] = (
    "prepare_sprint",
    "run_agent_executor",
    "apply_result",
)


class TeamLeadGraphState(TypedDict):
    """Internal Team Lead graph state."""

    delivery_state: DeliveryState
    sprint: NotRequired[dict[str, Any]]
    sprint_id: NotRequired[str]
    history: NotRequired[list[dict[str, Any]]]
    max_steps: NotRequired[int]


class TeamLeadExecutor(Protocol):
    """Tool-calling Team Lead runtime boundary."""

    def run(
        self,
        *,
        delivery_state: DeliveryState,
        sprint: dict[str, Any],
        workers: TeamLeadWorkers,
        max_steps: int,
    ) -> TeamLeadExecutorResult:
        """Execute the sprint through bounded specialist tools."""


def build_team_lead_agent_graph(
    workers: TeamLeadWorkers,
    *,
    executor: TeamLeadExecutor | None = None,
    max_steps: int = 100,
):
    """Build the Team Lead graph with one real AgentExecutor node."""

    return build_agent_executor_graph(
        TeamLeadGraphState,
        prepare_node=_prepare_sprint(max_steps),
        run_agent_executor_node=_run_agent_executor(workers, executor),
        apply_result_node=_apply_result,
        node_order=TEAM_LEAD_AGENT_GRAPH_NODE_ORDER,
    )


def run_team_lead_agent_graph(
    delivery_state: DeliveryState,
    *,
    workers: TeamLeadWorkers,
    executor: TeamLeadExecutor | None = None,
    max_steps: int = 100,
) -> DeliveryState:
    graph_state: TeamLeadGraphState = {"delivery_state": delivery_state}
    result = build_team_lead_agent_graph(
        workers,
        executor=executor,
        max_steps=max_steps,
    ).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_team_lead_agent_graph_mermaid() -> str:
    """Render the Team Lead agent subgraph as Mermaid text."""

    def unavailable(state: DeliveryState) -> DeliveryState:
        raise RuntimeError("Worker is not available in graph rendering.")

    workers = TeamLeadWorkers(
        fullstack=unavailable,
        qa=unavailable,
        deployment=unavailable,
        handoff=unavailable,
    )
    return (
        build_team_lead_agent_graph(
            workers,
            executor=_NoopExecutor(),
        )
        .get_graph()
        .draw_mermaid()
    )


def _prepare_sprint(max_steps: int):
    def run(state: TeamLeadGraphState) -> TeamLeadGraphState:
        delivery_state = state["delivery_state"]
        sprint_id = str(delivery_state.get("team_lead_sprint_id") or "sprint-01")
        sprint = {"sprint_id": sprint_id, "id": sprint_id, "features": []}
        updated = {**delivery_state}
        updated["stage"] = "team_lead"
        updated["status"] = "team_lead_sprint_started"
        updated["team_lead_sprint_id"] = sprint_id
        write_team_lead_event(
            updated,
            "team_lead_sprint_started",
            {"sprint_id": sprint_id, "available_tools": list(TEAM_LEAD_TOOLS)},
        )
        write_sprint_plan_artifact(updated, sprint_id, sprint)
        checkpoint_delivery_state(updated)
        return {
            **state,
            "delivery_state": cast(DeliveryState, updated),
            "sprint": sprint,
            "sprint_id": sprint_id,
            "history": [],
            "max_steps": max_steps,
        }

    return run


def _run_agent_executor(
    workers: TeamLeadWorkers,
    executor: TeamLeadExecutor | None,
):
    def run(state: TeamLeadGraphState) -> TeamLeadGraphState:
        selected_executor = executor or LangChainTeamLeadExecutor()
        result = selected_executor.run(
            delivery_state=state["delivery_state"],
            sprint=state.get("sprint", {}),
            workers=workers,
            max_steps=int(state.get("max_steps", 100)),
        )
        return {**state, "delivery_state": result.delivery_state, "history": result.history}

    return run


def _apply_result(state: TeamLeadGraphState) -> TeamLeadGraphState:
    delivery_state = apply_team_lead_result(
        state["delivery_state"],
        str(state.get("sprint_id", "sprint-01")),
    )
    return {**state, "delivery_state": delivery_state}


class _NoopExecutor:
    def run(
        self,
        *,
        delivery_state: DeliveryState,
        sprint: dict[str, Any],
        workers: TeamLeadWorkers,
        max_steps: int,
    ) -> TeamLeadExecutorResult:
        return TeamLeadExecutorResult(delivery_state, [])
