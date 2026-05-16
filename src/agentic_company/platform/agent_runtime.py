"""Shared agent runtime helpers for LangGraph nodes backed by LangChain agents."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph

from agentic_company.platform.messages import AgentMessage, AgentMessageStore
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import DeliveryState, record_codex_thread

AGENT_EXECUTOR_GRAPH_NODE_ORDER: tuple[str, str, str] = (
    "prepare_context",
    "run_agent_executor",
    "apply_result",
)
DEFAULT_CODEX_TOOL_CALL_LIMIT = 5
DEFAULT_AGENT_LLM_MODEL = "gpt-4.1"
DEFAULT_AGENT_REASONING_EFFORT = None
DEFAULT_COORDINATOR_AGENT_REASONING_EFFORT = None
DEFAULT_SPECIALIST_AGENT_REASONING_EFFORT = None
AGENT_REASONING_EFFORT_ENV = "AGENT_REASONING_EFFORT"
COORDINATOR_AGENT_REASONING_EFFORT_ENV = "COORDINATOR_AGENT_REASONING_EFFORT"
SPECIALIST_AGENT_REASONING_EFFORT_ENV = "SPECIALIST_AGENT_REASONING_EFFORT"
VALID_AGENT_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh"}


class InvokableAgent(Protocol):
    """Minimal invoke contract returned by LangChain `create_agent`."""

    def invoke(self, input: dict[str, Any], config: dict[str, Any] | None = None) -> Any:
        """Run the agent."""


ChatModelFactory = Callable[[str, str, str | None], Any]
CreateAgentFactory = Callable[[Any, Sequence[Callable[..., str]], str], InvokableAgent]
GraphNode = Callable[[Any], Any]


class CodexRunner(Protocol):
    """Runner contract exposed behind the standard `codex_exec` tool."""

    def run(self, run_dir: Path) -> AgentRunResult:
        """Run the Codex-backed specialist implementation."""


class SpecialistAgentExecutor(Protocol):
    """Executor contract for specialist agents that expose tools to create_agent."""

    def run(self, request: SpecialistAgentRequest) -> AgentRunResult:
        """Run the specialist AgentExecutor and return the selected tool result."""


class LangChainAgentRuntimeError(RuntimeError):
    """Base error raised by the shared LangChain agent runtime."""


class MissingAgentRuntimeConfig(LangChainAgentRuntimeError):
    """Raised when required model provider configuration is missing."""


@dataclass(frozen=True, slots=True)
class LangChainAgentRequest:
    """Input packet for a LangChain `create_agent` runtime invocation."""

    agent_id: str
    system_prompt: str
    user_prompt: str
    tools: Sequence[Callable[..., str]]
    delivery_state: DeliveryState
    max_steps: int
    stage: str = ""
    default_model: str = DEFAULT_AGENT_LLM_MODEL
    model_env_keys: tuple[str, ...] = ("AGENT_LLM_MODEL",)
    default_reasoning_effort: str | None = DEFAULT_AGENT_REASONING_EFFORT
    reasoning_effort_env_keys: tuple[str, ...] = (AGENT_REASONING_EFFORT_ENV,)


@dataclass(frozen=True, slots=True)
class SpecialistAgentRequest:
    """Input packet for a specialist AgentExecutor invocation."""

    agent_id: str
    agent_name: str
    stage: str
    system_prompt: str
    user_prompt: str
    runner: CodexRunner
    run_dir: Path
    delivery_state: DeliveryState
    max_steps: int = 4
    max_codex_tool_calls: int = DEFAULT_CODEX_TOOL_CALL_LIMIT
    default_model: str = DEFAULT_AGENT_LLM_MODEL
    model_env_keys: tuple[str, ...] = ("AGENT_LLM_MODEL",)
    default_reasoning_effort: str | None = DEFAULT_SPECIALIST_AGENT_REASONING_EFFORT
    reasoning_effort_env_keys: tuple[str, ...] = (SPECIALIST_AGENT_REASONING_EFFORT_ENV,)


class LangChainCreateAgentRuntime:
    """Reusable runtime for LangGraph nodes that delegate decisions to `create_agent`."""

    def __init__(
        self,
        *,
        chat_model_factory: ChatModelFactory | None = None,
        create_agent_factory: CreateAgentFactory | None = None,
    ) -> None:
        self.chat_model_factory = chat_model_factory or _default_chat_model_factory
        self.create_agent_factory = create_agent_factory or _default_create_agent_factory

    def invoke(self, request: LangChainAgentRequest) -> Any:
        """Build and invoke a LangChain `create_agent` instance."""

        api_key = agent_env_value("OPENAI_API_KEY", request.delivery_state)
        if not api_key:
            raise MissingAgentRuntimeConfig(
                f"OPENAI_API_KEY is required for {request.agent_id} decisions."
            )

        os.environ.setdefault("OPENAI_API_KEY", api_key)
        model_name = _first_env_value(request.model_env_keys, request.delivery_state)
        reasoning_effort = _reasoning_effort_for_request(request)
        model = self.chat_model_factory(
            model_name or request.default_model,
            api_key,
            reasoning_effort,
        )
        agent = self.create_agent_factory(model, list(request.tools), request.system_prompt)
        return agent.invoke(
            {"messages": [{"role": "user", "content": request.user_prompt}]},
            config={"recursion_limit": request.max_steps * 2 + 8},
        )


class LangChainSpecialistAgentExecutor:
    """Standard create_agent executor for Codex-backed specialist agents."""

    def __init__(self, runtime: LangChainCreateAgentRuntime | None = None) -> None:
        self.runtime = runtime or LangChainCreateAgentRuntime()

    def run(self, request: SpecialistAgentRequest) -> AgentRunResult:
        """Expose `codex_exec` to create_agent and return the Codex tool result."""

        tool_result: AgentRunResult | None = None
        tool_call_count = 0

        def codex_exec(reason: str = "", message: str = "") -> str:
            """Run the specialist Codex worker for this assigned task."""

            nonlocal tool_call_count, tool_result
            if tool_call_count >= request.max_codex_tool_calls:
                return json.dumps(
                    {
                        "status": tool_result.status if tool_result else "tool_call_limit_reached",
                        "summary": (
                            tool_result.summary
                            if tool_result
                            else "codex_exec was not run before the tool-call limit."
                        ),
                        "message": (
                            "codex_exec tool-call limit reached. Return the latest result "
                            "upstream or block with the latest findings."
                        ),
                        "max_codex_tool_calls": request.max_codex_tool_calls,
                    },
                    sort_keys=True,
                )
            tool_call_count += 1
            _append_agent_executor_feedback(
                request,
                reason=reason,
                message=message,
                previous_result=tool_result,
                attempt=tool_call_count,
            )
            tool_result = request.runner.run(request.run_dir)
            record_codex_thread(
                request.delivery_state,
                tool_result.agent_id or request.agent_id,
                tool_result.codex_thread_id,
            )
            return json.dumps(
                {
                    "status": tool_result.status,
                    "summary": tool_result.summary,
                    "output_artifacts": tool_result.output_artifacts,
                    "blocking_findings": tool_result.blocking_findings,
                    "fix_request_artifacts": tool_result.fix_request_artifacts,
                    "recommended_next_action": tool_result.recommended_next_action,
                    "reason": reason,
                    "message": message,
                    "codex_tool_call": tool_call_count,
                    "remaining_codex_tool_calls": (request.max_codex_tool_calls - tool_call_count),
                    "repair_guidance": _repair_guidance(tool_result, request),
                },
                sort_keys=True,
            )

        agent_output = self.runtime.invoke(
            LangChainAgentRequest(
                agent_id=request.agent_id,
                system_prompt=_specialist_system_prompt(request),
                user_prompt=request.user_prompt,
                tools=[codex_exec],
                delivery_state=request.delivery_state,
                max_steps=request.max_steps,
                stage=request.stage,
                default_model=request.default_model,
                model_env_keys=request.model_env_keys,
                default_reasoning_effort=request.default_reasoning_effort,
                reasoning_effort_env_keys=request.reasoning_effort_env_keys,
            )
        )
        if tool_result is None:
            raise LangChainAgentRuntimeError(
                f"{request.agent_id} AgentExecutor completed without calling codex_exec."
            )
        agent_summary = _agent_executor_summary(agent_output)
        if not agent_summary:
            return tool_result
        return replace(
            tool_result,
            summary=(
                tool_result.summary.rstrip()
                + "\n\nAgentExecutor conclusion:\n"
                + agent_summary.strip()
            ),
        )


class DirectSpecialistAgentExecutor:
    """Explicit test executor that calls the specialist runner directly."""

    def run(self, request: SpecialistAgentRequest) -> AgentRunResult:
        """Run the configured runner without an LLM decision step."""

        return request.runner.run(request.run_dir)


def _specialist_system_prompt(request: SpecialistAgentRequest) -> str:
    return (
        request.system_prompt.rstrip()
        + "\n\nAgentExecutor repair protocol:\n"
        + f"- You may call `codex_exec` up to {request.max_codex_tool_calls} time(s).\n"
        + "- After each `codex_exec` result, read status, summary, blocking_findings, "
        + "fix_request_artifacts, and recommended_next_action.\n"
        + "- If the result is successful, stop calling tools and return.\n"
        + "- If the result failed or is blocked but the findings look repairable, call "
        + "`codex_exec` again with a concrete repair message quoting the findings and "
        + "artifact refs the Codex worker must inspect.\n"
        + "- Do not retry when the issue is not repairable by this agent, when the tool "
        + "limit is reached, or when a human/environment decision is required.\n"
    )


def _append_agent_executor_feedback(
    request: SpecialistAgentRequest,
    *,
    reason: str,
    message: str,
    previous_result: AgentRunResult | None,
    attempt: int,
) -> None:
    content = _feedback_content(reason=reason, message=message, previous_result=previous_result)
    if not content.strip():
        return
    AgentMessageStore(request.run_dir).append(
        AgentMessage(
            from_agent=request.agent_id,
            to_agent=request.agent_id,
            intent="agent_executor_feedback",
            content=content,
            artifact_refs=previous_result.output_artifacts if previous_result else [],
            correlation_id=request.stage or request.agent_id,
            parent_execution_id=previous_result.execution_id if previous_result else None,
        )
    )
    feedback_path = (
        request.run_dir / "agent-executor" / request.agent_id / f"feedback-{attempt:02d}.json"
    )
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    feedback_path.write_text(
        json.dumps(
            {
                "agent_id": request.agent_id,
                "attempt": attempt,
                "reason": reason,
                "message": message,
                "previous_result": previous_result.to_dict() if previous_result else None,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _feedback_content(
    *,
    reason: str,
    message: str,
    previous_result: AgentRunResult | None,
) -> str:
    parts: list[str] = []
    if previous_result is not None:
        parts.extend(
            [
                "Previous Codex result:",
                f"status: {previous_result.status}",
                f"summary: {previous_result.summary}",
            ]
        )
        if previous_result.blocking_findings:
            parts.append("blocking_findings:")
            parts.extend(f"- {finding}" for finding in previous_result.blocking_findings)
        if previous_result.fix_request_artifacts:
            parts.append("fix_request_artifacts:")
            parts.extend(f"- {artifact}" for artifact in previous_result.fix_request_artifacts)
        if previous_result.recommended_next_action:
            parts.append(f"recommended_next_action: {previous_result.recommended_next_action}")
    if reason:
        parts.append(f"AgentExecutor reason: {reason}")
    if message:
        parts.append(f"AgentExecutor message: {message}")
    return "\n".join(parts)


def _repair_guidance(result: AgentRunResult, request: SpecialistAgentRequest) -> str:
    successful_statuses = {"codex_completed", "ready", "deployed"}
    if result.status.endswith("_completed") or result.status in successful_statuses:
        return (
            "Result appears successful. Do not call codex_exec again unless you see a real blocker."
        )
    if result.blocking_findings or result.fix_request_artifacts or "failed" in result.status:
        return (
            "If these findings are repairable by "
            f"{request.agent_name}, call codex_exec again with a repair message that quotes "
            "the findings and artifact refs. Otherwise return the failure upstream."
        )
    return (
        "Inspect the status and summary before deciding whether another codex_exec call is useful."
    )


def _agent_executor_summary(agent_output: Any) -> str:
    """Extract the final AgentExecutor text from common LangChain return shapes."""

    if isinstance(agent_output, str):
        return agent_output.strip()
    if isinstance(agent_output, dict):
        output = agent_output.get("output")
        if isinstance(output, str) and output.strip():
            return output.strip()
        messages = agent_output.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                content = _message_content(message)
                if content:
                    return content
    content = _message_content(agent_output)
    return content or ""


def _message_content(message: Any) -> str:
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(str(item["text"]))
        return "\n".join(part.strip() for part in parts if part.strip())
    return ""


def build_agent_executor_graph(
    state_schema: type[Any],
    *,
    prepare_node: GraphNode,
    run_agent_executor_node: GraphNode,
    apply_result_node: GraphNode,
    node_order: tuple[str, str, str] = AGENT_EXECUTOR_GRAPH_NODE_ORDER,
):
    """Build the standard LangGraph shell for agentic roles.

    Concrete agents own their domain nodes. The shared shell only standardizes
    the shape: prepare context, let a LangChain/create_agent executor choose
    tools, then apply the result back to delivery state.
    """

    prepare_name, executor_name, apply_name = node_order
    graph = StateGraph(state_schema)
    graph.add_node(prepare_name, prepare_node)
    graph.add_node(executor_name, run_agent_executor_node)
    graph.add_node(apply_name, apply_result_node)
    graph.add_edge(START, prepare_name)
    graph.add_edge(prepare_name, executor_name)
    graph.add_edge(executor_name, apply_name)
    graph.add_edge(apply_name, END)
    return graph.compile()


def coordinator_recovery_policy(
    *,
    coordinator_name: str,
    downstream_tools: Sequence[str],
    repair_limit: int = DEFAULT_CODEX_TOOL_CALL_LIMIT,
) -> list[str]:
    """Return the standard recovery policy for coordinator AgentExecutors."""

    tools = ", ".join(f"`{tool}`" for tool in downstream_tools)
    bounded_limit = max(1, int(repair_limit))
    return [
        (
            "After every downstream tool call, read the returned status, message, "
            "downstream_response.content, downstream_response.artifact_refs, blockers, and "
            "the current delivery_state snapshot before choosing the next tool."
        ),
        (
            "If any downstream tool returns failed, blocked, waiting, refused, precondition, "
            "or contract-error status, do not immediately block. First decide whether the "
            "issue is repairable by the owning downstream agent, by another downstream agent, "
            "or only by a human/environment decision."
        ),
        (
            "For a real failed/blocked/contract-error downstream result, call `codex_review` "
            "before rerunning the owner. Ask it for a concise failure report, likely root "
            "cause, repair advice, and the exact artifact refs the owner should inspect. "
            "A pure ordering/precondition response that did not run downstream work is not "
            "a meaningful worker result; after meaningful work ran, review before accepting, "
            "advancing, rerunning, or blocking."
        ),
        (
            "If the issue is repairable, rerun the owning downstream tool with a concrete "
            "repair message quoting the status, findings, artifact refs, and Codex Review "
            "advice when available."
        ),
        (
            "Keep recovery bounded: do not loop forever, do not retry the same downstream "
            "tool without new evidence, and do not exceed the run's configured repair limit "
            f"or {bounded_limit} repair attempt(s) by default."
        ),
        (
            "Block only when the response shows the problem is not repairable by the current "
            f"{coordinator_name} team, an environment/human decision is required, or the "
            "bounded repair limit has been reached."
        ),
        f"Own only these downstream tools in this coordinator scope: {tools}.",
    ]


def coordinator_quality_review_policy(
    *,
    coordinator_name: str,
    downstream_tools: Sequence[str],
    repair_limit: int = DEFAULT_CODEX_TOOL_CALL_LIMIT,
) -> list[str]:
    """Return the standard quality-review policy for coordinator AgentExecutors."""

    tools = ", ".join(f"`{tool}`" for tool in downstream_tools)
    bounded_limit = max(1, int(repair_limit))
    return [
        (
            "Do not blindly trust downstream `completed` statuses, but keep coordinator "
            "review lightweight. The coordinator sanity-checks the response before moving "
            "to the next phase."
        ),
        (
            "For meaningful downstream work, inspect the returned status, message, summary, "
            "artifact refs, blockers, and current delivery state. Use `codex_review` only "
            "when the response is unclear, artifacts look missing/mismatched, or the result "
            "is failed/blocked/preconditioned."
        ),
        (
            "When using `codex_review`, ask for a concise artifact sanity check and recovery "
            "advice. It should confirm expected artifacts and summaries exist, status is "
            "clear, and the result matches the requested sprint/feature. It should not run "
            "QA, deployment checks, implementation work, or deep report editing."
        ),
        (
            "Codex Review is not a scope-setting authority. It must review only against the "
            "user request, active PM sprint plan, active board item, returned artifact refs, "
            "and explicit downstream contract. It must not introduce new blocking gates such "
            "as repo URL, branch, commit SHA, PR, CI workflow, staging URL, deployment run "
            "link, extra reports, or release-governance metadata unless those gates are "
            "explicitly required by the user request or PM sprint plan."
        ),
        (
            "Treat unsolicited review recommendations as optional notes, not blockers. A "
            "material gap is blocker-worthy only when a PM-required acceptance criterion is "
            "missing, QA failed, requested artifacts are unreadable, the downstream result is "
            "truly failed/blocked, a required human decision is missing, or continuing would "
            "be unsafe."
        ),
        (
            "If review finds material gaps, rerun the owning downstream tool with the review "
            "feedback in `message`. Repeat repair only while useful, bounded by the "
            f"configured repair limit or {bounded_limit} repair attempt(s) by default."
        ),
        (
            "Pure routing/precondition responses that did not produce, validate, or mutate "
            "work are not meaningful downstream results. Do not count them as accepted work."
        ),
        (
            f"{coordinator_name} owns routing acceptance for these downstream tools: {tools}. "
            "Routing acceptance means deciding whether the next planned phase can proceed, "
            "not inventing broader product, release, CI, repository, or deployment governance."
        ),
    ]


def agent_env_value(key: str, delivery_state: DeliveryState) -> str:
    """Read runtime config from process env, run env, generated project env, or repo env."""

    process_value = os.getenv(key)
    if process_value:
        return process_value.strip()

    for env_path in _env_candidate_paths(delivery_state):
        value = _read_env_file(env_path).get(key)
        if value:
            return value.strip()
    return ""


def _first_env_value(keys: tuple[str, ...], delivery_state: DeliveryState) -> str:
    for key in keys:
        value = agent_env_value(key, delivery_state)
        if value:
            return value
    return ""


def _reasoning_effort_for_request(request: LangChainAgentRequest) -> str | None:
    configured = _first_env_value(request.reasoning_effort_env_keys, request.delivery_state)
    effort = configured or request.default_reasoning_effort
    if not effort:
        return None
    if effort not in VALID_AGENT_REASONING_EFFORTS:
        allowed = ", ".join(sorted(VALID_AGENT_REASONING_EFFORTS))
        raise ValueError(f"{AGENT_REASONING_EFFORT_ENV} must be one of: {allowed}")
    if effort == "none":
        return None
    return effort


def _default_chat_model_factory(model: str, api_key: str, reasoning_effort: str | None) -> Any:
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise LangChainAgentRuntimeError(
            f"LangChain OpenAI dependencies are missing: {exc}"
        ) from exc
    kwargs: dict[str, Any] = {"model": model, "api_key": api_key}
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    return ChatOpenAI(**kwargs)


def _default_create_agent_factory(
    model: Any,
    tools: Sequence[Callable[..., str]],
    system_prompt: str,
) -> InvokableAgent:
    try:
        from langchain.agents import create_agent
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise LangChainAgentRuntimeError(f"LangChain dependencies are missing: {exc}") from exc
    return create_agent(model=model, tools=list(tools), system_prompt=system_prompt)


def _env_candidate_paths(delivery_state: DeliveryState) -> list[Path]:
    paths: list[Path] = []
    target_dir = delivery_state.get("target_project_dir")
    if target_dir:
        paths.append(Path(str(target_dir)) / ".env")
    run_dir = delivery_state.get("run_dir")
    if run_dir:
        run_path = Path(str(run_dir))
        paths.append(run_path / "generated-project" / ".env")
        repo_env = _find_repo_env(run_path)
        if repo_env:
            paths.append(repo_env)
    repo_env = _find_repo_env(Path.cwd())
    if repo_env:
        paths.append(repo_env)

    unique_paths: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve() if path.exists() else path.absolute()
        if resolved not in seen:
            seen.add(resolved)
            unique_paths.append(path)
    return unique_paths


def _find_repo_env(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate_dir in [current, *current.parents]:
        if (candidate_dir / "pyproject.toml").exists():
            return candidate_dir / ".env"
    return None


def _read_env_file(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values
