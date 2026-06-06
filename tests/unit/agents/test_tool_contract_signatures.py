import inspect
from pathlib import Path

from agentic_company.agents.head.contracts import HEAD_TOOL_CONTRACT_REGISTRY
from agentic_company.agents.head.executor import langchain_tools as head_langchain_tools
from agentic_company.agents.team_lead.contracts import TEAM_LEAD_TOOL_CONTRACT_REGISTRY
from agentic_company.agents.team_lead.executor import langchain_tools as team_lead_langchain_tools
from agentic_company.platform.agent_runtime import (
    LangChainAgentRuntimeError,
    LangChainSpecialistAgentExecutor,
    SpecialistAgentRequest,
)
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.tool_contracts import (
    CODEX_EXEC_TOOL_CONTRACT,
    WorkItemExecutionPacket,
)


def test_head_tool_contract_parameters_match_callable_signatures():
    tools = {tool.__name__: tool for tool in head_langchain_tools(_NullToolbox())}

    for contract in HEAD_TOOL_CONTRACT_REGISTRY.all():
        signature_params = set(inspect.signature(tools[contract.tool_name]).parameters)
        expected = set(contract.required_parameters) | set(contract.optional_parameters)
        assert expected <= signature_params, contract.tool_name


def test_team_lead_tool_contract_parameters_match_callable_signatures():
    tools = {tool.__name__: tool for tool in team_lead_langchain_tools(_NullToolbox())}

    for contract in TEAM_LEAD_TOOL_CONTRACT_REGISTRY.all():
        signature_params = set(inspect.signature(tools[contract.tool_name]).parameters)
        expected = set(contract.required_parameters) | set(contract.optional_parameters)
        assert expected <= signature_params, contract.tool_name


def test_codex_exec_contract_parameters_match_callable_signature(tmp_path: Path):
    runtime = _CapturingSpecialistRuntime()
    executor = LangChainSpecialistAgentExecutor(runtime=runtime)

    try:
        executor.run(
            SpecialistAgentRequest(
                agent_id="fullstack-agent",
                agent_name="Builder",
                stage="fullstack",
                system_prompt="Use tools.",
                user_prompt="Run.",
                runner=_NoopRunner(),
                run_dir=tmp_path,
                delivery_state={"run_id": "run", "run_dir": str(tmp_path)},
                packet=WorkItemExecutionPacket(
                    run_id="run",
                    work_item_id="US-1",
                    sprint_id="sprint-01",
                    owner_agent="fullstack-agent",
                    tool_name="codex_exec",
                    tool_call_id="call-1",
                    attempt_id="1",
                    status="in_progress",
                ),
            )
        )
    except LangChainAgentRuntimeError:
        pass

    assert runtime.tool is not None
    signature_params = set(inspect.signature(runtime.tool).parameters)
    expected = set(CODEX_EXEC_TOOL_CONTRACT.required_parameters) | set(
        CODEX_EXEC_TOOL_CONTRACT.optional_parameters
    )
    assert expected <= signature_params


class _NullToolbox:
    def __getattr__(self, name):
        def _tool(*args, **kwargs):
            return "{}"

        return _tool


class _CapturingSpecialistRuntime:
    def __init__(self) -> None:
        self.tool = None

    def invoke(self, request):
        self.tool = request.tools[0]
        return {"messages": []}


class _NoopRunner:
    def run(self, run_dir: Path) -> AgentRunResult:
        return AgentRunResult(
            agent_id="fullstack-agent",
            status="codex_completed",
            output_artifacts=[],
            summary="Done.",
        )
