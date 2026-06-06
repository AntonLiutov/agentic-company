"""Internal LangGraph for the Architect agent."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, cast

from agentic_company.integrations.codex import DEFAULT_CODEX_MODEL
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
from agentic_company.platform.tool_contracts import WorkItemExecutionPacket

ARCHITECT_AGENT_ID = "architect-agent"
ARCHITECT_AGENT_GRAPH_NODE_ORDER = AGENT_EXECUTOR_GRAPH_NODE_ORDER
ARCHITECTURE_DIR = "upstream-planning"
ARCHITECTURE_REQUEST = f"{ARCHITECTURE_DIR}/architecture-request.json"
ARCHITECTURE_MD = f"{ARCHITECTURE_DIR}/architecture.md"
ARCHITECTURE_JSON = f"{ARCHITECTURE_DIR}/architecture.json"
ARCHITECTURE_MMD = f"{ARCHITECTURE_DIR}/architecture.mmd"
BUSINESS_ANALYSIS_MD = f"{ARCHITECTURE_DIR}/business-analysis.md"
BUSINESS_ANALYSIS_JSON = f"{ARCHITECTURE_DIR}/business-analysis.json"
DEFAULT_ARCHITECT_MODEL = DEFAULT_CODEX_MODEL

ARCHITECT_AGENT_SYSTEM_PROMPT = """
You are the Architect Agent for agentic-company.

