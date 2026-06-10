"""Internal LangGraph for the Project Manager agent."""

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
    record_specialist_completion,
    record_specialist_start,
)
from agentic_company.platform.agent_runtime import (
    AGENT_EXECUTOR_GRAPH_NODE_ORDER,
    SpecialistAgentExecutor,
    SpecialistAgentRequest,
    agent_env_value,
    build_agent_executor_graph,
)
from agentic_company.platform.messages import render_incoming_messages_for_prompt
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.runtime_db import materialize_pm_work_items
from agentic_company.platform.state import (
    DeliveryState,
    codex_resume_thread_id,
)
from agentic_company.platform.tool_contracts import WorkItemExecutionPacket

PROJECT_MANAGER_AGENT_ID = "project-manager-agent"
PROJECT_MANAGER_AGENT_GRAPH_NODE_ORDER = AGENT_EXECUTOR_GRAPH_NODE_ORDER
UPSTREAM_PLANNING_DIR = "upstream-planning"
PROJECT_MANAGEMENT_DIR = f"{UPSTREAM_PLANNING_DIR}/project-management"
PROJECT_MANAGEMENT_REQUEST = f"{UPSTREAM_PLANNING_DIR}/project-management-request.json"
PROJECT_MANAGEMENT_MD = f"{PROJECT_MANAGEMENT_DIR}/release-plan.md"
PROJECT_MANAGEMENT_JSON = f"{PROJECT_MANAGEMENT_DIR}/release-plan.json"
PROJECT_MANAGEMENT_WORK_ITEMS_JSON = f"{PROJECT_MANAGEMENT_DIR}/planned-work-items.json"
PROJECT_MANAGEMENT_RISKS_MD = f"{PROJECT_MANAGEMENT_DIR}/risks-and-dependencies.md"
PROJECT_MANAGEMENT_ROADMAP_CSV = f"{PROJECT_MANAGEMENT_DIR}/roadmap.csv"
BUSINESS_ANALYSIS_MD = f"{UPSTREAM_PLANNING_DIR}/business-analysis.md"
BUSINESS_ANALYSIS_JSON = f"{UPSTREAM_PLANNING_DIR}/business-analysis.json"
ARCHITECTURE_MD = f"{UPSTREAM_PLANNING_DIR}/architecture.md"
ARCHITECTURE_JSON = f"{UPSTREAM_PLANNING_DIR}/architecture.json"
ARCHITECTURE_MMD = f"{UPSTREAM_PLANNING_DIR}/architecture.mmd"
DEFAULT_PROJECT_MANAGER_MODEL = DEFAULT_CODEX_MODEL
SPRINT_COUNT_GUIDANCE = (
    "Choose the natural sprint breakdown for the source task, dependencies, "
    "risk, and validation needs. Do not use default sprint counts, numeric "
    "quotas, caps, or orientational ranges. Create as many or as few sprints "
    "as the actual scope needs for reliable delivery."
)
FEATURE_SIZING_GUIDANCE = (
    "Keep features as meaningful vertical slices that are small enough for "
    "Fullstack to implement and QA to validate without unspecified input. Preserve "
    "user journeys and source traceability. Avoid both tiny technical chores "
    "and overloaded feature bundles."
)
SPRINT_CAPACITY_GUIDANCE = (
    "Size each sprint by total risk, effort, dependency coupling, QA burden, and "
    "deployment/release complexity rather than by raw feature count. Do not "
    "compress large scope into fewer items for neatness, and do not add ceremony "
    "for its own sake. Choose the best package for the real scope."
)

PROJECT_MANAGER_AGENT_SYSTEM_PROMPT = """
You are the Project Manager Agent for agentic-company.

You own release and sprint planning only through the available tools. Call
`codex_exec` to run the Codex Project Manager worker for the current BA and
architecture artifacts. Do not claim planning is complete without calling a tool.
""".strip()


class ProjectManagerRunnerLike(Protocol):
    def run(self, run_dir: Path) -> AgentRunResult:
        """Run the Project Manager backend."""


class ProjectManagerAgentGraphState(TypedDict):
    """Internal state for the Project Manager agent subgraph."""

    run_dir: str
    delivery_state: DeliveryState
    result: NotRequired[AgentRunResult]


