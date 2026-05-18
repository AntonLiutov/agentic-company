import pytest

from agentic_company.platform.agent_runtime import (
    AGENT_EXECUTOR_GRAPH_NODE_ORDER,
    LangChainAgentRequest,
    LangChainCreateAgentRuntime,
    LangChainSpecialistAgentExecutor,
    MissingAgentRuntimeConfig,
    SpecialistAgentRequest,
    agent_env_value,
    build_agent_executor_graph,
)
from agentic_company.platform.messages import AgentMessageStore
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import initial_delivery_state


class FakeAgent:
    def __init__(self, calls: list[dict[str, object]]) -> None:
        self.calls = calls

    def invoke(self, input: dict[str, object], config: dict[str, object] | None = None) -> None:
        self.calls.append({"input": input, "config": config})


class ToolCallingAgent:
    def __init__(self, tools) -> None:
        self.tools = tools

    def invoke(self, input: dict[str, object], config: dict[str, object] | None = None):
        self.tools[0](reason="run specialist", message="execute")
        return {"messages": [{"role": "assistant", "content": "Specialist accepted result."}]}


class RepairingToolCallingAgent:
    def __init__(self, tools, tool_responses: list[str]) -> None:
        self.tools = tools
        self.tool_responses = tool_responses

    def invoke(self, input: dict[str, object], config: dict[str, object] | None = None):
        self.tool_responses.append(
            self.tools[0](reason="initial", message="write required artifacts")
        )
        self.tool_responses.append(
            self.tools[0](
                reason="repair",
                message="Fix the contract errors from the previous Codex result.",
            )
        )
        return {"output": "Repaired after reviewing the second Codex result."}


class FakeSpecialistRunner:
    def __init__(self) -> None:
        self.run_dirs = []

    def run(self, run_dir):
        self.run_dirs.append(run_dir)
        return AgentRunResult(
            agent_id="specialist-agent",
            status="codex_completed",
            output_artifacts=["summary.md"],
            summary="done",
        )


class SequenceSpecialistRunner:
    def __init__(self, results: list[AgentRunResult]) -> None:
        self.results = results
        self.run_dirs = []

    def run(self, run_dir):
        self.run_dirs.append(run_dir)
        return self.results.pop(0)