You own solution architecture only through the available tools. Call `codex_exec`
to run the Codex Architect worker for the current BA artifacts.
Do not claim architecture is complete without calling a tool.
""".strip()


class ArchitectRunnerLike(Protocol):
    def run(self, run_dir: Path) -> AgentRunResult:
        """Run the Architect backend."""


class ArchitectAgentGraphState(TypedDict):
    """Internal state for the Architect agent subgraph."""

    run_dir: str
    delivery_state: DeliveryState
    result: NotRequired[AgentRunResult]


def build_architect_agent_graph(
    runner: ArchitectRunnerLike,
    *,
    agent_executor: SpecialistAgentExecutor,
    node_order: Sequence[str] | None = None,
):
    """Build the Architect agent internal graph."""

    order = list(ARCHITECT_AGENT_GRAPH_NODE_ORDER if node_order is None else node_order)
    if not order:
        raise ValueError("Architect agent graph requires at least one node.")

    node_map = {
        "prepare_context": _prepare_context,
        "run_agent_executor": _run_agent_executor(runner, agent_executor),
        "apply_result": _apply_result,
    }
    return build_agent_executor_graph(
        ArchitectAgentGraphState,
        prepare_node=node_map[order[0]],
        run_agent_executor_node=node_map[order[1]],
        apply_result_node=node_map[order[2]],
        node_order=tuple(order),
    )


def run_architect_agent_graph(
    delivery_state: DeliveryState,
    runner: ArchitectRunnerLike,
    agent_executor: SpecialistAgentExecutor,
) -> DeliveryState:
    """Run the Architect agent subgraph and return updated delivery state."""

    graph_state: ArchitectAgentGraphState = {
        "delivery_state": delivery_state,
        "run_dir": delivery_state["run_dir"],
    }
    result = build_architect_agent_graph(
        runner,
        agent_executor=agent_executor,
    ).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_architect_agent_graph_mermaid() -> str:
    """Render the Architect subgraph as Mermaid text."""

    class NoopRunner:
        def run(self, run_dir: Path) -> AgentRunResult:
            raise RuntimeError("Runner is not available in graph rendering.")

    return (
        build_architect_agent_graph(
            cast(ArchitectRunnerLike, NoopRunner()),
            agent_executor=cast(SpecialistAgentExecutor, object()),
        )
        .get_graph()
        .draw_mermaid()
    )


def _prepare_context(state: ArchitectAgentGraphState) -> ArchitectAgentGraphState:
    delivery_state = state["delivery_state"]
    run_dir = Path(state["run_dir"])
    missing_inputs = [
        artifact
        for artifact in (BUSINESS_ANALYSIS_MD, BUSINESS_ANALYSIS_JSON)
        if not (run_dir / artifact).exists()
    ]
    if missing_inputs:
        result = AgentRunResult(
            agent_id=ARCHITECT_AGENT_ID,
            status="architecture_blocked",
            output_artifacts=[],
            summary="Architecture requires BA artifacts: " + ", ".join(missing_inputs),
        )
        return {**state, "result": result}

    request = _architecture_request(run_dir, delivery_state)
    request_path = run_dir / ARCHITECTURE_REQUEST
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_event(
        run_dir,
        delivery_state["run_id"],
        ARCHITECT_AGENT_ID,
        "architecture_started",
        {"artifact": ARCHITECTURE_REQUEST},
    )
    return state


def _run_agent_executor(
    runner: ArchitectRunnerLike,
    agent_executor: SpecialistAgentExecutor,
):
    def run(state: ArchitectAgentGraphState) -> ArchitectAgentGraphState:
        if "result" in state:
            return state
        result = agent_executor.run(
            SpecialistAgentRequest(
                agent_id=ARCHITECT_AGENT_ID,
                agent_name="Architect Agent",
                stage="architecture",
                system_prompt=ARCHITECT_AGENT_SYSTEM_PROMPT,
                user_prompt=_architect_user_prompt(state["delivery_state"]),
                runner=runner,
                run_dir=Path(state["run_dir"]),
                delivery_state=state["delivery_state"],
                packet=WorkItemExecutionPacket(
                    run_id=str(state["delivery_state"]["run_id"]),
                    work_item_id="PLAN-02",
                    sprint_id="planning",
                    owner_agent=ARCHITECT_AGENT_ID,
                    tool_name="run_architect",
                    tool_call_id=str(
                        state["delivery_state"].get("agent_execution_id") or "PLAN-02"
                    ),
                    attempt_id="1",
                    status="in_progress",
                ),
            )
        )
        return {**state, "result": result}

    return run


def _architect_user_prompt(state: DeliveryState) -> str:
    return json.dumps(
        {
            "task": "Run architecture planning from the Business Analyst artifacts.",
            "run_dir": state["run_dir"],
            "requirements_path": state.get("requirements_path"),
            "input_artifacts": [BUSINESS_ANALYSIS_MD, BUSINESS_ANALYSIS_JSON],
            "expected_outputs": [ARCHITECTURE_MD, ARCHITECTURE_JSON, ARCHITECTURE_MMD],
        },
        indent=2,
        sort_keys=True,
    )


def _apply_result(state: ArchitectAgentGraphState) -> ArchitectAgentGraphState:
    if "result" not in state:
        raise ValueError("Architect agent graph result is missing.")

    delivery_state = state["delivery_state"]
    result = state["result"]
    updated = mark_node_completed(
        delivery_state,
        node_name="architecture",
        stage="architecture",
        status=result.status,
    )
    extend_artifacts(
        updated,
        artifact_refs(
            result.output_artifacts,
            kind="planning",
            owner_agent=result.agent_id or ARCHITECT_AGENT_ID,
        ),
    )
    append_downstream_response(updated, from_agent=ARCHITECT_AGENT_ID, result=result)
    if result.status != "architecture_completed":
        updated["blockers"] = [*updated.get("blockers", []), result.summary]
    completed_event = (
        "architecture_completed"
        if result.status == "architecture_completed"
        else "architecture_blocked"
    )
    write_event(
        Path(updated["run_dir"]),
        updated["run_id"],
        ARCHITECT_AGENT_ID,
        completed_event,
        {"status": result.status, "artifacts": result.output_artifacts},
    )
    return {**state, "delivery_state": updated}


def _architecture_request(run_dir: Path, state: DeliveryState) -> dict[str, object]:
    model = (
        agent_env_value("ARCHITECT_CODEX_MODEL", state)
        or agent_env_value("AGENT_CODEX_MODEL", state)
        or DEFAULT_ARCHITECT_MODEL
    )
    configured_requirements = state.get("requirements_path")
    requirements_path = (
        Path(str(configured_requirements))
        if configured_requirements
        else run_dir / "00-requirements.md"
    )
    return {
        "run_id": state["run_id"],
        "agent_id": ARCHITECT_AGENT_ID,
        "model": model,
        "requirements_artifact": _relative_or_absolute(run_dir, requirements_path),
        "input_artifacts": [BUSINESS_ANALYSIS_MD, BUSINESS_ANALYSIS_JSON],
        "expected_outputs": [ARCHITECTURE_MD, ARCHITECTURE_JSON, ARCHITECTURE_MMD],
        "codex_resume_thread_id": codex_resume_thread_id(state, ARCHITECT_AGENT_ID),
        "available_agents": _available_agent_descriptors(),
        "incoming_messages": render_incoming_messages_for_prompt(
            run_dir,
            to_agent=ARCHITECT_AGENT_ID,
        ),
    }


def _relative_or_absolute(run_dir: Path, path: Path) -> str:
    return path.relative_to(run_dir).as_posix() if path.is_relative_to(run_dir) else str(path)


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
