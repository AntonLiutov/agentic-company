"""Internal LangGraph for the Business Analyst agent."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, cast

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
from agentic_company.platform.events import write_event
from agentic_company.platform.messages import render_incoming_messages_for_prompt
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import (
    DeliveryState,
    codex_resume_thread_id,
    mark_node_completed,
)

BUSINESS_ANALYST_AGENT_ID = "business-analyst-agent"
BUSINESS_ANALYST_AGENT_GRAPH_NODE_ORDER = AGENT_EXECUTOR_GRAPH_NODE_ORDER
BUSINESS_ANALYSIS_DIR = "upstream-planning"
BUSINESS_ANALYSIS_REQUEST = f"{BUSINESS_ANALYSIS_DIR}/business-analysis-request.json"
BUSINESS_ANALYSIS_MD = f"{BUSINESS_ANALYSIS_DIR}/business-analysis.md"
BUSINESS_ANALYSIS_JSON = f"{BUSINESS_ANALYSIS_DIR}/business-analysis.json"
DEFAULT_BUSINESS_ANALYST_MODEL = "gpt-5.5"

BUSINESS_ANALYST_AGENT_SYSTEM_PROMPT = """
You are the Business Analyst Agent for agentic-company.

You own business analysis only through the available tools. Call `codex_exec`
to run the Codex Business Analyst worker for the assigned requirements.
Do not claim analysis is complete without calling a tool.
""".strip()


class BusinessAnalystRunnerLike(Protocol):
    def run(self, run_dir: Path) -> AgentRunResult:
        """Run the Business Analyst backend."""


class BusinessAnalystAgentGraphState(TypedDict):
    """Internal state for the Business Analyst agent subgraph."""

    run_dir: str
    delivery_state: DeliveryState
    result: NotRequired[AgentRunResult]


def build_business_analyst_agent_graph(
    runner: BusinessAnalystRunnerLike,
    *,
    agent_executor: SpecialistAgentExecutor,
    node_order: Sequence[str] | None = None,
):
    """Build the Business Analyst agent internal graph."""

    order = list(BUSINESS_ANALYST_AGENT_GRAPH_NODE_ORDER if node_order is None else node_order)
    if not order:
        raise ValueError("Business Analyst agent graph requires at least one node.")

    node_map = {
        "prepare_context": _prepare_context,
        "run_agent_executor": _run_agent_executor(runner, agent_executor),
        "apply_result": _apply_result,
    }
    return build_agent_executor_graph(
        BusinessAnalystAgentGraphState,
        prepare_node=node_map[order[0]],
        run_agent_executor_node=node_map[order[1]],
        apply_result_node=node_map[order[2]],
        node_order=tuple(order),
    )


def run_business_analyst_agent_graph(
    delivery_state: DeliveryState,
    runner: BusinessAnalystRunnerLike,
    agent_executor: SpecialistAgentExecutor,
) -> DeliveryState:
    """Run the Business Analyst agent subgraph and return updated delivery state."""

    graph_state: BusinessAnalystAgentGraphState = {
        "delivery_state": delivery_state,
        "run_dir": delivery_state["run_dir"],
    }
    result = build_business_analyst_agent_graph(
        runner,
        agent_executor=agent_executor,
    ).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_business_analyst_agent_graph_mermaid() -> str:
    """Render the Business Analyst subgraph as Mermaid text."""

    class NoopRunner:
        def run(self, run_dir: Path) -> AgentRunResult:
            raise RuntimeError("Runner is not available in graph rendering.")

    return (
        build_business_analyst_agent_graph(
            cast(BusinessAnalystRunnerLike, NoopRunner()),
            agent_executor=cast(SpecialistAgentExecutor, object()),
        )
        .get_graph()
        .draw_mermaid()
    )


def _prepare_context(state: BusinessAnalystAgentGraphState) -> BusinessAnalystAgentGraphState:
    delivery_state = state["delivery_state"]
    run_dir = Path(state["run_dir"])
    requirements_path = _requirements_path(run_dir, delivery_state)
    if not requirements_path.exists():
        result = AgentRunResult(
            agent_id=BUSINESS_ANALYST_AGENT_ID,
            status="business_analysis_blocked",
            output_artifacts=[],
            summary="Business analysis requires 00-requirements.md.",
        )
        return {**state, "result": result}

    request = _business_analysis_request(run_dir, requirements_path, delivery_state)
    request_path = run_dir / BUSINESS_ANALYSIS_REQUEST
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_event(
        run_dir / "events.jsonl",
        delivery_state["run_id"],
        BUSINESS_ANALYST_AGENT_ID,
        "business_analysis_started",
        {"artifact": BUSINESS_ANALYSIS_REQUEST},
    )
    return state


def _run_agent_executor(
    runner: BusinessAnalystRunnerLike,
    agent_executor: SpecialistAgentExecutor,
):
    def run(state: BusinessAnalystAgentGraphState) -> BusinessAnalystAgentGraphState:
        if "result" in state:
            return state
        result = agent_executor.run(
            SpecialistAgentRequest(
                agent_id=BUSINESS_ANALYST_AGENT_ID,
                agent_name="Business Analyst Agent",
                stage="business_analysis",
                system_prompt=BUSINESS_ANALYST_AGENT_SYSTEM_PROMPT,
                user_prompt=_business_analyst_user_prompt(state["delivery_state"]),
                runner=runner,
                run_dir=Path(state["run_dir"]),
                delivery_state=state["delivery_state"],
            )
        )
        return {**state, "result": result}

    return run


def _business_analyst_user_prompt(state: DeliveryState) -> str:
    return json.dumps(
        {
            "task": "Run business analysis for the raw product requirements.",
            "run_dir": state["run_dir"],
            "requirements_path": state.get("requirements_path"),
            "expected_outputs": [BUSINESS_ANALYSIS_MD, BUSINESS_ANALYSIS_JSON],
        },
        indent=2,
        sort_keys=True,
    )


def _apply_result(state: BusinessAnalystAgentGraphState) -> BusinessAnalystAgentGraphState:
    if "result" not in state:
        raise ValueError("Business Analyst agent graph result is missing.")

    delivery_state = state["delivery_state"]
    result = state["result"]
    updated = mark_node_completed(
        delivery_state,
        node_name="business_analyst",
        stage="business_analysis",
        status=result.status,
    )
    extend_artifacts(
        updated,
        artifact_refs(
            result.output_artifacts,
            kind="planning",
            owner_agent=result.agent_id or BUSINESS_ANALYST_AGENT_ID,
        ),
    )
    append_downstream_response(updated, from_agent=BUSINESS_ANALYST_AGENT_ID, result=result)
    if result.status != "business_analysis_completed":
        updated["blockers"] = [*updated.get("blockers", []), result.summary]
    write_event(
        Path(updated["run_dir"]) / "events.jsonl",
        updated["run_id"],
        BUSINESS_ANALYST_AGENT_ID,
        (
            "business_analysis_completed"
            if result.status == "business_analysis_completed"
            else "business_analysis_blocked"
        ),
        {"status": result.status, "artifacts": result.output_artifacts},
    )
    return {**state, "delivery_state": updated}


def _requirements_path(run_dir: Path, state: DeliveryState) -> Path:
    configured = state.get("requirements_path")
    if configured:
        return Path(str(configured))
    return run_dir / "00-requirements.md"


def _business_analysis_request(
    run_dir: Path,
    requirements_path: Path,
    state: DeliveryState,
) -> dict[str, object]:
    model = (
        agent_env_value("BUSINESS_ANALYST_CODEX_MODEL", state)
        or agent_env_value("AGENT_CODEX_MODEL", state)
        or DEFAULT_BUSINESS_ANALYST_MODEL
    )
    return {
        "run_id": state["run_id"],
        "agent_id": BUSINESS_ANALYST_AGENT_ID,
        "model": model,
        "requirements_artifact": requirements_path.relative_to(run_dir).as_posix()
        if requirements_path.is_relative_to(run_dir)
        else str(requirements_path),
        "expected_outputs": [BUSINESS_ANALYSIS_MD, BUSINESS_ANALYSIS_JSON],
        "codex_resume_thread_id": codex_resume_thread_id(state, BUSINESS_ANALYST_AGENT_ID),
        "available_agents": _available_agent_descriptors(),
        "incoming_messages": render_incoming_messages_for_prompt(
            run_dir,
            to_agent=BUSINESS_ANALYST_AGENT_ID,
        ),
    }


def _available_agent_descriptors() -> list[dict[str, str]]:
    from agentic_company.agents.registry import active_agents

    return [
        {
            "agent_id": descriptor.agent_id,
            "name": descriptor.name,
            "stage": descriptor.stage,
            "family": descriptor.family,
            "runtime": descriptor.runtime,
        }
        for descriptor in active_agents()
    ]