def test_langchain_create_agent_runtime_invokes_agent_with_scoped_prompt(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_ROUTER_MODEL", raising=False)
    monkeypatch.delenv("AGENT_LLM_MODEL", raising=False)
    run_dir = tmp_path / "run"
    target_dir = run_dir / "generated-project"
    target_dir.mkdir(parents=True)
    (target_dir / ".env").write_text(
        "OPENAI_API_KEY=sk-test\nAGENT_ROUTER_MODEL=gpt-router-test\n"
        "AGENT_LLM_MODEL=gpt-agent-test\n",
        encoding="utf-8",
    )
    state = initial_delivery_state(
        run_id="run",
        run_dir=run_dir,
        target_project_dir=target_dir,
    )
    created: dict[str, object] = {}
    calls: list[dict[str, object]] = []

    def chat_model_factory(
        model: str,
        api_key: str,
        reasoning_effort: str | None,
    ) -> dict[str, str]:
        return {
            "model": model,
            "api_key": api_key,
            "reasoning_effort": reasoning_effort or "",
        }

    def create_agent_factory(model, tools, system_prompt):
        created["model"] = model
        created["tools"] = tools
        created["system_prompt"] = system_prompt
        return FakeAgent(calls)

    def tool() -> str:
        return "ok"

    runtime = LangChainCreateAgentRuntime(
        chat_model_factory=chat_model_factory,
        create_agent_factory=create_agent_factory,
    )

    runtime.invoke(
        LangChainAgentRequest(
            agent_id="test-agent",
            system_prompt="system",
            user_prompt="user",
            tools=[tool],
            delivery_state=state,
            max_steps=3,
        )
    )

    assert created["model"] == {
        "model": "gpt-agent-test",
        "api_key": "sk-test",
        "reasoning_effort": "",
    }
    assert created["tools"] == [tool]
    assert created["system_prompt"] == "system"
    assert calls == [
        {
            "input": {"messages": [{"role": "user", "content": "user"}]},
            "config": {"recursion_limit": 14},
        }
    ]


def test_langchain_create_agent_runtime_requires_openai_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_LLM_PROVIDER", raising=False)
    monkeypatch.chdir(tmp_path)
    state = initial_delivery_state(run_id="run", run_dir=tmp_path / "run")
    runtime = LangChainCreateAgentRuntime(
        chat_model_factory=lambda model, api_key, reasoning_effort: None,
        create_agent_factory=lambda model, tools, system_prompt: FakeAgent([]),
    )

    with pytest.raises(MissingAgentRuntimeConfig):
        runtime.invoke(
            LangChainAgentRequest(
                agent_id="test-agent",
                system_prompt="system",
                user_prompt="user",
                tools=[],
                delivery_state=state,
                max_steps=1,
            )
        )


def test_langchain_create_agent_runtime_uses_google_gemini_provider(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "google_gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-test-key")
    monkeypatch.setenv("AGENT_LLM_MODEL", "gemini-3.1-flash-lite")
    monkeypatch.setenv("AGENT_REASONING_EFFORT", "high")
    state = initial_delivery_state(run_id="run", run_dir=tmp_path)
    captured: dict[str, object] = {}

    def create_agent_factory(model, tools, system_prompt):
        captured["model"] = model
        return FakeAgent([])

    runtime = LangChainCreateAgentRuntime(
        chat_model_factory=lambda model, api_key, reasoning_effort: {
            "model": model,
            "api_key": api_key,
            "reasoning_effort": reasoning_effort,
        },
        create_agent_factory=create_agent_factory,
    )

    runtime.invoke(
        LangChainAgentRequest(
            agent_id="test-agent",
            system_prompt="system",
            user_prompt="user",
            tools=[],
            delivery_state=state,
            max_steps=1,
        )
    )

    assert captured["model"] == {
        "model": "gemini-3.1-flash-lite",
        "api_key": "google-test-key",
        "reasoning_effort": None,
    }


def test_langchain_create_agent_runtime_requires_google_key_for_gemini(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "google_gemini")
    state = initial_delivery_state(run_id="run", run_dir=tmp_path)
    runtime = LangChainCreateAgentRuntime(
        chat_model_factory=lambda model, api_key, reasoning_effort: None,
        create_agent_factory=lambda model, tools, system_prompt: FakeAgent([]),
    )

    with pytest.raises(MissingAgentRuntimeConfig) as exc_info:
        runtime.invoke(
            LangChainAgentRequest(
                agent_id="test-agent",
                system_prompt="system",
                user_prompt="user",
                tools=[],
                delivery_state=state,
                max_steps=1,
            )
        )

    assert "GOOGLE_API_KEY is required" in str(exc_info.value)


def test_langchain_specialist_agent_executor_runs_codex_exec_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    runner = FakeSpecialistRunner()
    state = initial_delivery_state(run_id="run", run_dir=tmp_path)
    captured: dict[str, object] = {}

    def create_agent_factory(model, tools, system_prompt):
        captured["model"] = model
        captured["tools"] = tools
        captured["system_prompt"] = system_prompt
        return ToolCallingAgent(tools)

    executor = LangChainSpecialistAgentExecutor(
        LangChainCreateAgentRuntime(
            chat_model_factory=lambda model, api_key, reasoning_effort: {
                "model": model,
                "reasoning_effort": reasoning_effort,
            },
            create_agent_factory=create_agent_factory,
        )
    )

    result = executor.run(
        SpecialistAgentRequest(
            agent_id="specialist-agent",
            agent_name="Specialist Agent",
            stage="specialist",
            system_prompt="system",
            user_prompt="user",
            runner=runner,
            run_dir=tmp_path,
            delivery_state=state,
        )
    )

    assert result.status == "codex_completed"
    assert "AgentExecutor conclusion:" in result.summary
    assert "Specialist accepted result." in result.summary
    assert captured["model"] == {"model": "gpt-4.1", "reasoning_effort": None}
    assert runner.run_dirs == [tmp_path]
    assert str(captured["system_prompt"]).startswith("system")
    assert "AgentExecutor repair protocol" in str(captured["system_prompt"])
    assert captured["tools"][0].__name__ == "codex_exec"


def test_langchain_specialist_agent_executor_allows_bounded_self_repair(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    state = initial_delivery_state(run_id="run", run_dir=tmp_path)
    runner = SequenceSpecialistRunner(
        [
            AgentRunResult(
                agent_id="specialist-agent",
                status="specialist_failed",
                output_artifacts=["bad-summary.md"],
                summary="contract failed",
                execution_id="exec-1",
                codex_thread_id="thread-1",
                blocking_findings=["Missing required artifact: output.json"],
                fix_request_artifacts=["fix-request.md"],
                recommended_next_action="Retry after writing output.json.",
            ),
            AgentRunResult(
                agent_id="specialist-agent",
                status="specialist_completed",
                output_artifacts=["output.json", "summary.md"],
                summary="repaired",
                execution_id="exec-2",
                codex_thread_id="thread-2",
            ),
        ]
    )
    tool_responses: list[str] = []

    def create_agent_factory(model, tools, system_prompt):
        return RepairingToolCallingAgent(tools, tool_responses)

    executor = LangChainSpecialistAgentExecutor(
        LangChainCreateAgentRuntime(
            chat_model_factory=lambda model, api_key, reasoning_effort: {
                "model": model,
                "reasoning_effort": reasoning_effort,
            },
            create_agent_factory=create_agent_factory,
        )
    )

    result = executor.run(
        SpecialistAgentRequest(
            agent_id="specialist-agent",
            agent_name="Specialist Agent",
            stage="specialist",
            system_prompt="system",
            user_prompt="user",
            runner=runner,
            run_dir=tmp_path,
            delivery_state=state,
        )
    )

    assert result.status == "specialist_completed"
    assert "AgentExecutor conclusion:" in result.summary
    assert "Repaired after reviewing the second Codex result." in result.summary
    assert runner.run_dirs == [tmp_path, tmp_path]
    assert '"repair_guidance"' in tool_responses[0]
    assert '"remaining_codex_tool_calls": 4' in tool_responses[0]
    messages = AgentMessageStore(tmp_path).read(to_agent="specialist-agent")
    assert [message.intent for message in messages] == [
        "agent_executor_feedback",
        "agent_executor_feedback",
    ]
    assert "Missing required artifact: output.json" in messages[1].content
    assert (tmp_path / "agent-executor" / "specialist-agent" / "feedback-02.json").exists()
    assert state["codex_threads_by_agent"]["specialist-agent"] == "thread-2"


def test_langchain_specialist_agent_executor_allows_no_reasoning_effort(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("SPECIALIST_AGENT_REASONING_EFFORT", "none")
    runner = FakeSpecialistRunner()
    captured: dict[str, object] = {}

    def create_agent_factory(model, tools, system_prompt):
        captured["model"] = model
        return ToolCallingAgent(tools)

    executor = LangChainSpecialistAgentExecutor(
        LangChainCreateAgentRuntime(
            chat_model_factory=lambda model, api_key, reasoning_effort: {
                "model": model,
                "reasoning_effort": reasoning_effort,
            },
            create_agent_factory=create_agent_factory,
        )
    )

    executor.run(
        SpecialistAgentRequest(
            agent_id="specialist-agent",
            agent_name="Specialist Agent",
            stage="specialist",
            system_prompt="system",
            user_prompt="user",
            runner=runner,
            run_dir=tmp_path,
            delivery_state=initial_delivery_state(run_id="run", run_dir=tmp_path),
        )
    )

    assert captured["model"] == {"model": "gpt-4.1", "reasoning_effort": None}


def test_langchain_runtime_omits_reasoning_for_gpt_4_1(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AGENT_LLM_MODEL", "gpt-4.1")
    monkeypatch.setenv("AGENT_REASONING_EFFORT", "medium")
    state = initial_delivery_state(run_id="run", run_dir=tmp_path)
    captured: dict[str, object] = {}

    def create_agent_factory(model, tools, system_prompt):
        captured["model"] = model
        return FakeAgent([])

    runtime = LangChainCreateAgentRuntime(
        chat_model_factory=lambda model, api_key, reasoning_effort: {
            "model": model,
            "reasoning_effort": reasoning_effort,
        },
        create_agent_factory=create_agent_factory,
    )

    runtime.invoke(
        LangChainAgentRequest(
            agent_id="test-agent",
            system_prompt="system",
            user_prompt="user",
            tools=[],
            delivery_state=state,
            max_steps=1,
            default_model="gpt-4.1",
        )
    )

    assert captured["model"] == {"model": "gpt-4.1", "reasoning_effort": None}


def test_langchain_runtime_keeps_reasoning_for_reasoning_models(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AGENT_LLM_MODEL", "gpt-5.5")
    monkeypatch.setenv("AGENT_REASONING_EFFORT", "high")
    state = initial_delivery_state(run_id="run", run_dir=tmp_path)
    captured: dict[str, object] = {}

    def create_agent_factory(model, tools, system_prompt):
        captured["model"] = model
        return FakeAgent([])

    runtime = LangChainCreateAgentRuntime(
        chat_model_factory=lambda model, api_key, reasoning_effort: {
            "model": model,
            "reasoning_effort": reasoning_effort,
        },
        create_agent_factory=create_agent_factory,
    )

    runtime.invoke(
        LangChainAgentRequest(
            agent_id="test-agent",
            system_prompt="system",
            user_prompt="user",
            tools=[],
            delivery_state=state,
            max_steps=1,
            default_model="gpt-5.5",
        )
    )

    assert captured["model"] == {"model": "gpt-5.5", "reasoning_effort": "high"}


def test_agent_env_value_reads_generated_project_env(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / "run"
    generated_project = run_dir / "generated-project"
    generated_project.mkdir(parents=True)
    (generated_project / ".env").write_text("OPENAI_API_KEY=sk-generated\n", encoding="utf-8")
    state = initial_delivery_state(run_id="run", run_dir=run_dir)

    assert agent_env_value("OPENAI_API_KEY", state) == "sk-generated"


def test_agent_env_value_prefers_run_env_over_process_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AGENT_LLM_MODEL", "gpt-4.1")
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / "run"
    generated_project = run_dir / "generated-project"
    generated_project.mkdir(parents=True)
    (generated_project / ".env").write_text(
        "AGENT_LLM_PROVIDER=google_gemini\nAGENT_LLM_MODEL=gemini-3.1-flash-lite\n",
        encoding="utf-8",
    )
    state = initial_delivery_state(run_id="run", run_dir=run_dir)

    assert agent_env_value("AGENT_LLM_PROVIDER", state) == "google_gemini"
    assert agent_env_value("AGENT_LLM_MODEL", state) == "gemini-3.1-flash-lite"


def test_agent_env_value_reads_utf8_sig_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-bom\n", encoding="utf-8-sig")
    state = initial_delivery_state(run_id="run", run_dir=tmp_path / "run")

    assert agent_env_value("OPENAI_API_KEY", state) == "sk-bom"


def test_build_agent_executor_graph_runs_standard_node_order():
    visited: list[str] = []

    def prepare(state: dict[str, object]) -> dict[str, object]:
        visited.append("prepare")
        return {**state, "prepared": True}

    def run_agent_executor(state: dict[str, object]) -> dict[str, object]:
        visited.append("executor")
        return {**state, "executed": state["prepared"]}

    def apply_result(state: dict[str, object]) -> dict[str, object]:
        visited.append("apply")
        return {**state, "applied": state["executed"]}

    graph = build_agent_executor_graph(
        dict,
        prepare_node=prepare,
        run_agent_executor_node=run_agent_executor,
        apply_result_node=apply_result,
    )

    result = graph.invoke({})

    assert AGENT_EXECUTOR_GRAPH_NODE_ORDER == (
        "prepare_context",
        "run_agent_executor",
        "apply_result",
    )
    assert visited == ["prepare", "executor", "apply"]
    assert result["applied"] is True
