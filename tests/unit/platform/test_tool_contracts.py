import json
from pathlib import Path
from typing import Any

from agentic_company.agents.team_lead.contracts import (
    CRITICAL_TOOL_CONTRACT_REGISTRY,
    CRITICAL_TOOL_CONTRACTS,
    TEAM_LEAD_TOOL_CONTRACT_REGISTRY,
    TEAM_LEAD_TOOLS,
)
from agentic_company.agents.team_lead.executor import langchain_tools
from agentic_company.agents.team_lead.tools import TeamLeadToolbox, TeamLeadWorkers
from agentic_company.platform.agent_runtime import (
    LangChainAgentRequest,
    LangChainSpecialistAgentExecutor,
    SpecialistAgentRequest,
)
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import initial_delivery_state


def test_critical_tool_contracts_are_complete_and_dashboard_ready():
    required_fields = {
        "tool_name",
        "owner_agent",
        "purpose",
        "business_description",
        "input_schema",
        "output_schema",
        "required_parameters",
        "optional_parameters",
        "artifact_inputs",
        "artifact_outputs",
        "status_outputs",
        "failure_modes",
        "retry_policy",
        "idempotency",
        "examples",
        "dashboard_status",
        "dashboard_summary",
        "dashboard_comment",
        "external_reference_type",
        "risk_level",
    }
    expected = {
        "codex_exec",
        "run_fullstack",
        "run_qa",
        "run_deployment",
        "run_post_deploy_qa",
        "run_handoff",
        "inspect_sprint_status",
        "codex_review",
        "deployment_runner",
        "handoff_report_runner",
    }

    assert expected.issubset(set(CRITICAL_TOOL_CONTRACT_REGISTRY.names()))
    for contract in CRITICAL_TOOL_CONTRACTS:
        payload = contract.to_dict()
        assert required_fields.issubset(payload)
        assert contract.input_schema
        assert contract.output_schema
        assert contract.status_outputs
        assert contract.failure_modes
        assert contract.examples
        for example in contract.examples:
            for parameter in contract.required_parameters:
                assert parameter in example


def test_team_lead_exposed_tools_have_contract_rendered_docstrings(tmp_path):
    toolbox = TeamLeadToolbox(
        delivery_state=initial_delivery_state(run_id="docstrings", run_dir=tmp_path),
        sprint={"sprint_id": "sprint-01"},
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=lambda state: state,
            deployment=lambda state: state,
            handoff=lambda state: state,
        ),
        max_steps=6,
    )

    tools = {tool.__name__: tool for tool in langchain_tools(toolbox)}

    assert set(TEAM_LEAD_TOOLS) == set(tools)
    for tool_name, tool in tools.items():
        contract = TEAM_LEAD_TOOL_CONTRACT_REGISTRY.get(tool_name)
        docstring = tool.__doc__ or ""
        assert contract.purpose in docstring
        assert "Required parameters:" in docstring
        assert "Possible statuses:" in docstring
        assert "External dashboard support:" in docstring
        assert "Idempotency:" in docstring
        assert "Example call:" in docstring


def test_team_lead_tool_result_is_structured_and_keeps_legacy_keys(tmp_path):
    state = initial_delivery_state(run_id="structured-team-lead", run_dir=tmp_path)
    state["feature_queue"] = [
        {"id": "F1", "title": "Foundation", "delivery_order": 1, "sprint_id": "sprint-01"}
    ]
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=lambda state: state,
            deployment=lambda state: state,
            handoff=lambda state: state,
        ),
        max_steps=6,
    )

    response = json.loads(toolbox.run_qa(target="F1", reason="Validate.", message="Validate F1."))

    assert response["status"] == "initialized"
    assert response["message"] == "run_qa completed with status initialized."
    assert response["tool_name"] == "run_qa"
    assert response["business_summary"] == response["message"]
    assert isinstance(response["developer_diagnostics"], dict)
    assert isinstance(response["output_artifacts"], list)
    assert response["dashboard_update"]["status"] == "in_progress"
    assert response["dashboard_update"]["summary"] == response["message"]
    assert response["implicit_resolution_warnings"] == []
    assert not _has_secret_key(response)


def test_team_lead_tool_result_reports_implicit_resolution_warning(tmp_path):
    state = initial_delivery_state(run_id="implicit-target", run_dir=tmp_path)
    state["feature_queue"] = [
        {"id": "F1", "title": "Foundation", "delivery_order": 1, "sprint_id": "sprint-01"}
    ]
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=lambda state: state,
            deployment=lambda state: state,
            handoff=lambda state: state,
        ),
        max_steps=6,
    )

    response = json.loads(
        toolbox.run_fullstack(
            target="Fullstack should implement the first item.",
            reason="Start.",
            message="Start work.",
        )
    )

    assert response["tool_name"] == "run_fullstack"
    assert response["implicit_resolution_warnings"]
    assert "legacy target" in response["implicit_resolution_warnings"][0]


def test_codex_exec_docstring_and_result_shape_are_contract_ready(tmp_path):
    runtime = CapturingRuntime()
    executor = LangChainSpecialistAgentExecutor(runtime=runtime)  # type: ignore[arg-type]

    result = executor.run(
        SpecialistAgentRequest(
            agent_id="fullstack-agent",
            agent_name="Builder",
            stage="fullstack",
            system_prompt="Build.",
            user_prompt="Use tools.",
            runner=FakeCodexRunner(),
            run_dir=tmp_path,
            delivery_state=initial_delivery_state(run_id="codex-tool", run_dir=tmp_path),
        )
    )

    assert result.status == "codex_completed"
    assert runtime.tool_docstring is not None
    assert "Required parameters:" in runtime.tool_docstring
    assert "External dashboard support:" in runtime.tool_docstring
    assert runtime.tool_payload["tool_name"] == "codex_exec"
    assert runtime.tool_payload["status"] == "codex_completed"
    assert runtime.tool_payload["business_summary"] == "Done."
    assert runtime.tool_payload["dashboard_update"]["status"] == "done"
    assert runtime.tool_payload["output_artifacts"][0]["path"] == "fullstack/result.json"
    assert runtime.tool_payload["recommended_next_action"] == "Ship it."
    assert not _has_secret_key(runtime.tool_payload)


class FakeCodexRunner:
    def run(self, run_dir: Path) -> AgentRunResult:
        return AgentRunResult(
            agent_id="fullstack-agent",
            status="codex_completed",
            output_artifacts=["fullstack/result.json"],
            summary="Done.",
            execution_id="exec-1",
            codex_thread_id="thread-1",
            recommended_next_action="Ship it.",
        )


class CapturingRuntime:
    tool_payload: dict[str, Any]
    tool_docstring: str | None

    def __init__(self) -> None:
        self.tool_payload = {}
        self.tool_docstring = None

    def invoke(self, request: LangChainAgentRequest) -> dict[str, object]:
        tool = request.tools[0]
        self.tool_docstring = tool.__doc__
        self.tool_payload = json.loads(tool(reason="Run.", message="Execute."))
        return {"output": "Agent accepted the Codex result."}


def _has_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if "secret" in lowered or "api_key" in lowered or "token" in lowered:
                return True
            if _has_secret_key(item):
                return True
    if isinstance(value, list):
        return any(_has_secret_key(item) for item in value)
    return False
