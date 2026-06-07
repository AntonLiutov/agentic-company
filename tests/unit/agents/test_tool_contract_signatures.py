import inspect
from pathlib import Path

from agentic_company.agents.head.contracts import HEAD_TOOL_CONTRACT_REGISTRY
from agentic_company.agents.head.executor import langchain_tools as head_langchain_tools
from agentic_company.agents.team_lead.contracts import TEAM_LEAD_TOOL_CONTRACT_REGISTRY
from agentic_company.agents.team_lead.executor import langchain_tools as team_lead_langchain_tools
from agentic_company.console.web.db import ConsoleRepository
from agentic_company.platform.agent_runtime import (
    LangChainAgentRuntimeError,
    LangChainSpecialistAgentExecutor,
    SpecialistAgentRequest,
)
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.runtime_db import materialize_planning_items
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


def test_agent_executor_feedback_artifacts_are_registered_in_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "console.db"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    target_project_dir = run_dir / "generated-project"
    source = target_project_dir / "web" / "app.js"
    source.parent.mkdir(parents=True)
    source.write_text("console.log('app')\n", encoding="utf-8")
    monkeypatch.setenv("AGENTIC_CONSOLE_DB_PATH", str(db_path))
    monkeypatch.delenv("AGENTIC_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repo = ConsoleRepository(db_path)
    repo.init_schema()
    user = repo.create_user(
        email="feedback@example.test",
        username="feedback-user",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Feedback",
        request_text="Build",
        mode="simple_prototype",
        complexity="simple",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="run",
        run_dir=run_dir,
        status="running",
        mode="simple_prototype",
        reasoning="medium",
    )
    materialize_planning_items("run")
    executor = LangChainSpecialistAgentExecutor(runtime=_DoubleToolCallRuntime())

    executor.run(
        SpecialistAgentRequest(
            agent_id="fullstack-agent",
            agent_name="Builder",
            stage="fullstack",
            system_prompt="Use tools.",
            user_prompt="Run.",
            runner=_SequencedRunner(),
            run_dir=run_dir,
            delivery_state={
                "run_id": "run",
                "run_dir": str(run_dir),
                "target_project_dir": str(target_project_dir),
            },
            packet=WorkItemExecutionPacket(
                run_id="run",
                work_item_id="PLAN-04",
                sprint_id="Planning",
                owner_agent="fullstack-agent",
                tool_name="codex_exec",
                tool_call_id="call-1",
                attempt_id="1",
                status="in_progress",
            ),
        )
    )

    artifact_paths = {record.relative_path for record in repo.list_artifact_records(run.id)}
    assert "agent-executor/fullstack-agent/feedback-01.json" in artifact_paths
    assert "agent-executor/fullstack-agent/feedback-02.json" in artifact_paths
    implementation = [
        record
        for record in repo.list_artifact_records(run.id)
        if record.relative_path == "generated-project/web/app.js"
    ]
    assert len(implementation) == 1
    assert implementation[0].artifact_type == "implementation_artifact"
    assert implementation[0].visibility == "developer"


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


class _DoubleToolCallRuntime:
    def invoke(self, request):
        tool = request.tools[0]
        tool(reason="initial attempt")
        tool(reason="repair attempt")
        return {"messages": []}


class _SequencedRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, run_dir: Path) -> AgentRunResult:
        self.calls += 1
        if self.calls == 1:
            return AgentRunResult(
                agent_id="fullstack-agent",
                status="codex_failed",
                output_artifacts=[],
                summary="Failed.",
                blocking_findings=["Needs repair."],
            )
        return AgentRunResult(
            agent_id="fullstack-agent",
            status="codex_completed",
            output_artifacts=[],
            summary="Done.",
        )
