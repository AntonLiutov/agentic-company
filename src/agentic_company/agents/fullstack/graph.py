"""Internal LangGraph for the fullstack agent."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NotRequired, Protocol, TypedDict, cast

from agentic_company.integrations.codex import DEFAULT_CODEX_MODEL
from agentic_company.platform.agent.agent_contracts import (
    append_downstream_response,
    artifact_refs,
    extend_artifacts,
    record_specialist_completion,
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
    write_execution_request,
)
from agentic_company.platform.db.models import AgentRunResult
from agentic_company.platform.db.runtime_db import (
    completed_work_item_ids,
    get_work_item,
    packet_for_work_item,
)
from agentic_company.platform.db.state import (
    DeliveryState,
    codex_resume_thread_id,
)
from agentic_company.platform.mirror.messages import AgentMessageStore

FULLSTACK_AGENT_GRAPH_NODE_ORDER = AGENT_EXECUTOR_GRAPH_NODE_ORDER
FULLSTACK_AGENT_SYSTEM_PROMPT = """You are the Fullstack Agent for agentic-company.

You own implementation work only through the available tools. Call `codex_exec`
to run the Codex implementation worker for the assigned feature or project.
Do not claim work is complete without calling a tool.
"""


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
    agent_executor: SpecialistAgentExecutor,
    node_order: Sequence[str] | None = None,
):
    """Build the fullstack agent internal graph."""

    order = list(FULLSTACK_AGENT_GRAPH_NODE_ORDER if node_order is None else node_order)
    if not order:
        raise ValueError("Fullstack agent graph requires at least one node.")

    node_map = {
        "prepare_context": _prepare_context,
        "run_agent_executor": _run_agent_executor(runner, agent_executor),
        "apply_result": _apply_result,
    }
    return build_agent_executor_graph(
        FullstackAgentGraphState,
        prepare_node=node_map[order[0]],
        run_agent_executor_node=node_map[order[1]],
        apply_result_node=node_map[order[2]],
        node_order=tuple(order),
    )


def run_fullstack_agent_graph(
    delivery_state: DeliveryState,
    runner: FullstackRunnerLike,
    agent_executor: SpecialistAgentExecutor,
) -> DeliveryState:
    """Run the fullstack agent subgraph and return updated delivery state."""

    graph_state: FullstackAgentGraphState = {
        "delivery_state": delivery_state,
        "run_dir": delivery_state["run_dir"],
    }
    result = build_fullstack_agent_graph(runner, agent_executor=agent_executor).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_fullstack_agent_graph_mermaid() -> str:
    """Render the fullstack agent subgraph as Mermaid text."""

    class NoopRunner:
        def run(self, run_dir: Path) -> AgentRunResult:
            raise RuntimeError("Runner is not available in graph rendering.")

    return (
        build_fullstack_agent_graph(
            cast(FullstackRunnerLike, NoopRunner()),
            agent_executor=cast(SpecialistAgentExecutor, object()),
        )
        .get_graph()
        .draw_mermaid()
    )


def _prepare_context(state: FullstackAgentGraphState) -> FullstackAgentGraphState:
    delivery_state = state["delivery_state"]
    run_dir = Path(state["run_dir"])
    work_item_id = str(delivery_state.get("agent_call_correlation_id") or "").strip()
    updated_delivery_state = {**delivery_state}
    if not work_item_id:
        result = AgentRunResult(
            agent_id="fullstack-agent",
            status="contract_error",
            output_artifacts=[],
            summary="Fullstack contract error: missing explicit work_item_id.",
            execution_id=str(delivery_state.get("agent_execution_id") or ""),
        )
        return {
            **state,
            "delivery_state": cast(DeliveryState, updated_delivery_state),
            "result": result,
            "results": [result],
        }

    work_item = get_work_item(str(delivery_state["run_id"]), work_item_id)
    _write_feature_execution_request(
        run_dir,
        work_item.to_dict(),
        completed_work_item_ids(str(delivery_state["run_id"]), work_item.sprint_id),
        delivery_state,
    )
    return {**state, "delivery_state": cast(DeliveryState, updated_delivery_state)}


def _run_agent_executor(runner: FullstackRunnerLike, agent_executor: SpecialistAgentExecutor):
    def run(state: FullstackAgentGraphState) -> FullstackAgentGraphState:
        if "result" in state:
            return state
        run_dir = Path(state["run_dir"])
        result = agent_executor.run(
            SpecialistAgentRequest(
                agent_id="fullstack-agent",
                agent_name="Fullstack Agent",
                stage="fullstack",
                system_prompt=FULLSTACK_AGENT_SYSTEM_PROMPT,
                user_prompt=_fullstack_user_prompt(state["delivery_state"]),
                runner=runner,
                run_dir=run_dir,
                delivery_state=state["delivery_state"],
                packet=packet_for_work_item(
                    run_id=str(state["delivery_state"]["run_id"]),
                    work_item_id=str(
                        state["delivery_state"].get("agent_call_correlation_id") or ""
                    ),
                    tool_name="run_fullstack",
                    tool_call_id=str(state["delivery_state"].get("agent_execution_id") or ""),
                    attempt_id="1",
                    owner_agent="fullstack-agent",
                ),
            )
        )
        return {
            **state,
            "result": result,
            "results": [result],
        }

    return run


def _fullstack_user_prompt(state: DeliveryState) -> str:
    return json.dumps(
        {
            "task": "Run the assigned Fullstack Codex implementation task.",
            "run_dir": state["run_dir"],
            "work_item_id": state.get("agent_call_correlation_id"),
            "agent_call_message_id": state.get("agent_call_message_id"),
            "agent_execution_id": state.get("agent_execution_id"),
        },
        indent=2,
        sort_keys=True,
    )


def _apply_result(state: FullstackAgentGraphState) -> FullstackAgentGraphState:
    results = state.get("results") or ([state["result"]] if "result" in state else [])
    if not results:
        raise ValueError("Fullstack agent graph result is missing.")

    delivery_state = state["delivery_state"]
    result = results[-1]
    work_item_id = str(delivery_state.get("agent_call_correlation_id") or "")
    updated = record_specialist_completion(
        delivery_state,
        agent_id="fullstack-agent",
        stage="fullstack",
        node_name="fullstack",
        outcome=result.status,
        work_item_id=work_item_id or None,
    )
    if result.status != "codex_completed":
        updated["blockers"] = [
            *updated.get("blockers", []),
            f"Fullstack work item {work_item_id or 'unknown'} did not complete successfully.",
        ]
    extend_artifacts(
        updated,
        artifact_refs(
            [artifact for item in results for artifact in item.output_artifacts],
            kind="execution",
            owner_agent=result.agent_id,
        ),
    )
    if result.status == "codex_completed" and work_item_id:
        try:  # best-effort: branch -> commit -> PR for this feature; never breaks delivery
            from agentic_company.platform.delivery.delivery_pr import publish_work_item_pr

            publish_work_item_pr(str(delivery_state["run_id"]), work_item_id)
        except Exception:
            pass
    append_downstream_response(updated, from_agent="fullstack-agent", result=result)
    return {**state, "delivery_state": updated}


def _write_feature_execution_request(
    run_dir: Path,
    work_item: dict[str, Any],
    completed_work_item_ids: list[str],
    delivery_state: DeliveryState,
) -> None:
    work_item_id = str(work_item["work_item_id"])
    upstream_artifacts = _current_agent_call_artifacts(run_dir, delivery_state)
    instructions = [
        "Read the upstream agent message and artifact refs before editing.",
        "Implement only the explicit work item selected by Team Lead in this Codex run.",
        "Use the work-item title, source refs, dependencies, and acceptance criteria.",
        "Preserve behavior from previously completed work items.",
        "Write a concise execution summary mapping work to the explicit work item.",
        (
            f"Work item for this run: {work_item_id} - {work_item.get('title', '')}. "
            "Implement only this work item in this Codex run."
        ),
        (
            "Already completed work items before this run: "
            + (", ".join(completed_work_item_ids) if completed_work_item_ids else "none")
            + ". Preserve their behavior."
        ),
        (
            "This is a repair run when upstream artifact refs include QA/fix evidence. "
            "Read the upstream Team Lead message and artifact refs before editing. "
            "If this is a repair request, pass through the exact QA findings instead "
            "of relying on derived filenames."
        ),
    ]
    repo_ctx = _run_repo_context(str(delivery_state["run_id"]))
    if repo_ctx:
        pr = _work_item_pr(str(delivery_state["run_id"]), work_item_id)
        existing_note = (
            f" A pull request already tracks this item: {pr.get('url')} — your push updates "
            "that same PR; do not open a second one and do not merge."
            if pr
            else ""
        )
        instructions.append(
            f"A git repository is connected for this run: {repo_ctx['repository']} "
            f"(base branch `{repo_ctx['base_branch']}`). After you finish this work item, DELIVER "
            "IT AS A PULL REQUEST using the git-pr-workflow skill: orient first "
            "(check your current "
            f"branch and recent commits), put your work on branch `adl/{work_item_id.lower()}`, "
            "commit (never secrets), push, and open the PR. Never commit to the base branch "
            "directly; QA reviews and merges the PR." + existing_note
        )
    request = build_execution_request_payload(
        delivery_state,
        agent_id="fullstack-agent",
        model=(
            agent_env_value("FULLSTACK_CODEX_MODEL", delivery_state)
            or agent_env_value("AGENT_CODEX_MODEL", delivery_state)
            or DEFAULT_CODEX_MODEL
        ),
        input_artifacts=upstream_artifacts,
        expected_outputs=[
            "README.md",
            "pyproject.toml",
            "uv.lock",
            ".env.example",
            "execution-summary.md",
        ],
        instructions=instructions,
        constraints=[
            (
                "Do not expand scope beyond the upstream BA, architecture, PM, "
                "and Team Lead artifacts."
            ),
            "Work only inside the generated project directory.",
            "Do not bake secrets into generated code or images.",
        ],
        target_project_dir=str(delivery_state["target_project_dir"]),
        work_item=work_item,
        completed_work_item_ids=completed_work_item_ids,
        codex_resume_thread_id=codex_resume_thread_id(delivery_state, "fullstack-agent"),
    )
    write_execution_request(run_dir, request)


def _work_item_pr(run_id: str, work_item_id: str) -> dict[str, Any] | None:
    """The PR tracking this work item, so the Builder knows its changes update it."""
    try:
        from agentic_company.platform.delivery.delivery_pr import get_work_item_pr

        return get_work_item_pr(run_id, work_item_id)
    except Exception:
        return None


def _run_repo_context(run_id: str) -> dict[str, str] | None:
    """Connected repo info ({repository, base_branch}) so the Builder delivers a PR."""
    try:
        from agentic_company.platform.delivery.delivery_pr import run_repo_context

        return run_repo_context(run_id)
    except Exception:
        return None


def _current_agent_call_artifacts(run_dir: Path, state: DeliveryState) -> list[str]:
    message_id = str(state.get("agent_call_message_id") or "")
    message = AgentMessageStore(run_dir).get(message_id) if message_id else None
    if message and message.artifact_refs:
        return _unique_paths(message.artifact_refs)
    return []


def _unique_paths(paths: list[str]) -> list[str]:
    unique: list[str] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique
