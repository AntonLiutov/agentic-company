"""Streamlit operator console for running local delivery workflows."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from agentic_company.console.services.graph_artifacts import refresh_graph_artifacts
from agentic_company.console.support import (
    ARTIFACT_GROUPS,
    ARTIFACTS,
    DIAGNOSTIC_ARTIFACT_GROUPS,
    ArtifactSpec,
    azure_deployment_running,
    clear_console_runs,
    codex_execution_running,
    create_console_run,
    deployment_completed,
    ensure_required_env_defaults,
    execution_completed,
    initial_env_value,
    load_sample_requirements,
    missing_required_env_keys,
    read_events,
    read_json_artifact,
    read_required_configuration,
    repo_root,
    root_env_value,
    saved_env_keys,
    start_azure_deployment,
    start_codex_execution,
    write_target_env,
)
from agentic_company.console.views.live_logs import render_live_logs
from agentic_company.platform.logging import configure_logging

LOGGER = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    st.set_page_config(
        page_title="Agentic Planning Console",
        layout="wide",
    )

    root = repo_root()
    _refresh_graph_artifacts(root)
    _ensure_state(root)

    st.title("Agentic Planning Console")
    st.caption("Run the deterministic planning pipeline and inspect the agent artifacts.")

    input_col, output_col = st.columns([0.9, 1.4], gap="large")
    with input_col:
        _render_input_panel(root)
        _render_cleanup_panel(root)
    with output_col:
        _render_output_panel()


def _ensure_state(root: Path) -> None:
    st.session_state.setdefault("requirements_text", load_sample_requirements(root))
    st.session_state.setdefault("last_run_dir", None)
    st.session_state.setdefault("last_error", None)
    st.session_state.setdefault("output_view", "Artifacts")


def _render_input_panel(root: Path) -> None:
    st.subheader("Requirements")

    button_col, path_col = st.columns([0.55, 1])
    if button_col.button("Load sample", use_container_width=True):
        LOGGER.info("Loading sample requirements")
        st.session_state["requirements_text"] = load_sample_requirements(root)
        st.session_state["last_error"] = None
        st.rerun()
    path_col.caption("Sample: `examples/requirements/web-app-mvp-chat.md`")

    requirements_text = st.text_area(
        "Project requirements",
        value=st.session_state["requirements_text"],
        height=420,
    )
    st.session_state["requirements_text"] = requirements_text

    if st.button("Run planning pipeline", type="primary", use_container_width=True):
        if not requirements_text.strip():
            st.session_state["last_error"] = "Requirements cannot be empty."
            LOGGER.warning("Planning requested with empty requirements")
            st.rerun()
        try:
            LOGGER.info("Creating console run")
            run_dir = create_console_run(requirements_text, root / "runs")
        except Exception as exc:  # pragma: no cover - shown in Streamlit
            st.session_state["last_error"] = str(exc)
            LOGGER.exception("Planning pipeline failed")
        else:
            st.session_state["last_run_dir"] = str(run_dir)
            st.session_state["last_error"] = None
            LOGGER.info("Planning pipeline completed run_dir=%s", run_dir)
        st.rerun()

    st.info(
        "Planning starts deterministically; execution and review are moving onto the LangGraph "
        "delivery spine."
    )


def _refresh_graph_artifacts(root: Path) -> None:
    refresh_key = "_graph_artifacts_refreshed_root"
    if st.session_state.get(refresh_key) == str(root):
        return

    try:
        refresh_graph_artifacts(root)
    except Exception:  # pragma: no cover - should not block the console
        LOGGER.exception("Failed to refresh LangGraph Mermaid artifacts")
        return

    st.session_state[refresh_key] = str(root)


def _render_cleanup_panel(root: Path) -> None:
    st.subheader("Run history")
    st.caption(
        "Clear only local `runs/console-*` folders. Named smoke or demo runs are left alone."
    )
    confirm = st.checkbox("Confirm clearing console runs")
    if st.button("Clear console runs", use_container_width=True, disabled=not confirm):
        LOGGER.info("Clearing console runs")
        result = clear_console_runs(root / "runs")
        run_dir_value = st.session_state["last_run_dir"]
        if run_dir_value and not Path(str(run_dir_value)).exists():
            st.session_state["last_run_dir"] = None
        st.session_state["last_error"] = None
        st.success(f"Deleted {result.deleted} console run folder(s).")
        if result.skipped:
            LOGGER.warning("Skipped locked console runs count=%s", len(result.skipped))
            st.warning(
                "Some console run folders could not be deleted because another process is "
                "still using them. Stop the generated app, Docker container, terminal, or file "
                "viewer that has the folder open, then try again."
            )
            with st.expander("Skipped folders"):
                for item in result.skipped:
                    st.code(item, language="text")


def _render_output_panel() -> None:
    if st.session_state["last_error"]:
        st.error(st.session_state["last_error"])

    run_dir_value = st.session_state["last_run_dir"]
    if not run_dir_value:
        st.subheader("Run output")
        st.write("Run the planning pipeline to see artifacts and timeline here.")
        return

    run_dir = Path(str(run_dir_value))
    st.subheader("Run output")
    st.code(str(run_dir), language="text")
    generated_app_exists = (run_dir / "generated-project" / "app.py").exists()
    execution_is_completed = execution_completed(run_dir)
    execution_is_running = codex_execution_running(run_dir)
    deployment_is_running = azure_deployment_running(run_dir)
    deployment_is_completed = deployment_completed(run_dir)
    if generated_app_exists:
        st.success("Generated project is available.")
    if _workflow_should_refresh(run_dir, execution_is_running or deployment_is_running):
        _auto_refresh()
    if execution_is_running:
        st.info("Execution workflow is running. Logs and stage status refresh automatically.")
    if deployment_is_running:
        st.info("Azure deployment is running. Stage status refreshes automatically.")
    missing_credentials = _render_credentials_panel(run_dir)
    credentials_ready = not missing_credentials
    if not credentials_ready:
        st.warning(
            "Save required credentials before generating or executing the app: "
            + ", ".join(f"`{key}`" for key in missing_credentials)
        )
    _render_stage_notice(
        run_dir,
        missing_credentials=missing_credentials,
        execution_is_running=execution_is_running,
        execution_is_completed=execution_is_completed,
        deployment_is_running=deployment_is_running,
        deployment_is_completed=deployment_is_completed,
    )
    confirm_codex = st.checkbox("Confirm real Codex execution")
    if st.button(
        "Run Codex execution",
        use_container_width=True,
        disabled=(
            execution_is_completed
            or execution_is_running
            or not confirm_codex
            or not credentials_ready
        ),
    ):
        try:
            ensure_required_env_defaults(run_dir)
            LOGGER.info("Starting Codex execution run_dir=%s", run_dir)
            start_codex_execution(run_dir)
        except Exception as exc:  # pragma: no cover - shown in Streamlit
            st.session_state["last_error"] = str(exc)
            LOGGER.exception("Codex execution failed to start run_dir=%s", run_dir)
        else:
            st.session_state["last_error"] = None
            st.session_state["output_view"] = "Live Logs"
            LOGGER.info("Codex execution started run_dir=%s", run_dir)
        st.rerun()

    _render_deployment_action(run_dir, deployment_is_running, deployment_is_completed)

    view_options = ["Artifacts", "Live Logs", "Timeline", "Summary"]
    selected_view = st.radio(
        "Run view",
        view_options,
        horizontal=True,
        label_visibility="collapsed",
        key="output_view",
    )

    if selected_view == "Artifacts":
        _render_artifacts(run_dir)
    elif selected_view == "Live Logs":
        render_live_logs(run_dir)
    elif selected_view == "Timeline":
        _render_timeline(run_dir)
    else:
        _render_summary(run_dir)


def _render_artifacts(run_dir: Path) -> None:
    st.caption(
        "Primary artifacts show the working flow. "
        "Raw evidence and prepared request files are grouped under diagnostics."
    )

    for group_name, description, artifacts in ARTIFACT_GROUPS:
        existing = [artifact for artifact in artifacts if (run_dir / artifact[0]).exists()]
        if not existing:
            continue

        with st.container(border=True):
            st.subheader(f"{group_name} ({len(existing)})")
            st.caption(description)
            _render_artifact_entries(run_dir, existing)

    diagnostic_groups = [
        (
            group_name,
            description,
            [artifact for artifact in artifacts if (run_dir / artifact[0]).exists()],
        )
        for group_name, description, artifacts in DIAGNOSTIC_ARTIFACT_GROUPS
    ]
    diagnostics = [artifact for _, _, artifacts in diagnostic_groups for artifact in artifacts]
    if diagnostics:
        show_diagnostics = st.toggle(
            f"Show diagnostics ({len(diagnostics)})",
            value=False,
            help="Raw JSON, command logs, Docker logs, Codex prompt, and deployment requests.",
        )
        if show_diagnostics:
            with st.container(border=True):
                st.subheader("Diagnostics")
                st.caption(
                    "Detailed evidence for debugging QA, Codex, Docker, and deployment behavior."
                )
                for group_name, description, artifacts in diagnostic_groups:
                    if not artifacts:
                        continue
                    st.markdown(f"**{group_name} ({len(artifacts)})**")
                    st.caption(description)
                    _render_artifact_entries(run_dir, artifacts)


def _render_artifact_entries(
    run_dir: Path,
    artifacts: list[ArtifactSpec],
) -> None:
    for filename, label, agent in artifacts:
        path = run_dir / filename
        with st.expander(f"{label} - {agent}"):
            if not path.exists():
                st.warning(f"Missing artifact: {filename}")
                continue
            runtime = _artifact_runtime(filename)
            st.caption(f"Runtime: {runtime} | Artifact: `{filename}`")
            if path.suffix == ".json":
                st.json(read_json_artifact(path))
            elif path.suffix in {".jsonl", ".log", ".patch"}:
                st.code(path.read_text(encoding="utf-8"), language=_artifact_language(path))
            else:
                st.markdown(path.read_text(encoding="utf-8"))


def _render_stage_notice(
    run_dir: Path,
    *,
    missing_credentials: list[str],
    execution_is_running: bool,
    execution_is_completed: bool,
    deployment_is_running: bool,
    deployment_is_completed: bool,
) -> None:
    summary_path = run_dir / "07-execution-summary.md"
    qa_results_path = run_dir / "qa" / "results.json"
    handoff_path = run_dir / "09-handoff-summary.md"
    deployment_request_path = run_dir / "12-deployment-request.json"
    deployment_summary_path = run_dir / "13-deployment-summary.md"
    qa_status = _qa_status(qa_results_path)
    events = read_events(run_dir)
    qa_running = _event_open(events, "qa_started", "qa_completed")
    handoff_running = _event_open(events, "handoff_started", "handoff_ready")

    if missing_credentials:
        current = "Credentials required"
        next_step = "Save required values before execution"
    elif execution_is_running:
        if qa_running:
            current = "QA running"
            next_step = "Deployment readiness starts after QA completes"
        elif handoff_running:
            current = "Handoff running"
            next_step = "Review final artifacts"
        else:
            current = "Execution running"
            next_step = "QA starts after Codex completes"
    elif not summary_path.exists():
        current = "Ready for execution"
        next_step = "Run Codex execution"
    elif not qa_results_path.exists():
        current = "Execution complete"
        next_step = "QA starts automatically in the execution graph"
    elif qa_status == "failed":
        current = "QA failed"
        next_step = "Inspect QA evidence, fix, then rerun"
    elif deployment_is_running:
        current = "Deployment running"
        next_step = "Review deployment summary, then handoff"
    elif qa_status == "passed" and not deployment_summary_path.exists():
        current = "Ready for deployment"
        next_step = "Deploy generated project to Azure"
    elif deployment_summary_path.exists() and not deployment_is_completed:
        current = "Deployment needs attention"
        next_step = "Inspect deployment summary and retry"
    elif deployment_is_completed and not handoff_path.exists():
        current = "Deployment QA passed"
        next_step = "Wait for final handoff summary"
    elif deployment_is_completed:
        current = "Deployment complete"
        next_step = "Review handoff summary"
    elif execution_is_completed and deployment_request_path.exists():
        current = "Ready for deployment"
        next_step = "Deploy generated project to Azure"
    elif execution_is_completed:
        current = "Handoff ready"
        next_step = "Review artifacts"
    else:
        current = "Planning complete"
        next_step = "Continue execution"

    st.info(f"Current step: **{current}**. Next: **{next_step}**.")
    stages = [
        ("Planning", "done"),
        ("Credentials", "blocked" if missing_credentials else "done"),
        ("Execution", _execution_stage(summary_path, execution_is_running)),
        ("QA", _qa_stage(qa_results_path, qa_status, qa_running)),
        ("Deployment", _deployment_stage(deployment_summary_path, deployment_is_running)),
        (
            "Handoff",
            _handoff_stage(
                handoff_path,
                handoff_running,
                deployment_expected=deployment_request_path.exists(),
                deployment_completed=deployment_is_completed,
            ),
        ),
    ]
    cols = st.columns(len(stages))
    for column, (label, status) in zip(cols, stages, strict=False):
        column.metric(label, status)


def _execution_stage(summary_path: Path, execution_is_running: bool) -> str:
    if execution_is_running:
        return "running"
    return "done" if summary_path.exists() else "pending"


def _qa_stage(qa_results_path: Path, qa_status: str, qa_running: bool) -> str:
    if qa_running:
        return "running"
    if not qa_results_path.exists():
        return "pending"
    return qa_status


def _handoff_stage(
    handoff_path: Path,
    handoff_running: bool,
    *,
    deployment_expected: bool,
    deployment_completed: bool,
) -> str:
    if handoff_running:
        return "running"
    if deployment_expected and not deployment_completed:
        return "pending"
    return "done" if handoff_path.exists() else "pending"


def _deployment_stage(deployment_summary_path: Path, deployment_is_running: bool) -> str:
    if deployment_is_running:
        return "running"
    if not deployment_summary_path.exists():
        return "pending"
    status = _markdown_status(deployment_summary_path)
    return "done" if status == "deployed" else status or "needs attention"


def _markdown_status(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Status:"):
            return line.split(":", 1)[1].strip()
    return ""


def _event_open(events: list[dict[str, object]], start_event: str, end_event: str) -> bool:
    latest_start = -1
    latest_end = -1
    for index, event in enumerate(events):
        name = event.get("event")
        if name == start_event:
            latest_start = index
        elif name == end_event:
            latest_end = index
    return latest_start > latest_end


def _workflow_should_refresh(run_dir: Path, execution_is_running: bool) -> bool:
    if execution_is_running:
        return True

    events = read_events(run_dir)
    execution_started = any(event.get("event") == "execution_started" for event in events)
    terminal_events = {
        "execution_failed",
        "qa_completed",
        "handoff_ready",
    }
    terminal_seen = any(event.get("event") in terminal_events for event in events)
    return execution_started and not terminal_seen


def _qa_status(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        payload = read_json_artifact(path)
    except json.JSONDecodeError:
        return "unknown"
    return str(payload.get("status", "unknown"))


def _auto_refresh() -> None:
    st_autorefresh(
        interval=2000,
        limit=None,
        key="codex-log-refresh",
    )


def _render_deployment_action(
    run_dir: Path,
    deployment_is_running: bool,
    deployment_is_completed: bool,
) -> None:
    request_path = run_dir / "12-deployment-request.json"
    qa_results_path = run_dir / "qa" / "results.json"
    if _qa_status(qa_results_path) != "passed":
        return

    with st.expander("Azure deployment", expanded=deployment_is_running):
        st.warning(
            "This creates or updates Azure resources for the generated project. "
            "Use the selected Azure CLI account only if the subscription is correct."
        )
        if request_path.exists():
            request = read_json_artifact(request_path)
            inputs = request.get("inputs", {})
            if isinstance(inputs, dict):
                st.caption(
                    "Target: "
                    f"`{inputs.get('resource_group', '')}` / "
                    f"`{inputs.get('container_app_name', '')}`"
                )
        else:
            st.caption("Deployment plan and request will be prepared by the deployment graph.")

        confirm = st.checkbox("Confirm Azure deployment for this generated project")
        disabled = deployment_is_running or deployment_is_completed or not confirm
        if st.button(
            "Deploy generated project to Azure", use_container_width=True, disabled=disabled
        ):
            try:
                LOGGER.info("Starting Azure deployment run_dir=%s", run_dir)
                start_azure_deployment(run_dir)
            except Exception as exc:  # pragma: no cover - shown in Streamlit
                st.session_state["last_error"] = str(exc)
                LOGGER.exception("Azure deployment failed to start run_dir=%s", run_dir)
            else:
                st.session_state["last_error"] = None
                st.session_state["output_view"] = "Live Logs"
                LOGGER.info("Azure deployment started run_dir=%s", run_dir)
            st.rerun()


def _artifact_language(path: Path) -> str:
    if path.suffix == ".jsonl":
        return "json"
    if path.suffix == ".patch":
        return "diff"
    return "text"


def _artifact_runtime(filename: str) -> str:
    if filename.startswith(("07-", "codex/")):
        return "L6 Codex Agent"
    if filename.startswith(("13-deployment-summary", "deployment/")):
        return "L2 Tool Executor"
    if filename.startswith(("08-", "qa/")):
        return "L2 Tool Executor"
    return "L0 Deterministic"


def _render_credentials_panel(run_dir: Path) -> list[str]:
    required_config = read_required_configuration(run_dir)
    if not required_config:
        return []

    missing_keys = missing_required_env_keys(run_dir)
    with st.expander("Required credentials", expanded=bool(missing_keys)):
        saved_keys = saved_env_keys(run_dir)
        if saved_keys:
            st.caption("Saved locally for this run: " + ", ".join(f"`{key}`" for key in saved_keys))
        root_prefilled_keys = [
            key for key in required_config if key not in saved_keys and root_env_value(key)
        ]
        if root_prefilled_keys:
            st.caption(
                "Pre-filled from repo `.env`: "
                + ", ".join(f"`{key}`" for key in root_prefilled_keys)
            )
        st.caption("Values are written to `generated-project/.env` and are not shown again.")
        st.caption(
            "Execution is disabled until every required value is saved or has a safe default."
        )

        values: dict[str, str] = {}
        for key in required_config:
            default_value = "" if key in saved_keys else initial_env_value(key)
            values[key] = st.text_input(
                key,
                type="password" if _looks_secret(key) else "default",
                value=default_value,
                placeholder=(
                    "Leave blank to keep existing value"
                    if key in saved_keys
                    else "Required before execution"
                ),
                key=f"credential-{run_dir.name}-{key}",
            )

        if st.button("Save .env for this run", use_container_width=True):
            missing_after_save = missing_required_env_keys(run_dir, values)
            if missing_after_save:
                LOGGER.warning(
                    "Credential save blocked missing_keys=%s run_dir=%s",
                    missing_after_save,
                    run_dir,
                )
                st.error(
                    "Cannot continue until these required values are provided: "
                    + ", ".join(f"`{key}`" for key in missing_after_save)
                )
            else:
                env_path = write_target_env(run_dir, values)
                ensure_required_env_defaults(run_dir)
                LOGGER.info(
                    "Saved required environment keys count=%s run_dir=%s",
                    len(saved_env_keys(run_dir)),
                    run_dir,
                )
                st.success(f"Saved {env_path.name} with {len(saved_env_keys(run_dir))} key(s).")
                for key in required_config:
                    st.session_state.pop(f"credential-{run_dir.name}-{key}", None)
                st.rerun()
    return missing_keys


def _looks_secret(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in ["key", "token", "secret", "password"])


def _render_timeline(run_dir: Path) -> None:
    events = read_events(run_dir)
    if not events:
        st.warning("No events found.")
        return

    for index, event in enumerate(events, start=1):
        data = event.get("data", {})
        artifact = data.get("artifact") if isinstance(data, dict) else None
        cols = st.columns([0.12, 0.2, 0.25, 0.22, 0.21])
        cols[0].write(index)
        cols[1].write(event.get("timestamp", ""))
        cols[2].write(event.get("agent_id", ""))
        cols[3].write(event.get("event", ""))
        cols[4].write(artifact or event.get("runtime", "L0 Deterministic"))


def _render_summary(run_dir: Path) -> None:
    events = read_events(run_dir)
    artifact_count = sum(1 for filename, _, _ in ARTIFACTS if (run_dir / filename).exists())
    completed = any(event.get("event") == "run_completed" for event in events)

    metric_cols = st.columns(3)
    metric_cols[0].metric("Artifacts", artifact_count)
    metric_cols[1].metric("Events", len(events))
    metric_cols[2].metric("Status", "Complete" if completed else "In progress")

    st.write("Visible stages")
    for label, agents in _visible_stages().items():
        st.write(f"- {label}: {agents}")


def _visible_stages() -> dict[str, str]:
    return {
        "Intake": "Intake Agent",
        "Scope": "Product Manager Agent, Business Analyst Agent",
        "Staffing": "Team Assembler Agent",
        "Plan": "Architecture Agent, PM Agent, Design Agent",
        "Execution": "Fullstack Agent through Codex",
        "Review": "QA Agent tool checks, Documentation / Handoff Agent",
        "Deployment": "Deployment Agent deployment-readiness inspection",
    }


if __name__ == "__main__":
    main()
