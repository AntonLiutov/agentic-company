"""Internal LangGraph for the QA specialist."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from agentic_company.agents.base import artifact_refs, extend_artifacts
from agentic_company.agents.quality.docker_checks import (
    run_docker_compose_config,
    run_docker_runtime_e2e,
)
from agentic_company.agents.quality.docker_summary import write_docker_build_summary
from agentic_company.agents.quality.models import (
    CommandExecutor,
    QualityCheckResult,
    QualityTestPlanItem,
)
from agentic_company.agents.quality.plan import build_test_plan
from agentic_company.agents.quality.playwright_checks import run_playwright_live_chat
from agentic_company.agents.quality.python_checks import (
    run_python_compile,
    run_streamlit_apptest,
    run_uv_sync,
)
from agentic_company.agents.quality.reports import render_qa_report
from agentic_company.agents.quality.static_checks import (
    check_expected_outputs,
    check_no_secrets,
    check_readme_operational_docs,
)
from agentic_company.platform.artifacts import load_execution_request
from agentic_company.platform.events import write_event
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import DeliveryState, mark_node_completed

LOGGER = logging.getLogger(__name__)

QUALITY_AGENT_ID = "qa-agent"
QA_REPORT_FILENAME = "08-qa-report.md"
QA_TEST_PLAN_FILENAME = "qa/test-plan.json"
QA_RESULTS_FILENAME = "qa/results.json"
QA_COMMANDS_LOG_FILENAME = "qa/commands.log"
QA_DOCKER_SUMMARY_FILENAME = "qa/docker/build-summary.json"
QA_OUTPUT_ARTIFACTS = [
    QA_REPORT_FILENAME,
    QA_TEST_PLAN_FILENAME,
    QA_RESULTS_FILENAME,
    QA_COMMANDS_LOG_FILENAME,
    QA_DOCKER_SUMMARY_FILENAME,
]

QUALITY_AGENT_GRAPH_NODE_ORDER = [
    "prepare_context",
    "check_existing_evidence",
    "prepare_evidence",
    "build_test_plan",
    "artifact_checks",
    "static_security_checks",
    "python_checks",
    "docker_checks",
    "browser_checks",
    "summarize_results",
    "write_report",
    "apply_result",
]


class QualityAgentGraphState(TypedDict):
    """Internal state for the QA specialist subgraph."""

    run_dir: str
    command_executor: NotRequired[CommandExecutor | None]
    command_timeout_seconds: NotRequired[int]
    force: NotRequired[bool]
    delivery_state: NotRequired[DeliveryState]
    run_id: NotRequired[str]
    target_dir: NotRequired[str]
    expected_outputs: NotRequired[list[str]]
    event_log: NotRequired[str]
    qa_path: NotRequired[str]
    test_plan_path: NotRequired[str]
    results_path: NotRequired[str]
    commands_log_path: NotRequired[str]
    docker_summary_path: NotRequired[str]
    already_completed: NotRequired[bool]
    test_plan: NotRequired[list[QualityTestPlanItem]]
    checks: NotRequired[list[QualityCheckResult]]
    status: NotRequired[str]
    report: NotRequired[str]
    result: NotRequired[AgentRunResult]


def build_quality_agent_graph(
    *,
    node_order: Sequence[str] | None = None,
):
    """Build the QA specialist internal graph."""

    order = list(QUALITY_AGENT_GRAPH_NODE_ORDER if node_order is None else node_order)
    if not order:
        raise ValueError("QA agent graph requires at least one node.")

    graph = StateGraph(QualityAgentGraphState)
    node_map = {
        "prepare_context": _prepare_context,
        "check_existing_evidence": _check_existing_evidence,
        "prepare_evidence": _prepare_evidence,
        "build_test_plan": _build_test_plan_node,
        "artifact_checks": _artifact_checks,
        "static_security_checks": _static_security_checks,
        "python_checks": _python_checks,
        "docker_checks": _docker_checks,
        "browser_checks": _browser_checks,
        "summarize_results": _summarize_results,
        "write_report": _write_report,
        "apply_result": _apply_result,
    }
    for name in order:
        graph.add_node(name, node_map[name])

    graph.add_edge(START, order[0])
    for current, next_node in zip(order, order[1:], strict=False):
        graph.add_edge(current, next_node)
    graph.add_edge(order[-1], END)
    return graph.compile()


def run_quality_workflow_graph(
    run_dir: Path,
    *,
    command_executor: CommandExecutor | None = None,
    command_timeout_seconds: int = 300,
    force: bool = False,
) -> AgentRunResult:
    """Run the QA subgraph as a standalone runner facade."""

    graph_state: QualityAgentGraphState = {
        "run_dir": str(run_dir),
        "command_executor": command_executor,
        "command_timeout_seconds": command_timeout_seconds,
        "force": force,
    }
    result = build_quality_agent_graph().invoke(graph_state)
    return cast(AgentRunResult, result["result"])


def run_quality_agent_graph(
    delivery_state: DeliveryState,
    *,
    command_executor: CommandExecutor | None = None,
    command_timeout_seconds: int = 300,
    force: bool = False,
) -> DeliveryState:
    """Run the QA agent subgraph and return updated delivery state."""

    graph_state: QualityAgentGraphState = {
        "delivery_state": delivery_state,
        "run_dir": delivery_state["run_dir"],
        "command_executor": command_executor,
        "command_timeout_seconds": command_timeout_seconds,
        "force": force,
    }
    result = build_quality_agent_graph().invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_quality_agent_graph_mermaid() -> str:
    """Render the QA agent subgraph as Mermaid text."""

    return build_quality_agent_graph().get_graph().draw_mermaid()


def run_qa_checks(
    target_dir: Path,
    expected_outputs: list[str],
    *,
    command_executor: CommandExecutor | None,
    command_timeout_seconds: int,
    commands_log_path: Path,
) -> list[QualityCheckResult]:
    """Run QA checks without writing the surrounding QA report artifacts."""

    checks: list[QualityCheckResult] = []
    LOGGER.info("QA check plan started target_dir=%s", target_dir)
    checks.extend(check_expected_outputs(target_dir, expected_outputs))
    checks.extend(_run_static_security_checks(target_dir))
    checks.extend(
        _run_python_checks(
            target_dir,
            command_executor=command_executor,
            timeout_seconds=command_timeout_seconds,
            commands_log_path=commands_log_path,
        )
    )
    checks.extend(
        _run_docker_checks(
            target_dir,
            command_executor=command_executor,
            timeout_seconds=command_timeout_seconds,
            commands_log_path=commands_log_path,
        )
    )
    checks.append(
        run_playwright_live_chat(
            target_dir,
            command_executor=command_executor,
            timeout_seconds=command_timeout_seconds,
            commands_log_path=commands_log_path,
        )
    )
    LOGGER.info("QA check plan completed target_dir=%s checks=%s", target_dir, len(checks))
    return checks


def summarize_status(checks: list[QualityCheckResult]) -> str:
    """Summarize QA checks into the current pass/fail status."""

    if any(check.status == "failed" for check in checks):
        return "failed"
    return "passed"


def _prepare_context(state: QualityAgentGraphState) -> QualityAgentGraphState:
    run_dir = Path(state["run_dir"])
    request = load_execution_request(run_dir)
    target_dir = Path(request.target_project_dir)
    return {
        **state,
        "run_id": request.run_id,
        "target_dir": str(target_dir),
        "expected_outputs": request.expected_outputs,
        "event_log": str(run_dir / "events.jsonl"),
        "qa_path": str(run_dir / QA_REPORT_FILENAME),
        "test_plan_path": str(run_dir / QA_TEST_PLAN_FILENAME),
        "results_path": str(run_dir / QA_RESULTS_FILENAME),
        "commands_log_path": str(run_dir / QA_COMMANDS_LOG_FILENAME),
        "docker_summary_path": str(run_dir / QA_DOCKER_SUMMARY_FILENAME),
    }


def _check_existing_evidence(state: QualityAgentGraphState) -> QualityAgentGraphState:
    if state.get("force", False):
        return {**state, "already_completed": False}

    paths = [
        Path(state["qa_path"]),
        Path(state["test_plan_path"]),
        Path(state["results_path"]),
        Path(state["commands_log_path"]),
        Path(state["docker_summary_path"]),
    ]
    if not all(path.exists() for path in paths):
        return {**state, "already_completed": False}

    LOGGER.info("QA already completed run_dir=%s", state["run_dir"])
    result = AgentRunResult(
        agent_id=QUALITY_AGENT_ID,
        status="already_completed",
        output_artifacts=QA_OUTPUT_ARTIFACTS,
        summary=Path(state["qa_path"]).read_text(encoding="utf-8"),
    )
    return {**state, "already_completed": True, "result": result}


def _prepare_evidence(state: QualityAgentGraphState) -> QualityAgentGraphState:
    if state.get("already_completed", False):
        return state

    qa_path = Path(state["qa_path"])
    test_plan_path = Path(state["test_plan_path"])
    results_path = Path(state["results_path"])
    commands_log_path = Path(state["commands_log_path"])

    qa_path.parent.mkdir(parents=True, exist_ok=True)
    test_plan_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    commands_log_path.write_text("", encoding="utf-8")

    LOGGER.info("QA started run_id=%s target_dir=%s", state["run_id"], state["target_dir"])
    write_event(
        Path(state["event_log"]),
        state["run_id"],
        QUALITY_AGENT_ID,
        "qa_started",
        {"target_project_dir": state["target_dir"]},
    )
    return state


def _build_test_plan_node(state: QualityAgentGraphState) -> QualityAgentGraphState:
    if state.get("already_completed", False):
        return state

    test_plan = build_test_plan(state["expected_outputs"])
    Path(state["test_plan_path"]).write_text(
        json.dumps([item.to_dict() for item in test_plan], indent=2) + "\n",
        encoding="utf-8",
    )
    write_event(
        Path(state["event_log"]),
        state["run_id"],
        QUALITY_AGENT_ID,
        "artifact_written",
        {"artifact": QA_TEST_PLAN_FILENAME},
    )
    return {**state, "test_plan": test_plan, "checks": []}


def _artifact_checks(state: QualityAgentGraphState) -> QualityAgentGraphState:
    if state.get("already_completed", False):
        return state
    checks = [
        *state.get("checks", []),
        *check_expected_outputs(Path(state["target_dir"]), state["expected_outputs"]),
    ]
    return {**state, "checks": checks}


def _static_security_checks(state: QualityAgentGraphState) -> QualityAgentGraphState:
    if state.get("already_completed", False):
        return state
    checks = [
        *state.get("checks", []),
        *_run_static_security_checks(Path(state["target_dir"])),
    ]
    return {**state, "checks": checks}


def _python_checks(state: QualityAgentGraphState) -> QualityAgentGraphState:
    if state.get("already_completed", False):
        return state
    checks = [
        *state.get("checks", []),
        *_run_python_checks(
            Path(state["target_dir"]),
            command_executor=state.get("command_executor"),
            timeout_seconds=state.get("command_timeout_seconds", 300),
            commands_log_path=Path(state["commands_log_path"]),
        ),
    ]
    return {**state, "checks": checks}


def _docker_checks(state: QualityAgentGraphState) -> QualityAgentGraphState:
    if state.get("already_completed", False):
        return state
    checks = [
        *state.get("checks", []),
        *_run_docker_checks(
            Path(state["target_dir"]),
            command_executor=state.get("command_executor"),
            timeout_seconds=state.get("command_timeout_seconds", 300),
            commands_log_path=Path(state["commands_log_path"]),
        ),
    ]
    return {**state, "checks": checks}


def _browser_checks(state: QualityAgentGraphState) -> QualityAgentGraphState:
    if state.get("already_completed", False):
        return state
    checks = [
        *state.get("checks", []),
        run_playwright_live_chat(
            Path(state["target_dir"]),
            command_executor=state.get("command_executor"),
            timeout_seconds=state.get("command_timeout_seconds", 300),
            commands_log_path=Path(state["commands_log_path"]),
        ),
    ]
    return {**state, "checks": checks}


def _summarize_results(state: QualityAgentGraphState) -> QualityAgentGraphState:
    if state.get("already_completed", False):
        return state

    checks = state.get("checks", [])
    status = summarize_status(checks)
    docker_summary_path = write_docker_build_summary(Path(state["run_dir"]))
    LOGGER.info("QA checks finished run_id=%s status=%s", state["run_id"], status)
    results = {
        "status": status,
        "target_project_dir": state["target_dir"],
        "docker_build_summary": docker_summary_path.relative_to(Path(state["run_dir"])).as_posix(),
        "checks": [check.to_dict() for check in checks],
    }
    Path(state["results_path"]).write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**state, "status": status, "docker_summary_path": str(docker_summary_path)}


def _write_report(state: QualityAgentGraphState) -> QualityAgentGraphState:
    if state.get("already_completed", False):
        return state

    report = render_qa_report(
        Path(state["run_dir"]),
        Path(state["target_dir"]),
        state.get("checks", []),
        state["status"],
        state["test_plan"],
    )
    Path(state["qa_path"]).write_text(report, encoding="utf-8")

    write_event(
        Path(state["event_log"]),
        state["run_id"],
        QUALITY_AGENT_ID,
        "artifact_written",
        {
            "artifact": QA_REPORT_FILENAME,
            "results": QA_RESULTS_FILENAME,
            "status": state["status"],
        },
    )
    write_event(
        Path(state["event_log"]),
        state["run_id"],
        QUALITY_AGENT_ID,
        "qa_completed",
        {"status": state["status"]},
    )
    LOGGER.info("QA completed run_id=%s status=%s", state["run_id"], state["status"])

    result = AgentRunResult(
        agent_id=QUALITY_AGENT_ID,
        status=f"qa_{state['status']}",
        output_artifacts=QA_OUTPUT_ARTIFACTS,
        summary=report,
    )
    return {**state, "report": report, "result": result}


def _apply_result(state: QualityAgentGraphState) -> QualityAgentGraphState:
    result = state.get("result")
    if result is None:
        raise ValueError("QA agent graph result is missing.")

    delivery_state = state.get("delivery_state")
    if delivery_state is None:
        return state

    qa_status = result.status.removeprefix("qa_")
    updated = mark_node_completed(
        delivery_state,
        node_name="qa",
        stage="qa",
        status=result.status,
    )
    updated["qa_status"] = qa_status
    extend_artifacts(
        updated,
        artifact_refs(result.output_artifacts, kind="qa", owner_agent=result.agent_id),
    )
    return {**state, "delivery_state": updated}


def _run_static_security_checks(target_dir: Path) -> list[QualityCheckResult]:
    return [
        check_no_secrets(target_dir),
        check_readme_operational_docs(target_dir),
    ]


def _run_python_checks(
    target_dir: Path,
    *,
    command_executor: CommandExecutor | None,
    timeout_seconds: int,
    commands_log_path: Path,
) -> list[QualityCheckResult]:
    return [
        run_uv_sync(
            target_dir,
            command_executor=command_executor,
            timeout_seconds=timeout_seconds,
            commands_log_path=commands_log_path,
        ),
        run_python_compile(
            target_dir,
            command_executor=command_executor,
            timeout_seconds=timeout_seconds,
            commands_log_path=commands_log_path,
        ),
        run_streamlit_apptest(
            target_dir,
            command_executor=command_executor,
            timeout_seconds=timeout_seconds,
            commands_log_path=commands_log_path,
        ),
    ]


def _run_docker_checks(
    target_dir: Path,
    *,
    command_executor: CommandExecutor | None,
    timeout_seconds: int,
    commands_log_path: Path,
) -> list[QualityCheckResult]:
    return [
        run_docker_compose_config(
            target_dir,
            command_executor=command_executor,
            timeout_seconds=timeout_seconds,
            commands_log_path=commands_log_path,
        ),
        run_docker_runtime_e2e(
            target_dir,
            command_executor=command_executor,
            timeout_seconds=timeout_seconds,
            commands_log_path=commands_log_path,
        ),
    ]