def build_project_manager_agent_graph(
    runner: ProjectManagerRunnerLike,
    *,
    agent_executor: SpecialistAgentExecutor,
    node_order: Sequence[str] | None = None,
):
    """Build the Project Manager agent internal graph."""

    order = list(PROJECT_MANAGER_AGENT_GRAPH_NODE_ORDER if node_order is None else node_order)
    if not order:
        raise ValueError("Project Manager agent graph requires at least one node.")

    node_map = {
        "prepare_context": _prepare_context,
        "run_agent_executor": _run_agent_executor(runner, agent_executor),
        "apply_result": _apply_result,
    }
    return build_agent_executor_graph(
        ProjectManagerAgentGraphState,
        prepare_node=node_map[order[0]],
        run_agent_executor_node=node_map[order[1]],
        apply_result_node=node_map[order[2]],
        node_order=tuple(order),
    )


def run_project_manager_agent_graph(
    delivery_state: DeliveryState,
    runner: ProjectManagerRunnerLike,
    agent_executor: SpecialistAgentExecutor,
) -> DeliveryState:
    """Run the Project Manager agent subgraph and return updated delivery state."""

    graph_state: ProjectManagerAgentGraphState = {
        "delivery_state": delivery_state,
        "run_dir": delivery_state["run_dir"],
    }
    result = build_project_manager_agent_graph(
        runner,
        agent_executor=agent_executor,
    ).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_project_manager_agent_graph_mermaid() -> str:
    """Render the Project Manager subgraph as Mermaid text."""

    class NoopRunner:
        def run(self, run_dir: Path) -> AgentRunResult:
            raise RuntimeError("Runner is not available in graph rendering.")

    return (
        build_project_manager_agent_graph(
            cast(ProjectManagerRunnerLike, NoopRunner()),
            agent_executor=cast(SpecialistAgentExecutor, object()),
        )
        .get_graph()
        .draw_mermaid()
    )


def _prepare_context(state: ProjectManagerAgentGraphState) -> ProjectManagerAgentGraphState:
    delivery_state = state["delivery_state"]
    run_dir = Path(state["run_dir"])
    missing_inputs = [
        artifact
        for artifact in (
            BUSINESS_ANALYSIS_MD,
            BUSINESS_ANALYSIS_JSON,
            ARCHITECTURE_MD,
            ARCHITECTURE_JSON,
        )
        if not (run_dir / artifact).exists()
    ]
    if missing_inputs:
        result = AgentRunResult(
            agent_id=PROJECT_MANAGER_AGENT_ID,
            status="project_management_blocked",
            output_artifacts=[],
            summary="Project management requires BA and architecture artifacts: "
            + ", ".join(missing_inputs),
        )
        return {**state, "result": result}

    request = _project_management_request(run_dir, delivery_state)
    request_path = run_dir / PROJECT_MANAGEMENT_REQUEST
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    record_specialist_start(
        delivery_state, agent_id=PROJECT_MANAGER_AGENT_ID, stage="project_management"
    )
    return state


def _run_agent_executor(
    runner: ProjectManagerRunnerLike,
    agent_executor: SpecialistAgentExecutor,
):
    def run(state: ProjectManagerAgentGraphState) -> ProjectManagerAgentGraphState:
        if "result" in state:
            return state
        result = agent_executor.run(
            SpecialistAgentRequest(
                agent_id=PROJECT_MANAGER_AGENT_ID,
                agent_name="Project Manager Agent",
                stage="project_management",
                system_prompt=PROJECT_MANAGER_AGENT_SYSTEM_PROMPT,
                user_prompt=_project_manager_user_prompt(state["delivery_state"]),
                runner=runner,
                run_dir=Path(state["run_dir"]),
                delivery_state=state["delivery_state"],
                packet=WorkItemExecutionPacket(
                    run_id=str(state["delivery_state"]["run_id"]),
                    work_item_id="PLAN-03",
                    sprint_id="planning",
                    owner_agent=PROJECT_MANAGER_AGENT_ID,
                    tool_name="run_project_manager",
                    tool_call_id=str(
                        state["delivery_state"].get("agent_execution_id") or "PLAN-03"
                    ),
                    attempt_id="1",
                    status="in_progress",
                ),
            )
        )
        return {**state, "result": result}

    return run


