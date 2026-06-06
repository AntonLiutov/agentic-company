"""Internal LangGraph for the QA Agent."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NotRequired, Protocol, TypedDict, cast

from agentic_company.agents.quality.codex_cli import (
    QUALITY_CODEX_AGENT_ID,
    QualityCodexRunner,
)
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
from agentic_company.platform.artifacts import (
    build_execution_request_payload,
    update_execution_request_context,
    write_execution_request,
)
from agentic_company.platform.events import write_event
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.runtime_db import (
    completed_work_item_ids,
    get_work_item,
    packet_for_work_item,
)
from agentic_company.platform.state import (
    DeliveryState,
    codex_resume_thread_id,
    mark_node_completed,
)

QUALITY_AGENT_ID = "qa-agent"

QUALITY_AGENT_GRAPH_NODE_ORDER = AGENT_EXECUTOR_GRAPH_NODE_ORDER
QUALITY_AGENT_SYSTEM_PROMPT = """You are the QA Agent for agentic-company.

You own validation work only through the available tools. Call `codex_exec` to
run the Codex QA worker for the assigned work item, deployment, or release check.
Do not claim QA is complete without calling a tool.
"""


class FeatureQaRunner(Protocol):
    """Feature-scoped Codex QA execution boundary."""

    def run(self, run_dir: Path) -> AgentRunResult:
        """Run QA for the active work item and return the parsed QA result."""


class QualityAgentGraphState(TypedDict):
    """Internal state for the QA Agent subgraph."""

    delivery_state: DeliveryState
    run_dir: str
    work_item_id: NotRequired[str | None]
    result: NotRequired[AgentRunResult]
    status: NotRequired[str]


def build_quality_agent_graph(
    runner: FeatureQaRunner | None = None,
    *,
    agent_executor: SpecialistAgentExecutor,
    node_order: Sequence[str] | None = None,
):
    """Build the QA Agent internal graph.

    The graph is intentionally generic. It does not encode concrete QA commands,
    endpoint names, browser scripts, or Docker checks. Those choices belong to the
    Codex QA specialist inside the `codex_quality_execution` node.
    """

    order = list(QUALITY_AGENT_GRAPH_NODE_ORDER if node_order is None else node_order)
    if not order:
        raise ValueError("Quality agent graph requires at least one node.")

    node_map = {
        "prepare_context": _prepare_context,
        "run_agent_executor": _run_agent_executor(runner, agent_executor),
        "apply_result": _apply_quality_result,
    }
    return build_agent_executor_graph(
        QualityAgentGraphState,
        prepare_node=node_map[order[0]],
        run_agent_executor_node=node_map[order[1]],
        apply_result_node=node_map[order[2]],
        node_order=tuple(order),
    )


def run_quality_agent_graph(
    delivery_state: DeliveryState,
    *,
    runner: FeatureQaRunner | None = None,
    agent_executor: SpecialistAgentExecutor,
) -> DeliveryState:
    """Run the QA Agent subgraph and return updated delivery state."""

    graph_state: QualityAgentGraphState = {
        "delivery_state": delivery_state,
        "run_dir": delivery_state["run_dir"],
    }
    result = build_quality_agent_graph(runner, agent_executor=agent_executor).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_quality_agent_graph_mermaid() -> str:
    """Render the QA Agent subgraph as Mermaid text."""

    class NoopRunner:
        def run(self, run_dir: Path) -> AgentRunResult:
            raise RuntimeError("Runner is not available in graph rendering.")

    return (
        build_quality_agent_graph(
            cast(FeatureQaRunner, NoopRunner()),
            agent_executor=cast(SpecialistAgentExecutor, object()),
        )
        .get_graph()
        .draw_mermaid()
    )


def _prepare_context(state: QualityAgentGraphState) -> QualityAgentGraphState:
    delivery_state = state["delivery_state"]
    work_item_id = str(delivery_state.get("agent_call_correlation_id") or "").strip()
    if not work_item_id:
        result = AgentRunResult(
            agent_id=QUALITY_AGENT_ID,
            status="contract_error",
            output_artifacts=[],
            summary="QA contract error: missing explicit work_item_id.",
            execution_id=str(delivery_state.get("agent_execution_id") or ""),
            recommended_next_action="Team Lead must send QA an explicit work_item_id.",
        )
        return {**state, "work_item_id": None, "result": result}

    run_dir = Path(state["run_dir"])
    work_item = get_work_item(str(delivery_state["run_id"]), work_item_id)
    _write_quality_execution_request(run_dir, delivery_state, work_item.to_dict())
    update_execution_request_context(
        run_dir,
        execution_id=str(delivery_state.get("agent_execution_id") or ""),
        execution_intent=str(delivery_state.get("agent_execution_intent") or ""),
        parent_message_id=str(delivery_state.get("agent_call_message_id") or ""),
        codex_resume_thread_id=codex_resume_thread_id(delivery_state, QUALITY_CODEX_AGENT_ID),
        work_item=work_item.to_dict(),
        completed_work_item_ids=completed_work_item_ids(
            str(delivery_state["run_id"]), work_item.sprint_id
        ),
    )
    return {**state, "work_item_id": work_item.work_item_id}


def _write_quality_execution_request(
    run_dir: Path,
    delivery_state: DeliveryState,
    work_item: dict[str, Any],
) -> None:
    work_item_id = str(work_item["work_item_id"])
    request = build_execution_request_payload(
        delivery_state,
        agent_id=QUALITY_AGENT_ID,
        model=(
            agent_env_value("QUALITY_CODEX_MODEL", delivery_state)
            or agent_env_value("AGENT_CODEX_MODEL", delivery_state)
            or DEFAULT_CODEX_MODEL
        ),
        input_artifacts=_quality_input_artifacts(delivery_state),
        expected_outputs=[
            f"08-qa-report-{work_item_id}.md",
            f"qa/results-{work_item_id}.json",
        ],
        instructions=[
            (
                "Read the current work item, upstream planning artifacts, "
                "implementation summary, and QA evidence before testing."
            ),
            (
                "Validate the assigned work item or release target against its "
                "acceptance criteria and definition of done."
            ),
            "Keep QA focused on evidence and do not perform implementation work.",
            "Return explicit artifact refs, test evidence, defects, and QA status.",
        ],
        constraints=[
            (
                "Do not change product code unless the upstream request explicitly asks "
                "for a QA-owned repair artifact."
            ),
            "Do not invent passing evidence; report blocked or failed when evidence is missing.",
            "Keep checks proportional to the assigned work item.",
        ],
        target_project_dir=str(delivery_state["target_project_dir"]),
        work_item=work_item,
        completed_work_item_ids=completed_work_item_ids(
            str(delivery_state["run_id"]), str(work_item.get("sprint_id") or "")
        ),
        codex_resume_thread_id=codex_resume_thread_id(delivery_state, QUALITY_CODEX_AGENT_ID),
    )
    write_execution_request(run_dir, request)


def _quality_input_artifacts(delivery_state: DeliveryState) -> list[str]:
    paths = ["00-requirements.md"]
    return _unique_paths(paths)


def _run_agent_executor(runner: FeatureQaRunner | None, agent_executor: SpecialistAgentExecutor):
    def run(state: QualityAgentGraphState) -> QualityAgentGraphState:
        work_item_id = state.get("work_item_id")
        if "work_item_id" not in state:
            return state

        delivery_state = state["delivery_state"]
        run_dir = Path(state["run_dir"])
        event_log = run_dir
        event_data = {"work_item_id": work_item_id} if work_item_id else {}
        write_event(event_log, delivery_state["run_id"], QUALITY_AGENT_ID, "qa_started", event_data)
        result = agent_executor.run(
            SpecialistAgentRequest(
                agent_id=QUALITY_AGENT_ID,
                agent_name="QA Agent",
                stage="qa",
                system_prompt=QUALITY_AGENT_SYSTEM_PROMPT,
                user_prompt=_quality_user_prompt(delivery_state, work_item_id),
                runner=runner or QualityCodexRunner(),
                run_dir=run_dir,
                delivery_state=delivery_state,
                packet=packet_for_work_item(
                    run_id=str(delivery_state["run_id"]),
                    work_item_id=str(work_item_id or ""),
                    tool_name="run_qa",
                    tool_call_id=str(delivery_state.get("agent_execution_id") or ""),
                    attempt_id="1",
                    owner_agent=QUALITY_AGENT_ID,
                ),
            )
        )
        return {**state, "result": result, "status": _normalize_qa_status(result.status)}

    return run


def _apply_quality_result(state: QualityAgentGraphState) -> QualityAgentGraphState:
    result = state.get("result")
    work_item_id = state.get("work_item_id")
    if result is None:
        return state

    delivery_state = state["delivery_state"]
    status = state.get("status") or _normalize_qa_status(result.status)
    event_log = Path(state["run_dir"])
    artifact = (
        _primary_report_artifact(str(work_item_id), result.output_artifacts)
        if work_item_id
        else (result.output_artifacts[0] if result.output_artifacts else "08-qa-report.md")
    )
    event_data: dict[str, object] = {"artifact": artifact, "status": status}
    if work_item_id:
        event_data["work_item_id"] = work_item_id
    write_event(
        event_log,
        delivery_state["run_id"],
        QUALITY_AGENT_ID,
        "artifact_written",
        event_data,
    )
    completion_data: dict[str, object] = {"status": status}
    if work_item_id:
        completion_data["work_item_id"] = work_item_id
    write_event(
        event_log,
        delivery_state["run_id"],
        QUALITY_AGENT_ID,
        "qa_completed",
        completion_data,
    )

    updated = mark_node_completed(
        delivery_state,
        node_name=f"qa:{work_item_id}" if work_item_id else "qa",
        stage="qa",
        status=f"qa_{status}" if work_item_id else result.status,
    )
    updated["qa_status"] = status
    extend_artifacts(
        updated,
        artifact_refs(
            result.output_artifacts,
            kind="qa",
            owner_agent=QUALITY_AGENT_ID,
        ),
    )
    append_downstream_response(updated, from_agent=QUALITY_AGENT_ID, result=result)

    if work_item_id:
        if status == "passed":
            updated = _mark_feature_passed(updated, work_item_id)
        else:
            updated = _mark_feature_failed(updated, work_item_id)
    return {**state, "delivery_state": updated}


def _quality_user_prompt(state: DeliveryState, work_item_id: str | None) -> str:
    return json.dumps(
        {
            "task": "Run the assigned QA Codex task.",
            "run_dir": state["run_dir"],
            "work_item_id": work_item_id,
            "deployment_status": state.get("deployment_status"),
            "public_url": state.get("public_url"),
            "agent_call_message_id": state.get("agent_call_message_id"),
            "agent_execution_id": state.get("agent_execution_id"),
        },
        indent=2,
        sort_keys=True,
    )


def _primary_report_artifact(work_item_id: str, artifacts: list[str]) -> str:
    for artifact in artifacts:
        if artifact.lower().endswith(".md") and "qa-report" in artifact.lower():
            return artifact
    return artifacts[0] if artifacts else f"qa-report-{work_item_id}.md"


def _normalize_qa_status(status: str) -> str:
    normalized = status.removeprefix("qa_").removeprefix("codex_")
    return "passed" if normalized == "passed" else "failed"


def _mark_feature_passed(state: DeliveryState, work_item_id: str) -> DeliveryState:
    updated = {**state}
    updated["status"] = "qa_feature_passed_next_feature_ready"
    return updated


def _mark_feature_failed(state: DeliveryState, work_item_id: str) -> DeliveryState:
    updated = {**state}
    attempts = dict(updated.get("work_item_repair_attempts", {}))
    attempts[work_item_id] = attempts.get(work_item_id, 0) + 1
    updated["work_item_repair_attempts"] = attempts
    signatures, repeated_signature = _record_failure_signature(updated, work_item_id)
    if signatures:
        updated["work_item_failure_signatures"] = signatures

    if repeated_signature:
        updated["status"] = "qa_feature_failed_blocked"
        updated["blockers"] = [
            *updated.get("blockers", []),
            f"QA repeated the same failure signature for work item {work_item_id}: "
            f"{repeated_signature}.",
        ]
    elif attempts[work_item_id] >= updated.get("max_repair_attempts", 5):
        updated["status"] = "qa_feature_failed_blocked"
        updated["blockers"] = [
            *updated.get("blockers", []),
            f"QA failed work item {work_item_id} after {attempts[work_item_id]} attempts.",
        ]
    else:
        updated["status"] = "qa_feature_failed_repair_ready"
    return updated


def _record_failure_signature(
    state: DeliveryState,
    work_item_id: str,
) -> tuple[dict[str, list[str]], str]:
    signature = _latest_failure_signature(Path(state["run_dir"]), work_item_id)
    current = state.get("work_item_failure_signatures", {})
    signatures = (
        {
            str(key): [str(item) for item in value]
            for key, value in current.items()
            if isinstance(value, list)
        }
        if isinstance(current, dict)
        else {}
    )
    if not signature:
        return signatures, ""
    seen = [*signatures.get(work_item_id, []), signature]
    signatures[work_item_id] = seen
    repeated = signature if seen.count(signature) >= 2 else ""
    return signatures, repeated


def _latest_failure_signature(run_dir: Path, work_item_id: str) -> str:
    path = run_dir / f"10-fix-request-{work_item_id}.json"
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("failure_signature") or "")


def _unique_paths(paths: list[str]) -> list[str]:
    unique: list[str] = []
    for path in paths:
        if path and path not in unique:
            unique.append(path)
    return unique
