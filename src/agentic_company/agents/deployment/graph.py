"""Internal LangGraph for the Deployment Agent."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, cast

from agentic_company.agents.deployment.codex_cli import (
    DEPLOYMENT_CODEX_AGENT_ID,
    public_urls_from_deployment_result,
)
from agentic_company.integrations.codex import DEFAULT_CODEX_MODEL
from agentic_company.platform.agent.agent_contracts import (
    append_downstream_response,
    artifact_refs,
    extend_artifacts,
    record_specialist_completion,
    record_specialist_start,
)
from agentic_company.platform.agent.agent_runtime import (
    AGENT_EXECUTOR_GRAPH_NODE_ORDER,
    SpecialistAgentExecutor,
    SpecialistAgentRequest,
    agent_env_value,
    build_agent_executor_graph,
)
from agentic_company.platform.artifacts.artifacts import (
    build_execution_request_payload,
    update_execution_request_context,
    write_execution_request,
)
from agentic_company.platform.db.models import AgentRunResult
from agentic_company.platform.db.runtime_db import (
    completed_work_item_ids,
    get_work_item,
    packet_for_work_item,
    record_generated_app_url,
)
from agentic_company.platform.db.state import (
    DeliveryState,
    codex_resume_thread_id,
)
from agentic_company.platform.run.events import write_event

DEPLOYMENT_AGENT_ID = "deployment-agent"

DEPLOYMENT_AGENT_GRAPH_NODE_ORDER = AGENT_EXECUTOR_GRAPH_NODE_ORDER
DEPLOYMENT_AGENT_SYSTEM_PROMPT = """You are the Deployment Agent for agentic-company.

You own deployment work only through the available tools. Call `codex_exec` to
run the Codex deployment worker for the current run. Do not claim deployment is
complete without calling a tool.
"""


class DeploymentRunner(Protocol):
    """Codex-owned deployment execution boundary."""

    def run(self, run_dir: Path) -> AgentRunResult:
        """Run deployment and return the parsed deployment result."""


class DeploymentAgentGraphState(TypedDict):
    """Internal state for the Deployment Agent subgraph."""

    delivery_state: DeliveryState
    run_dir: str
    result: NotRequired[AgentRunResult]
    status: NotRequired[str]
    public_urls: NotRequired[list[str]]


def build_deployment_agent_graph(
    runner: DeploymentRunner | None = None,
    *,
    agent_executor: SpecialistAgentExecutor,
    node_order: Sequence[str] | None = None,
):
    """Build the Deployment Agent internal graph.

    The graph intentionally contains no concrete Azure command sequence, service
    names, ports, Dockerfile names, or topology branches. Those choices belong to
    the Codex Deployment specialist inside the `codex_deployment_execution` node.
    """

    order = list(DEPLOYMENT_AGENT_GRAPH_NODE_ORDER if node_order is None else node_order)
    if not order:
        raise ValueError("Deployment agent graph requires at least one node.")

    node_map = {
        "prepare_context": _prepare_context,
        "run_agent_executor": _run_agent_executor(runner, agent_executor),
        "apply_result": _apply_deployment_result,
    }
    return build_agent_executor_graph(
        DeploymentAgentGraphState,
        prepare_node=node_map[order[0]],
        run_agent_executor_node=node_map[order[1]],
        apply_result_node=node_map[order[2]],
        node_order=tuple(order),
    )


def run_deployment_agent_graph(
    delivery_state: DeliveryState,
    *,
    runner: DeploymentRunner | None = None,
    agent_executor: SpecialistAgentExecutor,
) -> DeliveryState:
    """Run the Deployment Agent subgraph and return updated delivery state."""

    graph_state: DeploymentAgentGraphState = {
        "delivery_state": delivery_state,
        "run_dir": delivery_state["run_dir"],
    }
    result = build_deployment_agent_graph(runner, agent_executor=agent_executor).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_deployment_agent_graph_mermaid() -> str:
    """Render the Deployment Agent subgraph as Mermaid text."""

    class NoopRunner:
        def run(self, run_dir: Path) -> AgentRunResult:
            raise RuntimeError("Runner is not available in graph rendering.")

    return (
        build_deployment_agent_graph(
            cast(DeploymentRunner, NoopRunner()),
            agent_executor=cast(SpecialistAgentExecutor, object()),
        )
        .get_graph()
        .draw_mermaid()
    )


def _prepare_context(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
    delivery_state = state["delivery_state"]
    run_dir = Path(state["run_dir"])
    _write_deployment_execution_request(run_dir, delivery_state)
    update_execution_request_context(
        run_dir,
        execution_id=str(delivery_state.get("agent_execution_id") or ""),
        execution_intent=str(delivery_state.get("agent_execution_intent") or ""),
        parent_message_id=str(delivery_state.get("agent_call_message_id") or ""),
        codex_resume_thread_id=codex_resume_thread_id(delivery_state, DEPLOYMENT_CODEX_AGENT_ID),
    )
    record_specialist_start(delivery_state, agent_id=DEPLOYMENT_AGENT_ID, stage="deployment")
    return state


def _write_deployment_execution_request(run_dir: Path, delivery_state: DeliveryState) -> None:
    """Write the Codex execution envelope required by the Deployment runner."""

    work_item_id = str(delivery_state.get("agent_call_correlation_id") or "").strip()
    work_item = get_work_item(str(delivery_state["run_id"]), work_item_id).to_dict()
    instructions = [
        "Inspect the current run artifacts, generated project, and deployment assignment.",
        "Use available Azure/Docker configuration when present.",
        "Deploy only when the current assignment and evidence support deployment.",
        "If required privileged inputs are missing, return an evidence-backed blocker.",
    ]
    repo_ctx = _run_repo_context(str(delivery_state["run_id"]))
    if repo_ctx:
        instructions.append(
            f"A git repository is connected for this run: {repo_ctx['repository']} "
            f"(base branch `{repo_ctx['base_branch']}`). If you commit any deployment config "
            "(Dockerfile, infra, manifests), DELIVER IT AS A PULL REQUEST using the "
            f"git-pr-workflow skill: branch `adl/{work_item_id.lower()}`, commit (never secrets), "
            "push, open the PR. Never commit to the base branch directly."
        )
        instructions.append(
            "Phase 3 platform override: do not run `gh`, do not push, and do not "
            "open or merge pull requests from inside the worker. Write deployment "
            "config changes and evidence only; the platform publishes branch/PR "
            "host-side after your Codex execution completes."
        )
    request = build_execution_request_payload(
        delivery_state,
        agent_id=DEPLOYMENT_AGENT_ID,
        model=(
            agent_env_value("DEPLOYMENT_CODEX_MODEL", delivery_state)
            or agent_env_value("AGENT_CODEX_MODEL", delivery_state)
            or DEFAULT_CODEX_MODEL
        ),
        input_artifacts=_deployment_input_artifacts(delivery_state),
        expected_outputs=[
            "deployment/result.json",
            "11-deployment-plan.json",
            "11-deployment-plan.md",
            "12-deployment-request.json",
            "12-deployment-request.md",
            "13-deployment-summary.md",
        ],
        instructions=instructions,
        constraints=[
            "Do not invent Azure resource names, credentials, or public URLs.",
            "Do not commit or print secrets.",
            "Do not delete cloud resources.",
            "Return explicit artifact refs and deployment status.",
        ],
        target_project_dir=str(delivery_state["target_project_dir"]),
        work_item=work_item,
        completed_work_item_ids=completed_work_item_ids(
            str(delivery_state["run_id"]), str(work_item.get("sprint_id") or "")
        ),
        codex_resume_thread_id=codex_resume_thread_id(delivery_state, DEPLOYMENT_CODEX_AGENT_ID),
    )
    write_execution_request(run_dir, request)


def _run_repo_context(run_id: str) -> dict[str, str] | None:
    """Connected repo info so the Publisher delivers committed config as a PR."""
    try:
        from agentic_company.platform.delivery.delivery_pr import run_repo_context

        return run_repo_context(run_id)
    except Exception:
        return None


def _deployment_input_artifacts(delivery_state: DeliveryState) -> list[str]:
    paths = ["00-requirements.md"]
    return _unique_paths(paths)


def _unique_paths(paths: list[str]) -> list[str]:
    unique: list[str] = []
    for path in paths:
        if path and path not in unique:
            unique.append(path)
    return unique


def _run_agent_executor(runner: DeploymentRunner | None, agent_executor: SpecialistAgentExecutor):
    def run(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
        if runner is None:
            raise ValueError("Deployment Agent requires an explicit worker runner.")
        run_dir = Path(state["run_dir"])
        result = agent_executor.run(
            SpecialistAgentRequest(
                agent_id=DEPLOYMENT_AGENT_ID,
                agent_name="Deployment Agent",
                stage="deployment",
                system_prompt=DEPLOYMENT_AGENT_SYSTEM_PROMPT,
                user_prompt=_deployment_user_prompt(state["delivery_state"]),
                runner=runner,
                run_dir=run_dir,
                delivery_state=state["delivery_state"],
                packet=packet_for_work_item(
                    run_id=str(state["delivery_state"]["run_id"]),
                    work_item_id=str(
                        state["delivery_state"].get("agent_call_correlation_id") or ""
                    ),
                    tool_name="run_deployment",
                    tool_call_id=str(state["delivery_state"].get("agent_execution_id") or ""),
                    attempt_id="1",
                    owner_agent=DEPLOYMENT_AGENT_ID,
                ),
            )
        )
        status = _normalize_deployment_status(result.status)
        public_urls = public_urls_from_deployment_result(run_dir)
        if status == "deployed" and not public_urls:
            status = "unknown"
        return {**state, "result": result, "status": status, "public_urls": public_urls}

    return run


def _apply_deployment_result(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
    result = state.get("result")
    if result is None:
        raise ValueError("Deployment agent graph result is missing.")

    delivery_state = state["delivery_state"]
    deployment_status = state.get("status") or _normalize_deployment_status(result.status)
    public_urls = state.get("public_urls", [])
    event_log = Path(state["run_dir"])
    write_event(
        event_log,
        delivery_state["run_id"],
        DEPLOYMENT_AGENT_ID,
        "artifact_written",
        {"artifact": "13-deployment-summary.md", "status": deployment_status},
    )

    updated = record_specialist_completion(
        delivery_state,
        agent_id=DEPLOYMENT_AGENT_ID,
        stage="deployment",
        node_name="deployment",
        outcome=deployment_status,
    )
    updated["deployment_status"] = deployment_status
    updated["public_url"] = public_urls[0] if public_urls else None
    if public_urls:
        updated["public_urls"] = public_urls
        # Persist the URL durably at the deployment point so a later node failure
        # cannot lose it before the run finalizer writes the terminal status.
        record_generated_app_url(str(updated["run_id"]), str(public_urls[0]))
    extend_artifacts(
        updated,
        artifact_refs(result.output_artifacts, kind="deployment", owner_agent=result.agent_id),
    )
    deploy_item = str(updated.get("agent_call_correlation_id") or "")
    if deployment_status not in {"failed", "blocked"} and deploy_item:
        try:  # best-effort: PR any deployment config the Publisher committed to the repo
            from agentic_company.platform.delivery.delivery_pr import publish_work_item_pr

            publish_work_item_pr(str(updated["run_id"]), deploy_item)
        except Exception:
            pass
    append_downstream_response(
        updated,
        from_agent=DEPLOYMENT_AGENT_ID,
        result=result,
        default_correlation_id=str(updated.get("team_lead_sprint_id") or ""),
    )
    return {**state, "delivery_state": updated}


def _normalize_deployment_status(status: str) -> str:
    normalized = status.removeprefix("deployment_").removeprefix("codex_")
    return normalized if normalized in {"deployed", "blocked", "failed", "unknown"} else "unknown"


def _deployment_user_prompt(state: DeliveryState) -> str:
    return json.dumps(
        {
            "task": "Run the assigned Deployment Codex task.",
            "run_dir": state["run_dir"],
            "deployment_status": state.get("deployment_status"),
            "public_urls": state.get("public_urls", []),
            "agent_call_message_id": state.get("agent_call_message_id"),
        },
        indent=2,
        sort_keys=True,
    )
