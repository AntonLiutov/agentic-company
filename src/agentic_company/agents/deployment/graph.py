"""Internal LangGraph for the deployment agent."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, NotRequired, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from agentic_company.agents.base import artifact_refs, extend_artifacts
from agentic_company.agents.deployment.planner import (
    write_deployment_plan,
    write_deployment_request,
)
from agentic_company.agents.deployment.runner import (
    DEPLOYMENT_COMMAND_LOG,
    DEPLOYMENT_SUMMARY_MARKDOWN,
    DeploymentStep,
    _load_or_create_deployment_request,
    _load_required_env_values,
    _request_inputs,
    _string_list,
    _summary_payload,
    render_deployment_summary,
)
from agentic_company.integrations.azure.container_apps import (
    account_set_command,
    account_show_command,
    container_app_public_url_command,
    container_app_show_command,
    container_environment_create_command,
    container_environment_show_command,
    first_registry_password,
    registry_create_command,
    registry_credentials_command,
    registry_login_command,
    registry_show_command,
    resource_group_create_command,
    resource_group_show_command,
    safe_account_summary,
)
from agentic_company.integrations.docker.images import (
    docker_build_command,
    docker_info_command,
    docker_push_command,
)
from agentic_company.platform.artifacts import load_execution_request
from agentic_company.platform.events import write_event
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import DeliveryState, mark_node_completed

DEPLOYMENT_AGENT_ID = "deployment-agent"

DEPLOYMENT_AGENT_GRAPH_NODE_ORDER = [
    "prepare_context",
    "write_deployment_plan",
    "write_deployment_request",
    "load_deployment_request",
    "validate_environment",
    "check_azure_account",
    "check_docker",
    "select_subscription",
    "ensure_resource_group",
    "ensure_registry",
    "build_and_push_image",
    "read_registry_credentials",
    "ensure_container_environment",
    "create_or_update_container_app",
    "read_public_url",
    "run_post_deploy_qa",
    "write_summary",
    "apply_result",
]


class AzureDeploymentRunnerLike(Protocol):
    """Methods the deployment graph needs from the Azure runner."""

    def _run_step(
        self,
        name: str,
        command: list[str],
        cwd: Path,
        steps: list[DeploymentStep],
        *,
        allow_failure: bool = False,
        sensitive: bool = False,
    ) -> bool: ...

    def _run_json_step(
        self,
        name: str,
        command: list[str],
        cwd: Path,
        steps: list[DeploymentStep],
        *,
        sensitive: bool = False,
    ) -> dict[str, object] | None: ...

    def _run_text_step(
        self,
        name: str,
        command: list[str],
        cwd: Path,
        steps: list[DeploymentStep],
    ) -> str: ...

    def _ensure_resource(
        self,
        cwd: Path,
        steps: list[DeploymentStep],
        *,
        check_name: str,
        create_name: str,
        check_command: list[str],
        create_command: list[str],
    ) -> bool: ...

    def _resource_exists(
        self,
        name: str,
        command: list[str],
        cwd: Path,
        steps: list[DeploymentStep],
    ) -> bool: ...

    def _create_container_app(
        self,
        cwd: Path,
        steps: list[DeploymentStep],
        *,
        app_name: str,
        resource_group: str,
        environment_name: str,
        registry_server: str,
        registry_username: str,
        registry_password: str,
        image: str,
        env_values: dict[str, str],
    ) -> bool: ...

    def _update_container_app(
        self,
        cwd: Path,
        steps: list[DeploymentStep],
        *,
        app_name: str,
        resource_group: str,
        registry_server: str,
        registry_username: str,
        registry_password: str,
        image: str,
        env_values: dict[str, str],
    ) -> bool: ...

    def _run_post_deploy_qa(
        self,
        run_dir: Path,
        cwd: Path,
        public_url: str,
        steps: list[DeploymentStep],
    ) -> bool: ...


class DeploymentAgentGraphState(TypedDict):
    """Internal state for the deployment agent subgraph."""

    run_dir: str
    delivery_state: NotRequired[DeliveryState]
    run_id: NotRequired[str]
    event_log: NotRequired[str]
    target_dir: NotRequired[str]
    deployment_artifacts: NotRequired[list[str]]
    deployment_request: NotRequired[dict[str, object]]
    inputs: NotRequired[dict[str, object]]
    env_values: NotRequired[dict[str, str]]
    steps: NotRequired[list[DeploymentStep]]
    account: NotRequired[dict[str, object]]
    subscription_id: NotRequired[str]
    resource_group: NotRequired[str]
    location: NotRequired[str]
    registry_name: NotRequired[str]
    app_name: NotRequired[str]
    environment_name: NotRequired[str]
    image: NotRequired[str]
    registry_server: NotRequired[str]
    registry_username: NotRequired[str]
    registry_password: NotRequired[str]
    public_url: NotRequired[str]
    summary: NotRequired[dict[str, object]]
    result: NotRequired[AgentRunResult]


def build_deployment_agent_graph(
    runner: AzureDeploymentRunnerLike,
    *,
    node_order: Sequence[str] | None = None,
):
    """Build the deployment agent internal graph."""

    order = list(DEPLOYMENT_AGENT_GRAPH_NODE_ORDER if node_order is None else node_order)
    if not order:
        raise ValueError("Deployment agent graph requires at least one node.")

    graph = StateGraph(DeploymentAgentGraphState)
    node_map = {
        "prepare_context": _prepare_context(runner),
        "write_deployment_plan": _write_deployment_plan,
        "write_deployment_request": _write_deployment_request,
        "load_deployment_request": _load_deployment_request,
        "validate_environment": _validate_environment,
        "check_azure_account": _check_azure_account(runner),
        "check_docker": _check_docker(runner),
        "select_subscription": _select_subscription(runner),
        "ensure_resource_group": _ensure_resource_group(runner),
        "ensure_registry": _ensure_registry(runner),
        "build_and_push_image": _build_and_push_image(runner),
        "read_registry_credentials": _read_registry_credentials(runner),
        "ensure_container_environment": _ensure_container_environment(runner),
        "create_or_update_container_app": _create_or_update_container_app(runner),
        "read_public_url": _read_public_url(runner),
        "run_post_deploy_qa": _run_post_deploy_qa(runner),
        "write_summary": _write_summary,
        "apply_result": _apply_result,
    }
    for name in order:
        graph.add_node(name, node_map[name])

    graph.add_edge(START, order[0])
    for current, next_node in zip(order, order[1:], strict=False):
        graph.add_edge(current, next_node)
    graph.add_edge(order[-1], END)
    return graph.compile()


def run_deployment_workflow_graph(
    run_dir: Path,
    runner: AzureDeploymentRunnerLike,
) -> AgentRunResult:
    """Run the Azure deployment graph as a standalone runner facade."""

    result = build_deployment_agent_graph(runner).invoke({"run_dir": str(run_dir)})
    return cast(AgentRunResult, result["result"])


def run_deployment_agent_graph(
    delivery_state: DeliveryState,
    runner: AzureDeploymentRunnerLike,
) -> DeliveryState:
    """Run the deployment agent subgraph and return updated delivery state."""

    graph_state: DeploymentAgentGraphState = {
        "delivery_state": delivery_state,
        "run_dir": delivery_state["run_dir"],
    }
    result = build_deployment_agent_graph(runner).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_deployment_agent_graph_mermaid() -> str:
    """Render the deployment agent subgraph as Mermaid text."""

    class NoopRunner:
        def __getattr__(self, name: str) -> Any:
            raise RuntimeError(f"{name} is not available in graph rendering.")

    return (
        build_deployment_agent_graph(cast(AzureDeploymentRunnerLike, NoopRunner()))
        .get_graph()
        .draw_mermaid()
    )


def _prepare_context(runner: AzureDeploymentRunnerLike):
    def run(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
        run_dir = Path(state["run_dir"])
        request = load_execution_request(run_dir)
        target_dir = Path(request.target_project_dir)
        event_log = run_dir / "events.jsonl"
        command_log = run_dir / DEPLOYMENT_COMMAND_LOG
        command_log.parent.mkdir(parents=True, exist_ok=True)
        command_log.write_text("Azure deployment command log\n\n", encoding="utf-8")
        runner._command_log_path = command_log
        write_event(
            event_log,
            request.run_id,
            DEPLOYMENT_AGENT_ID,
            "deployment_started",
            {"target_project_dir": request.target_project_dir},
        )
        return {
            **state,
            "run_id": request.run_id,
            "event_log": str(event_log),
            "target_dir": str(target_dir),
            "steps": [],
            "deployment_artifacts": [],
        }

    return run


def _write_deployment_plan(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
    artifacts = write_deployment_plan(Path(state["run_dir"]), Path(state["target_dir"]))
    return {
        **state,
        "deployment_artifacts": [*state.get("deployment_artifacts", []), *artifacts],
    }


def _write_deployment_request(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
    artifacts = write_deployment_request(Path(state["run_dir"]), Path(state["target_dir"]))
    return {
        **state,
        "deployment_artifacts": [*state.get("deployment_artifacts", []), *artifacts],
    }


def _load_deployment_request(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
    request = _load_or_create_deployment_request(Path(state["run_dir"]), Path(state["target_dir"]))
    if request.get("status") != "ready":
        step = DeploymentStep(
            name="Deployment request",
            status="blocked",
            details="Deployment request is blocked; resolve readiness blockers first.",
        )
        return _with_summary(state, "blocked", [step], deployment_request=request)
    return {**state, "deployment_request": request}


def _validate_environment(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
    if _is_terminal(state):
        return state
    request = state["deployment_request"]
    inputs = _request_inputs(request)
    env_values = _load_required_env_values(
        Path(state["target_dir"]) / ".env",
        _string_list(inputs.get("environment_variables")),
    )
    missing_env = [key for key, value in env_values.items() if not value.strip()]
    if missing_env:
        return _with_summary(
            state,
            "blocked",
            [
                DeploymentStep(
                    name="Application environment",
                    status="blocked",
                    details="Missing required .env values: " + ", ".join(missing_env),
                )
            ],
            inputs=inputs,
            env_values=env_values,
        )

    image = str(inputs["image"])
    return {
        **state,
        "inputs": inputs,
        "env_values": env_values,
        "resource_group": str(inputs["resource_group"]),
        "location": str(inputs["location"]),
        "registry_name": str(inputs["container_registry"]),
        "app_name": str(inputs["container_app_name"]),
        "environment_name": str(inputs["container_app_environment"]),
        "image": image,
        "registry_server": image.split("/", 1)[0],
    }


def _check_azure_account(runner: AzureDeploymentRunnerLike):
    def run(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
        if _is_terminal(state):
            return state
        steps = _steps(state)
        account = runner._run_json_step(
            "Azure account",
            account_show_command(),
            Path(state["target_dir"]),
            steps,
        )
        if account is None:
            return _with_summary(state, "blocked", steps)
        return {
            **state,
            "steps": steps,
            "account": account,
            "subscription_id": str(account.get("id", "")).strip(),
        }

    return run


def _check_docker(runner: AzureDeploymentRunnerLike):
    def run(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
        if _is_terminal(state):
            return state
        steps = _steps(state)
        if not runner._run_step(
            "Docker daemon", docker_info_command(), Path(state["target_dir"]), steps
        ):
            return _with_summary(state, "blocked", steps)
        return {**state, "steps": steps}

    return run


def _select_subscription(runner: AzureDeploymentRunnerLike):
    def run(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
        if _is_terminal(state):
            return state
        requested_subscription = str(state["inputs"].get("subscription_id", "")).strip()
        if not requested_subscription or requested_subscription.startswith("<"):
            return state

        steps = _steps(state)
        if not runner._run_step(
            "Select Azure subscription",
            account_set_command(requested_subscription),
            Path(state["target_dir"]),
            steps,
        ):
            return _with_summary(state, "blocked", steps)
        return {**state, "steps": steps, "subscription_id": requested_subscription}

    return run


def _ensure_resource_group(runner: AzureDeploymentRunnerLike):
    def run(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
        if _is_terminal(state):
            return state
        steps = _steps(state)
        if not runner._ensure_resource(
            Path(state["target_dir"]),
            steps,
            check_name="Check resource group",
            create_name="Create resource group",
            check_command=resource_group_show_command(state["resource_group"]),
            create_command=resource_group_create_command(
                state["resource_group"], state["location"]
            ),
        ):
            return _with_summary(state, "failed", steps)
        return {**state, "steps": steps}

    return run


def _ensure_registry(runner: AzureDeploymentRunnerLike):
    def run(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
        if _is_terminal(state):
            return state
        steps = _steps(state)
        if not runner._ensure_resource(
            Path(state["target_dir"]),
            steps,
            check_name="Check container registry",
            create_name="Create container registry",
            check_command=registry_show_command(state["registry_name"]),
            create_command=registry_create_command(
                resource_group=state["resource_group"],
                registry_name=state["registry_name"],
            ),
        ):
            return _with_summary(state, "failed", steps)
        return {**state, "steps": steps}

    return run


def _build_and_push_image(runner: AzureDeploymentRunnerLike):
    def run(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
        if _is_terminal(state):
            return state
        steps = _steps(state)
        commands = [
            (
                "Log in to container registry",
                registry_login_command(state["registry_name"]),
            ),
            (
                "Build container image",
                docker_build_command(image=state["image"]),
            ),
            ("Push container image", docker_push_command(state["image"])),
        ]
        for name, command in commands:
            if not runner._run_step(name, command, Path(state["target_dir"]), steps):
                return _with_summary(state, "failed", steps)
        return {**state, "steps": steps}

    return run


def _read_registry_credentials(runner: AzureDeploymentRunnerLike):
    def run(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
        if _is_terminal(state):
            return state
        steps = _steps(state)
        credentials = runner._run_json_step(
            "Read container registry credentials",
            registry_credentials_command(state["registry_name"]),
            Path(state["target_dir"]),
            steps,
            sensitive=True,
        )
        if credentials is None:
            return _with_summary(state, "failed", steps)
        registry_username = str(credentials.get("username", ""))
        registry_password = first_registry_password(credentials)
        if not registry_username or not registry_password:
            steps.append(
                DeploymentStep(
                    name="Read container registry credentials",
                    status="failed",
                    details="Azure did not return usable ACR credentials.",
                )
            )
            return _with_summary(state, "failed", steps)
        return {
            **state,
            "steps": steps,
            "registry_username": registry_username,
            "registry_password": registry_password,
        }

    return run


def _ensure_container_environment(runner: AzureDeploymentRunnerLike):
    def run(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
        if _is_terminal(state):
            return state
        steps = _steps(state)
        if not runner._ensure_resource(
            Path(state["target_dir"]),
            steps,
            check_name="Check Container Apps environment",
            create_name="Create Container Apps environment",
            check_command=container_environment_show_command(
                environment_name=state["environment_name"],
                resource_group=state["resource_group"],
            ),
            create_command=container_environment_create_command(
                environment_name=state["environment_name"],
                resource_group=state["resource_group"],
                location=state["location"],
            ),
        ):
            return _with_summary(state, "failed", steps)
        return {**state, "steps": steps}

    return run


def _create_or_update_container_app(runner: AzureDeploymentRunnerLike):
    def run(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
        if _is_terminal(state):
            return state
        steps = _steps(state)
        target_dir = Path(state["target_dir"])
        app_exists = runner._resource_exists(
            "Check Container App",
            container_app_show_command(
                app_name=state["app_name"],
                resource_group=state["resource_group"],
            ),
            target_dir,
            steps,
        )
        if app_exists:
            ok = runner._update_container_app(
                target_dir,
                steps,
                app_name=state["app_name"],
                resource_group=state["resource_group"],
                registry_server=state["registry_server"],
                registry_username=state["registry_username"],
                registry_password=state["registry_password"],
                image=state["image"],
                env_values=state["env_values"],
            )
        else:
            ok = runner._create_container_app(
                target_dir,
                steps,
                app_name=state["app_name"],
                resource_group=state["resource_group"],
                environment_name=state["environment_name"],
                registry_server=state["registry_server"],
                registry_username=state["registry_username"],
                registry_password=state["registry_password"],
                image=state["image"],
                env_values=state["env_values"],
            )
        if not ok:
            return _with_summary(state, "failed", steps)
        return {**state, "steps": steps}

    return run


def _read_public_url(runner: AzureDeploymentRunnerLike):
    def run(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
        if _is_terminal(state):
            return state
        steps = _steps(state)
        fqdn = runner._run_text_step(
            "Read public URL",
            container_app_public_url_command(
                app_name=state["app_name"],
                resource_group=state["resource_group"],
            ),
            Path(state["target_dir"]),
            steps,
        )
        if not fqdn:
            return _with_summary(state, "failed", steps)
        return {**state, "steps": steps, "public_url": f"https://{fqdn.strip()}"}

    return run


def _run_post_deploy_qa(runner: AzureDeploymentRunnerLike):
    def run(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
        if _is_terminal(state):
            return state
        steps = _steps(state)
        if not runner._run_post_deploy_qa(
            Path(state["run_dir"]),
            Path(state["target_dir"]),
            state["public_url"],
            steps,
        ):
            return _with_summary(state, "failed", steps)
        summary = _summary_payload(
            "deployed", Path(state["target_dir"]), state["deployment_request"], steps
        )
        summary["azure_account"] = safe_account_summary(state["account"], state["subscription_id"])
        summary["public_url"] = state["public_url"]
        summary["resource_group"] = state["resource_group"]
        summary["container_app_name"] = state["app_name"]
        summary["container_app_environment"] = state["environment_name"]
        summary["image"] = state["image"]
        summary["teardown_command"] = f"az group delete --name {state['resource_group']} --yes"
        return {**state, "steps": steps, "summary": summary}

    return run


def _write_summary(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
    summary = state.get("summary")
    if summary is None:
        return _with_summary(state, "failed", _steps(state))

    run_dir = Path(state["run_dir"])
    target_dir = Path(state["target_dir"])
    summary_path = run_dir / DEPLOYMENT_SUMMARY_MARKDOWN
    summary_path.write_text(render_deployment_summary(summary), encoding="utf-8")
    status = str(summary["status"])
    output_artifacts = [
        *state.get("deployment_artifacts", []),
        DEPLOYMENT_SUMMARY_MARKDOWN,
        DEPLOYMENT_COMMAND_LOG,
    ]
    if status == "deployed" and "delivery_state" not in state:
        from agentic_company.agents.handoff.summary import write_handoff_summary

        output_artifacts.append(write_handoff_summary(run_dir, target_dir, state["run_id"]))

    write_event(
        Path(state["event_log"]),
        state["run_id"],
        DEPLOYMENT_AGENT_ID,
        "artifact_written",
        {"artifact": DEPLOYMENT_SUMMARY_MARKDOWN, "status": status},
    )
    write_event(
        Path(state["event_log"]),
        state["run_id"],
        DEPLOYMENT_AGENT_ID,
        "deployment_completed",
        {"artifact": DEPLOYMENT_SUMMARY_MARKDOWN, "status": status},
    )
    result = AgentRunResult(
        agent_id=DEPLOYMENT_AGENT_ID,
        status=f"deployment_{status}",
        output_artifacts=output_artifacts,
        summary=summary_path.read_text(encoding="utf-8"),
    )
    return {**state, "result": result}


def _apply_result(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
    result = state.get("result")
    if result is None:
        raise ValueError("Deployment agent graph result is missing.")

    delivery_state = state.get("delivery_state")
    if delivery_state is None:
        return state

    deployment_status = result.status.removeprefix("deployment_")
    updated = mark_node_completed(
        delivery_state,
        node_name="deployment",
        stage="deployment",
        status=result.status,
    )
    updated["deployment_status"] = deployment_status
    updated["public_url"] = state.get("public_url")
    extend_artifacts(
        updated,
        artifact_refs(result.output_artifacts, kind="deployment", owner_agent=result.agent_id),
    )
    return {**state, "delivery_state": updated}


def _is_terminal(state: DeploymentAgentGraphState) -> bool:
    return "summary" in state


def _steps(state: DeploymentAgentGraphState) -> list[DeploymentStep]:
    return list(state.get("steps", []))


def _with_summary(
    state: DeploymentAgentGraphState,
    status: str,
    steps: list[DeploymentStep],
    **updates: object,
) -> DeploymentAgentGraphState:
    request = cast(
        dict[str, object], updates.get("deployment_request") or state.get("deployment_request", {})
    )
    summary = _summary_payload(status, Path(state["target_dir"]), request, steps)
    return {**state, **updates, "steps": steps, "summary": summary}
