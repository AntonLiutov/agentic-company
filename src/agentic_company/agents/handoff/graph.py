"""Internal LangGraph for the Handoff Agent."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, cast

from agentic_company.agents.handoff.codex_cli import (
    HANDOFF_CODEX_AGENT_ID,
    HandoffCodexRunner,
)
from agentic_company.agents.handoff.contracts import (
    FINAL_PROJECT_REPORT_SCOPE,
    SPRINT_HANDOFF_SCOPE,
    handoff_contract_paths_for_scope,
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
from agentic_company.platform.state import (
    DeliveryState,
    codex_resume_thread_id,
    mark_node_completed,
)

HANDOFF_AGENT_ID = "documentation-handoff-agent"

HANDOFF_AGENT_GRAPH_NODE_ORDER = AGENT_EXECUTOR_GRAPH_NODE_ORDER
HANDOFF_AGENT_SYSTEM_PROMPT = """You are the Documentation / Handoff Agent for agentic-company.

You own handoff packaging only through the available tools. Call `codex_exec` to
run the Codex Handoff worker for the current run. Do not claim handoff is
complete without calling a tool.
"""


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
    agent_executor: SpecialistAgentExecutor,
    node_order: Sequence[str] | None = None,
):
    """Build the Handoff Agent internal graph.

    The graph does not render report sections or stakeholder copy. Those choices
    belong to the Codex Handoff specialist inside `codex_handoff_execution`.
    """

    order = list(HANDOFF_AGENT_GRAPH_NODE_ORDER if node_order is None else node_order)
    if not order:
        raise ValueError("Handoff agent graph requires at least one node.")

    node_map = {
        "prepare_context": _prepare_context,
        "run_agent_executor": _run_agent_executor(runner, agent_executor),
        "apply_result": _apply_handoff_result,
    }
    return build_agent_executor_graph(
        HandoffAgentGraphState,
        prepare_node=node_map[order[0]],
        run_agent_executor_node=node_map[order[1]],
        apply_result_node=node_map[order[2]],
        node_order=tuple(order),
    )


def run_handoff_agent_graph(
    delivery_state: DeliveryState,
    *,
    runner: HandoffRunner | None = None,
    agent_executor: SpecialistAgentExecutor,
) -> DeliveryState:
    """Run the Handoff Agent subgraph and return updated delivery state."""

    graph_state: HandoffAgentGraphState = {
        "delivery_state": delivery_state,
        "run_dir": delivery_state["run_dir"],
    }
    result = build_handoff_agent_graph(runner, agent_executor=agent_executor).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_handoff_agent_graph_mermaid() -> str:
    """Render the Handoff Agent subgraph as Mermaid text."""

    class NoopRunner:
        def run(self, run_dir: Path) -> AgentRunResult:
            raise RuntimeError("Runner is not available in graph rendering.")

    return (
        build_handoff_agent_graph(
            cast(HandoffRunner, NoopRunner()),
            agent_executor=cast(SpecialistAgentExecutor, object()),
        )
        .get_graph()
        .draw_mermaid()
    )


def _prepare_context(state: HandoffAgentGraphState) -> HandoffAgentGraphState:
    delivery_state = state["delivery_state"]
    run_dir = Path(state["run_dir"])
    _write_handoff_execution_request(run_dir, delivery_state)
    update_execution_request_context(
        run_dir,
        execution_id=str(delivery_state.get("agent_execution_id") or ""),
        execution_intent=str(delivery_state.get("agent_execution_intent") or ""),
        parent_message_id=str(delivery_state.get("agent_call_message_id") or ""),
        codex_resume_thread_id=codex_resume_thread_id(delivery_state, HANDOFF_CODEX_AGENT_ID),
        handoff_scope=str(delivery_state.get("handoff_scope") or ""),
        handoff_sprint_id=str(delivery_state.get("handoff_sprint_id") or ""),
        handoff_output_dir=str(delivery_state.get("handoff_output_dir") or ""),
        handoff_expected_outputs=list(delivery_state.get("handoff_expected_outputs", [])),
    )
    event_log = run_dir / "events.jsonl"
    event_log.parent.mkdir(parents=True, exist_ok=True)
    write_event(
        event_log,
        delivery_state["run_id"],
        HANDOFF_AGENT_ID,
        "handoff_started",
        {"deployment_status": delivery_state.get("deployment_status")},
    )
    return state


def _write_handoff_execution_request(run_dir: Path, delivery_state: DeliveryState) -> None:
    contract_paths = handoff_contract_paths_for_scope(
        str(delivery_state.get("handoff_scope") or ""),
        sprint_id=str(delivery_state.get("handoff_sprint_id") or ""),
    )
    request = build_execution_request_payload(
        delivery_state,
        agent_id=HANDOFF_AGENT_ID,
        model=(
            agent_env_value("HANDOFF_CODEX_MODEL", delivery_state)
            or agent_env_value("AGENT_CODEX_MODEL", delivery_state)
            or "gpt-5.3-codex"
        ),
        input_artifacts=_handoff_input_artifacts(delivery_state),
        expected_outputs=contract_paths.as_list(),
        instructions=[
            (
                "Read the current Team Lead request, work board, delivery artifacts, "
                "QA evidence, deployment results, and prior handoff artifacts."
            ),
            (
                "Create the requested handoff package using the explicit handoff_scope, "
                "handoff_sprint_id, and handoff_expected_outputs from the execution request."
            ),
            "Return explicit artifact refs for every handoff file produced or referenced.",
            (
                "If this is a final project handoff, summarize all completed sprint "
                "and deployment evidence."
            ),
        ],
        constraints=[
            "Do not require post-deploy evidence for a local-only sprint handoff.",
            "Do not invent public URLs, deployment status, or QA evidence.",
            "Do not overwrite unrelated handoff scopes; use scope-aware paths when requested.",
        ],
        codex_resume_thread_id=codex_resume_thread_id(delivery_state, HANDOFF_CODEX_AGENT_ID),
        handoff_scope=str(delivery_state.get("handoff_scope") or ""),
        handoff_sprint_id=str(delivery_state.get("handoff_sprint_id") or ""),
        handoff_output_dir=str(Path(contract_paths.summary).parent),
        handoff_expected_outputs=contract_paths.as_list(),
    )
    write_execution_request(run_dir, request)


def _handoff_input_artifacts(delivery_state: DeliveryState) -> list[str]:
    paths = [
        "00-requirements.md",
        *[
            str(artifact.get("path"))
            for artifact in delivery_state.get("artifacts", [])
            if artifact.get("path") and "/codex/" not in str(artifact.get("path"))
        ],
    ]
    return _unique_paths(paths)


def _run_agent_executor(runner: HandoffRunner | None, agent_executor: SpecialistAgentExecutor):
    def run(state: HandoffAgentGraphState) -> HandoffAgentGraphState:
        run_dir = Path(state["run_dir"])
        result = agent_executor.run(
            SpecialistAgentRequest(
                agent_id=HANDOFF_AGENT_ID,
                agent_name="Documentation / Handoff Agent",
                stage="handoff",
                system_prompt=HANDOFF_AGENT_SYSTEM_PROMPT,
                user_prompt=_handoff_user_prompt(state["delivery_state"]),
                runner=runner or HandoffCodexRunner(),
                run_dir=run_dir,
                delivery_state=state["delivery_state"],
            )
        )
        return {**state, "result": result, "status": _normalize_handoff_status(result.status)}

    return run


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
    append_downstream_response(
        updated,
        from_agent=HANDOFF_AGENT_ID,
        result=result,
        default_correlation_id=_handoff_correlation_id(updated),
    )
    if updated.get("handoff_scope") == FINAL_PROJECT_REPORT_SCOPE and status == "ready":
        final_artifacts = [
            path
            for path in list(updated.get("handoff_expected_outputs", []))
            if isinstance(path, str) and path
        ]
        updated["final_project_report"] = next(
            (path for path in final_artifacts if path.endswith("/release-report.html")),
            "handoff/project/final/release-report.html",
        )
        updated["final_project_artifacts"] = final_artifacts or list(result.output_artifacts)
    return {**state, "delivery_state": updated}


def _normalize_handoff_status(status: str) -> str:
    normalized = status.removeprefix("handoff_").removeprefix("codex_")
    return normalized if normalized in {"ready", "blocked", "failed", "unknown"} else "unknown"


def _handoff_user_prompt(state: DeliveryState) -> str:
    return json.dumps(
        {
            "task": "Run the assigned Handoff Codex task.",
            "run_dir": state["run_dir"],
            "deployment_status": state.get("deployment_status"),
            "public_urls": state.get("public_urls", []),
            "agent_call_message_id": state.get("agent_call_message_id"),
            "handoff_scope": state.get("handoff_scope"),
            "handoff_sprint_id": state.get("handoff_sprint_id"),
            "handoff_expected_outputs": state.get("handoff_expected_outputs", []),
        },
        indent=2,
        sort_keys=True,
    )


def _unique_paths(paths: list[str]) -> list[str]:
    unique: list[str] = []
    for path in paths:
        if path and path not in unique:
            unique.append(path)
    return unique


def _handoff_correlation_id(state: DeliveryState) -> str:
    if state.get("handoff_scope") == SPRINT_HANDOFF_SCOPE:
        return str(state.get("handoff_sprint_id") or state.get("team_lead_sprint_id") or "")
    if state.get("handoff_scope") == FINAL_PROJECT_REPORT_SCOPE:
        return FINAL_PROJECT_REPORT_SCOPE
    return str(state.get("agent_call_correlation_id") or state.get("team_lead_sprint_id") or "")