def _project_manager_user_prompt(state: DeliveryState) -> str:
    return json.dumps(
        {
            "task": "Run project management planning from the BA and architecture artifacts.",
            "run_dir": state["run_dir"],
            "requirements_path": state.get("requirements_path"),
            "input_artifacts": [
                BUSINESS_ANALYSIS_MD,
                BUSINESS_ANALYSIS_JSON,
                ARCHITECTURE_MD,
                ARCHITECTURE_JSON,
                ARCHITECTURE_MMD,
            ],
            "planning_policy": {
                "sprint_count_guidance": SPRINT_COUNT_GUIDANCE,
                "feature_sizing_guidance": FEATURE_SIZING_GUIDANCE,
                "sprint_capacity_guidance": SPRINT_CAPACITY_GUIDANCE,
                "planning_bias": "minimum sufficient delivery plan without artificial expansion",
            },
            "expected_outputs": [
                PROJECT_MANAGEMENT_MD,
                PROJECT_MANAGEMENT_JSON,
                PROJECT_MANAGEMENT_WORK_ITEMS_JSON,
                PROJECT_MANAGEMENT_RISKS_MD,
                PROJECT_MANAGEMENT_ROADMAP_CSV,
            ],
        },
        indent=2,
        sort_keys=True,
    )


def _apply_result(state: ProjectManagerAgentGraphState) -> ProjectManagerAgentGraphState:
    if "result" not in state:
        raise ValueError("Project Manager agent graph result is missing.")

    delivery_state = state["delivery_state"]
    result = state["result"]
    outcome = result.status
    blockers = list(delivery_state.get("blockers", []))
    if result.status == "project_management_completed":
        try:
            materialize_pm_work_items(
                str(delivery_state["run_id"]), Path(delivery_state["run_dir"])
            )
        except ValueError as exc:
            outcome = "project_management_blocked"
            blockers.append(str(exc))
        else:
            blockers = []
    else:
        blockers.append(result.summary)
    updated = record_specialist_completion(
        delivery_state,
        agent_id=PROJECT_MANAGER_AGENT_ID,
        stage="project_management",
        node_name="project_management",
        outcome=outcome,
    )
    updated["blockers"] = blockers
    extend_artifacts(
        updated,
        artifact_refs(
            result.output_artifacts,
            kind="planning",
            owner_agent=result.agent_id or PROJECT_MANAGER_AGENT_ID,
        ),
    )
    append_downstream_response(updated, from_agent=PROJECT_MANAGER_AGENT_ID, result=result)
    return {**state, "delivery_state": updated}


def _project_management_request(run_dir: Path, state: DeliveryState) -> dict[str, object]:
    model = (
        agent_env_value("PROJECT_MANAGER_CODEX_MODEL", state)
        or agent_env_value("AGENT_CODEX_MODEL", state)
        or DEFAULT_PROJECT_MANAGER_MODEL
    )
    configured_requirements = state.get("requirements_path")
    requirements_path = (
        Path(str(configured_requirements))
        if configured_requirements
        else run_dir / "00-requirements.md"
    )
    return {
        "run_id": state["run_id"],
        "agent_id": PROJECT_MANAGER_AGENT_ID,
        "model": model,
        "requirements_artifact": _relative_or_absolute(run_dir, requirements_path),
        "input_artifacts": [
            BUSINESS_ANALYSIS_MD,
            BUSINESS_ANALYSIS_JSON,
            ARCHITECTURE_MD,
            ARCHITECTURE_JSON,
            ARCHITECTURE_MMD,
        ],
        "expected_outputs": [
            PROJECT_MANAGEMENT_MD,
            PROJECT_MANAGEMENT_JSON,
            PROJECT_MANAGEMENT_WORK_ITEMS_JSON,
            PROJECT_MANAGEMENT_RISKS_MD,
            PROJECT_MANAGEMENT_ROADMAP_CSV,
        ],
        "planning_policy": {
            "sprint_count_guidance": SPRINT_COUNT_GUIDANCE,
            "feature_sizing_guidance": FEATURE_SIZING_GUIDANCE,
            "sprint_capacity_guidance": SPRINT_CAPACITY_GUIDANCE,
            "planning_bias": "minimum sufficient delivery plan without artificial expansion",
        },
        "codex_resume_thread_id": codex_resume_thread_id(state, PROJECT_MANAGER_AGENT_ID),
        "available_agents": _available_agent_descriptors(),
        "incoming_messages": render_incoming_messages_for_prompt(
            run_dir,
            to_agent=PROJECT_MANAGER_AGENT_ID,
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
