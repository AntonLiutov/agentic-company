"""Streamlit operator console for running local delivery workflows."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from agentic_company.console.services.graph_artifacts import refresh_graph_artifacts
from agentic_company.console.support import (
    ArtifactSpec,
    artifact_groups_for_run,
    clear_console_runs,
    codex_execution_running,
    console_status_label,
    create_console_run,
    delivery_overview_for_run,
    ensure_required_env_defaults,
    execution_completed,
    list_sample_requirements,
    load_sample_requirements,
    read_events,
    read_json_artifact,
    read_text_artifact,
    repo_root,
    start_codex_execution,
    team_lead_step_rows,
    workflow_should_refresh,
)
from agentic_company.console.views.live_logs import render_live_logs
from agentic_company.platform.logging import configure_logging

LOGGER = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    st.set_page_config(
        page_title="Agentic Planning Console",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _apply_console_styles()

    root = repo_root()
    _refresh_graph_artifacts(root)
    _ensure_state(root)

    st.title("Agentic Planning Console")
    st.caption("Run the upstream planning agents and inspect BA, architecture, and PM artifacts.")

    with st.sidebar:
        st.header("Run setup")
        _render_input_panel(root)
        st.divider()
        _render_cleanup_panel(root)

    _render_output_panel()


def _ensure_state(root: Path) -> None:
    st.session_state.setdefault("requirements_text", load_sample_requirements(root))
    st.session_state.setdefault("selected_requirements_sample", "multi-service-task-tracker.md")
    st.session_state.setdefault("last_run_dir", None)
    st.session_state.setdefault("last_error", None)
    st.session_state.setdefault("output_view", "Overview")


def _render_input_panel(root: Path) -> None:
    st.subheader("Requirements")

    sample_paths = list_sample_requirements(root)
    sample_options = [path.name for path in sample_paths]
    if sample_options:
        selected_sample = st.selectbox(
            "Sample requirements",
            sample_options,
            index=_sample_index(
                sample_options,
                str(st.session_state["selected_requirements_sample"]),
            ),
        )
        st.session_state["selected_requirements_sample"] = selected_sample
    else:
        selected_sample = ""
        st.warning("No sample requirements found.")

    button_col, path_col = st.columns([0.55, 1])
    if button_col.button("Load selected sample", width="stretch", disabled=not selected_sample):
        LOGGER.info("Loading sample requirements")
        st.session_state["requirements_text"] = load_sample_requirements(root, selected_sample)
        st.session_state["last_error"] = None
        st.rerun()
    if selected_sample:
        path_col.caption(f"Sample: `examples/requirements/{selected_sample}`")
    else:
        path_col.caption("No sample requirements found.")

    requirements_text = st.text_area(
        "Project requirements",
        value=st.session_state["requirements_text"],
        height=420,
    )
    st.session_state["requirements_text"] = requirements_text

    if st.button("Create Planning run", type="primary", width="stretch"):
        if not requirements_text.strip():
            st.session_state["last_error"] = "Requirements cannot be empty."
            LOGGER.warning("Planning run requested with empty requirements")
            st.rerun()
        try:
            LOGGER.info("Creating planning console run")
            run_dir = create_console_run(requirements_text, root / "runs")
            LOGGER.info("Starting planning graph run_dir=%s", run_dir)
            start_codex_execution(run_dir)
        except Exception as exc:  # pragma: no cover - shown in Streamlit
            st.session_state["last_error"] = str(exc)
            LOGGER.exception("Planning run failed to start")
        else:
            st.session_state["last_run_dir"] = str(run_dir)
            st.session_state["last_error"] = None
            st.session_state["output_view"] = "Live Logs"
            LOGGER.info("Planning graph started run_dir=%s", run_dir)
        st.rerun()

    st.info(
        "The platform graph runs Head Agent -> END. Head coordinates Business Analyst, "
        "Architect, Project Manager, and Team Lead as specialist tools; Team Lead owns "
        "Fullstack, QA, Deployment, and Handoff."
    )


def _sample_index(sample_options: list[str], selected_sample: str) -> int:
    if selected_sample in sample_options:
        return sample_options.index(selected_sample)
    return 0


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
    if st.button("Clear console runs", width="stretch", disabled=not confirm):
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
        st.write("Create a planning run to see artifacts and timeline here.")
        return

    run_dir = Path(str(run_dir_value))
    st.subheader("Run output")
    st.code(str(run_dir), language="text")
    generated_project_dir = run_dir / "generated-project"
    generated_app_exists = any(
        [
            (generated_project_dir / "app.py").exists(),
            (generated_project_dir / "api" / "app.py").exists(),
            (generated_project_dir / "web" / "app.py").exists(),
        ]
    )
    execution_is_completed = execution_completed(run_dir)
    execution_is_running = codex_execution_running(run_dir)
    overview = delivery_overview_for_run(run_dir)
    deployment_is_running = execution_is_running and overview.stage == "deployment"
    deployment_is_completed = overview.deployment_status == "deployed"
    if generated_app_exists:
        st.success("Generated project is available.")
    if workflow_should_refresh(run_dir, execution_is_running=execution_is_running):
        _auto_refresh()
    if execution_is_running:
        st.info("Execution workflow is running. Logs and stage status refresh automatically.")
    _render_stage_notice(
        run_dir,
        execution_is_running=execution_is_running,
        execution_is_completed=execution_is_completed,
        deployment_is_running=deployment_is_running,
        deployment_is_completed=deployment_is_completed,
    )
    if st.button(
        "Run Planning",
        width="stretch",
        disabled=(execution_is_completed or execution_is_running),
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

    view_options = ["Overview", "Artifacts", "Live Logs", "Timeline", "Summary"]
    selected_view = st.radio(
        "Run view",
        view_options,
        horizontal=True,
        label_visibility="collapsed",
        key="output_view",
    )

    if selected_view == "Overview":
        _render_delivery_overview(run_dir)
    elif selected_view == "Artifacts":
        _render_artifacts(run_dir)
    elif selected_view == "Live Logs":
        render_live_logs(run_dir)
    elif selected_view == "Timeline":
        _render_timeline(run_dir)
    else:
        _render_summary(run_dir)


def _render_artifacts(run_dir: Path) -> None:
    st.caption(
        "Client-facing handoff artifacts are shown first. "
        "Technical evidence stays available below for deeper review."
    )

    groups = artifact_groups_for_run(run_dir)
    if not groups:
        st.info("No artifacts have been written yet.")
        return

    handoff_primary: list[tuple[str, str, list[ArtifactSpec]]] = []
    technical_groups: list[tuple[str, str, list[ArtifactSpec]]] = []
    for group_name, description, artifacts in groups:
        if group_name != "Documentation / Handoff Agent":
            technical_groups.append((group_name, description, artifacts))
            continue

        primary = [artifact for artifact in artifacts if _is_handoff_release_report(artifact[0])]
        technical = [
            artifact for artifact in artifacts if not _is_handoff_release_report(artifact[0])
        ]
        if primary:
            handoff_primary.append((group_name, description, primary))
        if technical:
            technical_groups.append((group_name, description, technical))

    for group_name, description, artifacts in handoff_primary:
        existing = [artifact for artifact in artifacts if (run_dir / artifact[0]).exists()]
        if not existing:
            continue

        with st.container(border=True):
            st.subheader(f"{group_name} ({len(existing)})")
            st.caption(description)
            _render_artifact_entries(run_dir, existing)

    if not technical_groups:
        return

    with st.expander("Technical evidence and developer artifacts", expanded=False):
        st.caption(
            "Planning, implementation, QA, deployment, prompts, logs, and structured evidence."
        )
        for group_name, description, artifacts in technical_groups:
            existing = [artifact for artifact in artifacts if (run_dir / artifact[0]).exists()]
            if not existing:
                continue

            with st.container(border=True):
                st.subheader(f"{group_name} ({len(existing)})")
                st.caption(description)
                _render_artifact_entries(run_dir, existing)


def _render_delivery_overview(run_dir: Path) -> None:
    overview = delivery_overview_for_run(run_dir)
    if not overview.features and not overview.deployment_targets and not overview.blockers:
        return

    with st.container(border=True):
        st.subheader("Delivery Overview")
        metric_cols = st.columns(5)
        metric_cols[0].metric("Stage", _display_value(overview.stage))
        metric_cols[1].metric("Features", _feature_count_label(overview))
        metric_cols[2].metric("QA", _display_value(overview.qa_status or "pending"))
        metric_cols[3].metric(
            "Deployment",
            _display_value(overview.deployment_status or "pending"),
        )
        metric_cols[4].metric("Handoff", _display_value(overview.handoff_status or "pending"))

        if overview.blockers:
            st.error("Blocked: " + "; ".join(overview.blockers))

        if overview.current_work:
            current = overview.current_work
            st.caption("Current work")
            st.dataframe(
                [
                    {
                        "Stage": _display_value(current.stage or overview.stage),
                        "Feature": current.feature_id or "-",
                        "Lane": _display_value(current.lane or "pending"),
                        "Status": _feature_status_label(current.status, active=True),
                        "Owner": _owner_label(current.owner),
                        "Assigned": _owner_label(current.assigned_agent),
                        "Last Tool": console_status_label(current.last_tool.removeprefix("run_")),
                        "Last Target": current.last_target or "-",
                        "Last Result": console_status_label(current.last_status),
                        "Title": current.title,
                    }
                ],
                hide_index=True,
                width="stretch",
            )

        if overview.team_lead_steps:
            st.caption("Team Lead AgentExecutor history.")
            st.dataframe(
                team_lead_step_rows(overview.team_lead_steps),
                hide_index=True,
                width="stretch",
            )

        if overview.features:
            st.caption("Work board is shown compactly so longer release batches stay readable.")
            st.dataframe(
                [
                    {
                        "Feature": feature.feature_id,
                        "Sprint": feature.sprint_id or "-",
                        "Lane": _display_value(feature.lane or "todo"),
                        "Status": _feature_status_label(feature.status, active=feature.active),
                        "Points": feature.story_points,
                        "Repairs": feature.repair_attempts,
                        "Owner": _owner_label(feature.owner),
                        "Assigned": _owner_label(feature.assigned_agent),
                        "Artifacts": str(feature.artifact_count),
                        "Title": feature.title,
                    }
                    for feature in sorted(
                        overview.features,
                        key=lambda feature: (
                            feature.delivery_order or 9999,
                            feature.feature_id,
                        ),
                    )
                ],
                hide_index=True,
                width="stretch",
            )

        if overview.topology_summary or overview.deployment_targets:
            cols = st.columns([0.38, 0.62])
            with cols[0]:
                st.write("Topology")
                st.caption(
                    overview.topology_summary or "Topology will appear after planning/deployment."
                )
            with cols[1]:
                st.write("Public links")
                if overview.deployment_targets:
                    for target in overview.deployment_targets:
                        st.link_button(target.label, target.url)
                else:
                    st.caption("Public URLs will appear after deployment.")


def _feature_count_label(overview) -> str:
    total = overview.total_feature_count
    if total == 0:
        return "none"
    return f"{overview.completed_feature_count}/{total}"


def _display_value(value: str) -> str:
    return console_status_label(value)


def _feature_status_label(status: str, *, active: bool) -> str:
    label = _display_value(status)
    return f"{label} (active)" if active else label


def _owner_label(owner: str) -> str:
    if not owner:
        return ""
    return owner.replace("-agent", "").replace("-", " ").title()


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
                _render_json_artifact(path)
            elif path.suffix == ".html":
                st.iframe(
                    path,
                    width="stretch",
                    height=760,
                )
            elif path.suffix in {".jsonl", ".log", ".mmd", ".patch"}:
                st.code(read_text_artifact(path), language=_artifact_language(path))
            else:
                st.markdown(read_text_artifact(path))


def _render_json_artifact(path: Path) -> None:
    try:
        st.json(read_json_artifact(path))
    except json.JSONDecodeError as exc:
        st.warning(
            "This JSON artifact is not valid yet or was written with malformed JSON. "
            "Showing raw contents instead."
        )
        st.caption(f"JSON parse error: {exc}")
        st.code(read_text_artifact(path), language="json")


def _render_stage_notice(
    run_dir: Path,
    *,
    execution_is_running: bool,
    execution_is_completed: bool,
    deployment_is_running: bool,
    deployment_is_completed: bool,
) -> None:
    overview = delivery_overview_for_run(run_dir)
    business_analysis_path = run_dir / "upstream-planning" / "business-analysis.json"
    architecture_path = run_dir / "upstream-planning" / "architecture.json"
    project_management_path = (
        run_dir / "upstream-planning" / "project-management" / "release-plan.json"
    )
    business_analysis_running = _event_open(
        read_events(run_dir),
        "business_analysis_started",
        "business_analysis_completed",
    )
    architecture_running = _event_open(
        read_events(run_dir),
        "architecture_started",
        "architecture_completed",
    )
    project_management_running = _event_open(
        read_events(run_dir),
        "project_management_started",
        "project_management_completed",
    )
    execution_summary_exists = any(run_dir.glob("07-execution-summary*.md"))
    qa_results_path = run_dir / "qa" / "results.json"
    qa_results_exists = qa_results_path.exists() or any((run_dir / "qa").glob("results-*.json"))
    handoff_path = _latest_handoff_summary_path(run_dir)
    deployment_request_path = run_dir / "12-deployment-request.json"
    deployment_summary_path = run_dir / "13-deployment-summary.md"
    qa_status = overview.qa_status or _qa_status(qa_results_path)
    events = read_events(run_dir)
    qa_running = _event_open(events, "qa_started", "qa_completed")
    handoff_running = _event_open(events, "handoff_started", "handoff_completed")

    if business_analysis_running:
        current = "Business Analysis running"
        next_step = "Architecture starts after BA completes"
    elif architecture_running:
        current = "Architecture running"
        next_step = "Project Management starts after Architecture completes"
    elif project_management_running:
        current = "Project Management running"
        next_step = "Review release plan and candidate feature queue when the graph completes"
    elif not business_analysis_path.exists():
        current = "Ready for Business Analysis"
        next_step = "Run Business Analyst"
    elif business_analysis_path.exists() and not architecture_path.exists():
        current = "Business Analysis complete"
        next_step = "Architecture starts automatically in the graph"
    elif architecture_path.exists() and not project_management_path.exists():
        current = "Architecture complete"
        next_step = "Project Management starts automatically in the graph"
    elif project_management_path.exists() and execution_is_completed:
        current = "Project Management complete"
        next_step = "Review Head, BA, architecture, and PM artifacts"
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
    elif not execution_summary_exists:
        current = "Ready for execution"
        next_step = "Run delivery workflow"
    elif not qa_results_exists:
        current = "Execution complete"
        next_step = "QA starts automatically in the execution graph"
    elif qa_status == "failed":
        current = "QA failed"
        next_step = "Inspect QA evidence, fix, then rerun"
    elif deployment_is_running:
        current = "Deployment running"
        next_step = "Review deployment summary, then handoff"
    elif qa_status == "passed" and not deployment_summary_path.exists():
        current = "QA passed"
        next_step = "Deployment starts automatically in the delivery graph"
    elif deployment_summary_path.exists() and not deployment_is_completed:
        current = "Deployment needs attention"
        next_step = "Inspect deployment summary and retry"
    elif deployment_is_completed and not handoff_path.exists():
        current = "Deployment smoke checks passed"
        next_step = "Wait for final handoff summary"
    elif deployment_is_completed:
        current = "Deployment complete"
        next_step = "Review handoff summary"
    elif execution_is_completed and deployment_request_path.exists():
        current = "Deployment prepared"
        next_step = "Continue automatic delivery workflow"
    elif execution_is_completed:
        current = "Handoff ready"
        next_step = "Review artifacts"
    else:
        current = "Planning complete"
        next_step = "Continue execution"

    st.info(f"Current step: **{current}**. Next: **{next_step}**.")
    stages = [
        (
            "Business Analysis",
            "running"
            if business_analysis_running
            else "done"
            if business_analysis_path.exists()
            else "pending",
        ),
        (
            "Architecture",
            "running"
            if architecture_running
            else "done"
            if architecture_path.exists()
            else "pending",
        ),
        (
            "Project Management",
            "running"
            if project_management_running
            else "done"
            if project_management_path.exists()
            else "pending",
        ),
        ("Execution", _execution_stage(execution_summary_exists, execution_is_running)),
        ("QA", _qa_stage(qa_results_exists, qa_status, qa_running)),
        (
            "Deployment",
            _deployment_stage(
                deployment_summary_path,
                deployment_is_running,
                deployment_is_completed=deployment_is_completed,
            ),
        ),
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
        column.metric(label, console_status_label(status))


def _execution_stage(execution_summary_exists: bool, execution_is_running: bool) -> str:
    if execution_is_running:
        return "running"
    return "done" if execution_summary_exists else "pending"


def _qa_stage(qa_results_exists: bool, qa_status: str, qa_running: bool) -> str:
    if qa_running:
        return "running"
    if not qa_results_exists:
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


def _is_handoff_release_report(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized.startswith("handoff/") and normalized.endswith("/release-report.html")


def _latest_handoff_summary_path(run_dir: Path) -> Path:
    scoped = sorted((run_dir / "handoff").glob("**/release-report.html"))
    if scoped:
        return scoped[-1]
    return run_dir / "handoff" / "release-report.html"


def _deployment_stage(
    deployment_summary_path: Path,
    deployment_is_running: bool,
    *,
    deployment_is_completed: bool,
) -> str:
    if deployment_is_running:
        return "running"
    if deployment_is_completed:
        return "done"
    if not deployment_summary_path.exists():
        return "pending"
    status = _markdown_status(deployment_summary_path)
    return "done" if status == "deployed" else status or "needs attention"


def _markdown_status(path: Path) -> str:
    for line in _read_markdown_text(path).splitlines():
        if line.startswith("Status:"):
            return line.split(":", 1)[1].strip()
    return ""


def _read_markdown_text(path: Path) -> str:
    return read_text_artifact(path)


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


def _artifact_language(path: Path) -> str:
    if path.suffix == ".jsonl":
        return "json"
    if path.suffix == ".mmd":
        return "mermaid"
    if path.suffix == ".patch":
        return "diff"
    return "text"


def _artifact_runtime(filename: str) -> str:
    if filename.startswith("upstream-planning/project-management"):
        return "L6 Codex Project Manager"
    if filename.startswith("upstream-planning/architecture"):
        return "L6 Codex Architect"
    if filename.startswith("upstream-planning/business-analysis"):
        return "L6 Codex Business Analyst"
    if filename.startswith(("07-", "codex/")):
        return "L6 Codex Agent"
    if filename.startswith(("13-deployment-summary", "deployment/")):
        return "L6 Codex Deployment Agent"
    if filename.startswith(("08-", "qa/")):
        return "L6 Codex QA Agent"
    return "Unknown Runtime"


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
        cols[4].write(artifact or event.get("runtime", "Unknown Runtime"))


def _render_summary(run_dir: Path) -> None:
    events = read_events(run_dir)
    overview = delivery_overview_for_run(run_dir)
    artifact_count = sum(len(artifacts) for _, _, artifacts in artifact_groups_for_run(run_dir))

    metric_cols = st.columns(4)
    metric_cols[0].metric("Artifacts", artifact_count)
    metric_cols[1].metric("Events", len(events))
    metric_cols[2].metric("Features", _feature_count_label(overview))
    metric_cols[3].metric("Status", console_status_label(overview.status))

    st.write("Visible stages")
    for label, agents in _visible_stages().items():
        st.write(f"- {label}: {agents}")


def _apply_console_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --agentic-sidebar-width: min(42rem, 90vw);
        }

        section[data-testid="stSidebar"][aria-expanded="true"] {
            min-width: var(--agentic-sidebar-width) !important;
            max-width: var(--agentic-sidebar-width) !important;
        }

        section[data-testid="stSidebar"][aria-expanded="true"] div[data-testid="stSidebarContent"] {
            min-width: var(--agentic-sidebar-width) !important;
            max-width: var(--agentic-sidebar-width) !important;
        }

        section[data-testid="stSidebar"][aria-expanded="true"]
        div[data-testid="stSidebarUserContent"] {
            padding-left: 1.25rem;
            padding-right: 1.25rem;
        }

        @media (max-width: 900px) {
            :root {
                --agentic-sidebar-width: min(34rem, 92vw);
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _visible_stages() -> dict[str, str]:
    return {
        "Intake": "Intake Agent",
        "Scope": "Product Manager Agent, Business Analyst Agent",
        "Staffing": "Team Assembler Agent",
        "Plan": "Architecture Agent, PM Agent, Design Agent",
        "Execution": "Fullstack Agent through Codex",
        "Review": "QA Agent autonomous Codex review and evidence",
        "Deployment": "Deployment Agent automatic Azure delivery",
        "Handoff": "Documentation / Handoff Agent client release report",
    }


if __name__ == "__main__":
    main()
