import json
from pathlib import Path
from types import SimpleNamespace

from agentic_company.console.web.product import (
    ArtifactView,
    BoardCard,
    _activity_groups_for_card,
    _business_log_text,
    _llm_text_content,
    _log_matches_card,
    _reports_for_card,
    agent_catalog,
    artifact_payload_by_id,
    artifacts_for_run,
    board_groups_for_run,
    canonical_activity_groups_for_run,
    canonical_artifacts_for_run,
    canonical_board_cards_for_run,
    canonical_delivery_overview_for_run,
    canonical_rendered_log_entries_for_run,
    canonical_task_detail_for_run,
    delivery_overview_from_work_items,
    format_request_text,
    html_report_document,
    is_user_facing_artifact,
    live_log_entries_for_run,
    render_markdown,
    run_timing_for_run,
    status_label,
    task_detail_for_run,
    user_facing_blockers,
    work_plan_groups_for_run,
)
from agentic_company.platform.artifact_registry import register_artifact
from agentic_company.platform.run_trace import RunEvent, ToolCallEvent, record_run_event
from agentic_company.platform.state import DELIVERY_STATE_SNAPSHOT


def delivery_state_path(run_dir: Path) -> Path:
    state_path = run_dir / DELIVERY_STATE_SNAPSHOT
    state_path.parent.mkdir(parents=True, exist_ok=True)
    return state_path


def write_run_events_fixture(run_dir: Path, events: list[dict[str, object]]) -> None:
    for index, event in enumerate(events):
        data = event.get("data", {})
        if not isinstance(data, dict):
            data = {}
        record_run_event(
            run_dir,
            run_id=str(event.get("run_id") or "run"),
            agent_id=str(event.get("agent_id") or ""),
            event_type=str(event.get("event") or ""),
            status=str(data.get("status") or event.get("status") or ""),
            message=str(event.get("message") or event.get("event") or ""),
            data=data,
            created_at=str(event.get("timestamp") or f"2026-05-18T01:00:{index:02d}Z"),
        )


def test_delivery_overview_from_work_items_uses_db_state_without_trace():
    overview = delivery_overview_from_work_items(
        run_id="42",
        work_items=[
            SimpleNamespace(
                work_item_id="US-rooms",
                title="Rooms",
                sprint_id="sprint-01",
                delivery_order=1,
                status="review",
                lane="qa",
                owner_agent="qa-agent",
                active=True,
                artifact_ids=[],
                blocker="",
                created_at="2026-05-31T10:00:00Z",
                updated_at="2026-05-31T10:05:00Z",
            )
        ],
        artifacts=[],
        status="running",
    )

    assert overview.features[0].feature_id == "US-rooms"
    assert overview.features[0].owner == "Quality Reviewer"
    assert overview.active_feature_id == "US-rooms"
    assert overview.qa_status == "review"


def test_format_request_text_structures_dictated_request_without_greeting():
    formatted = format_request_text(
        "hi hi I want to build a small app this stream lead and just a small app "
        "you can put anything whatever you want just three buttons and any actions "
        "you can preach and rated I mean you can generate it locally and then just "
        "when I click something appears like chalks numbers some actions whatever "
        "you want but we three buttons and three possible actions for each action "
        "and let's assume that sort of five different states when I click on each "
        "button it should be deployed and I want to see a small simple report for "
        "me and I've linked with this application"
    )

    assert formatted.startswith("# Product Request")
    assert "Build a small web app with three buttons" in formatted
    assert "hi hi" not in formatted.split("## Requirements", maxsplit=1)[0].lower()
    assert "- Build a small web app." in formatted
    assert "- Include three clearly visible buttons." in formatted
    assert "- Support three possible user actions." in formatted
    assert "- Support about five different app states." in formatted
    assert "- Deploy the finished app." in formatted
    assert "charts, numbers, or simple actions" in formatted
    assert "small, simple report with the application link" in formatted
    assert "## Original Dictated Text" not in formatted
    assert "> hi hi I want" not in formatted


def test_llm_text_content_extracts_gemini_text_block():
    content = [
        {
            "type": "text",
            "text": "# Product Request\n\n## Summary\nDevelop a smart joke app.",
            "extras": {"signature": "hidden"},
        }
    ]

    assert _llm_text_content(content) == (
        "# Product Request\n\n## Summary\nDevelop a smart joke app."
    )


def test_status_label_hides_internal_agent_and_graph_terms():
    assert status_label("head") == "Coordinator"
    assert status_label("head_delivery_completed") == "Delivery Complete"
    assert status_label("business_analysis_completed") == "Requirements Ready"
    assert status_label("project_management_completed") == "Delivery Plan Ready"
    assert status_label("deployment_deployed") == "Published"
    assert status_label("feature_queue_qa_completed_deployment_ready") == "Ready for Publishing"


def test_agent_catalog_uses_role_initials_and_icons():
    agents = {agent["name"]: agent for agent in agent_catalog()}

    assert agents["Coordinator"]["initials"] == "CO"
    assert agents["Coordinator"]["icon"] == "/static/agents/coordinator.png"
    assert agents["Business Analyst"]["initials"] == "BA"
    assert agents["Business Analyst"]["icon"] == "/static/agents/business-analyst.png"
    assert agents["Solution Architect"]["initials"] == "SA"
    assert agents["Delivery Planner"]["initials"] == "DP"
    assert agents["Delivery Lead"]["initials"] == "DL"
    assert agents["Builder"]["initials"] == "B"
    assert agents["Quality Reviewer"]["initials"] == "QR"
    assert agents["Publisher"]["initials"] == "P"
    assert agents["Release Reporter"]["initials"] == "RP"


def test_handoff_reports_expose_only_html_release_reports():
    assert is_user_facing_artifact("handoff/sprints/sprint-01/release-report.html")
    assert not is_user_facing_artifact("handoff/sprints/sprint-01/09-handoff-summary.md")
    assert not is_user_facing_artifact("handoff/sprints/sprint-01/release-evidence.json")


def test_canonical_artifacts_hide_internal_generated_project_files(tmp_path):
    report = tmp_path / "qa" / "F1" / "08-qa-report-F1.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Quality summary\n", encoding="utf-8")
    env_file = tmp_path / "generated-project" / ".env.example"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    visible = register_artifact(
        tmp_path,
        relative_path="qa/F1/08-qa-report-F1.md",
        owner_agent="Quality Reviewer",
        artifact_type="qa_report",
        visibility="qa_evidence",
        label="Quality summary",
        work_item_id="F1",
    )
    hidden = register_artifact(
        tmp_path,
        relative_path="generated-project/.env.example",
        owner_agent="Builder",
        artifact_type="artifact",
        visibility="business",
        label=".env.example",
    )

    business, technical = canonical_artifacts_for_run(tmp_path, [visible, hidden])

    assert [artifact.label for artifact in business] == ["Quality summary - F1"]
    assert [artifact.path for artifact in technical] == ["generated-project/.env.example"]


def test_canonical_board_and_task_detail_use_trace_and_artifact_ids(tmp_path):
    report = tmp_path / "qa" / "F1" / "08-qa-report-F1.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Quality summary\n", encoding="utf-8")
    record = register_artifact(
        tmp_path,
        relative_path="qa/F1/08-qa-report-F1.md",
        owner_agent="Quality Reviewer",
        artifact_type="qa_report",
        visibility="qa_evidence",
        label="Quality summary",
        work_item_id="F1",
    )
    events = [
        ToolCallEvent(
            event_id="tool-1",
            run_id="run-1",
            work_item_id="F1",
            agent_id="fullstack-agent",
            tool_name="codex_exec",
            tool_call_id="call-1",
            status="succeeded",
            output_summary={
                "dashboard_update": {
                    "status": "done",
                    "summary": "Built the feature.",
                    "comment": "The feature is implemented and ready.",
                },
                "feature_title": "Build app",
            },
            created_at="2026-05-24T10:00:00Z",
        )
    ]
    artifacts = canonical_artifacts_for_run(tmp_path, [record])[0]

    board = canonical_board_cards_for_run(tmp_path, events, artifacts)
    detail = canonical_task_detail_for_run(tmp_path, "F1", events, artifacts)

    assert board["done"][0].id == "F1"
    assert board["done"][0].elapsed_label == "0s"
    assert detail is not None
    assert detail.reports[0].artifact_id == record.artifact_id
    assert detail.activity_groups[0]["count"] == 1


def test_canonical_overview_ignores_repaired_stale_quality_failure():
    events = [
        ToolCallEvent(
            event_id="tool-fail",
            run_id="run-1",
            work_item_id="F1",
            agent_id="qa-agent",
            tool_name="run_qa",
            tool_call_id="call-fail",
            status="qa_failed",
            failure_mode="needs_repair",
            output_summary={
                "business_summary": "Quality failed because a platform gate failed.",
                "dashboard_update": {
                    "status": "blocked",
                    "comment": "Quality failed because a platform gate failed.",
                },
            },
            created_at="2026-05-24T10:00:00Z",
        ),
        ToolCallEvent(
            event_id="tool-pass",
            run_id="run-1",
            work_item_id="F1",
            agent_id="qa-agent",
            tool_name="run_qa",
            tool_call_id="call-pass",
            status="qa_passed",
            output_summary={
                "business_summary": "Quality passed after repair.",
                "dashboard_update": {
                    "status": "done",
                    "comment": "Quality passed after repair.",
                },
            },
            created_at="2026-05-24T10:05:00Z",
        ),
    ]

    overview = canonical_delivery_overview_for_run(
        run_id="run-1",
        run_events=[],
        tool_events=events,
        artifacts=[],
        status="running",
    )

    assert overview.blockers == []
    assert overview.qa_status == "passed"


def test_canonical_overview_marks_quality_review_after_repair_started():
    events = [
        ToolCallEvent(
            event_id="tool-fail",
            run_id="run-1",
            work_item_id="F2",
            agent_id="qa-agent",
            tool_name="run_qa",
            tool_call_id="call-fail",
            status="qa_feature_failed_repair_ready",
            failure_mode="needs_repair",
            output_summary={
                "business_summary": "Quality failed.",
                "dashboard_update": {"status": "blocked", "comment": "Quality failed."},
            },
            created_at="2026-05-24T10:00:00Z",
        ),
        ToolCallEvent(
            event_id="tool-repair",
            run_id="run-1",
            work_item_id="F2",
            agent_id="fullstack-agent",
            tool_name="run_fullstack",
            tool_call_id="call-repair",
            status="fullstack_feature_implemented",
            output_summary={
                "business_summary": "Repair implemented.",
                "dashboard_update": {"status": "in_progress", "comment": "Repair implemented."},
            },
            created_at="2026-05-24T10:05:00Z",
        ),
    ]
    run_events = [
        {
            "event_type": "qa_started",
            "created_at": "2026-05-24T10:06:00Z",
            "data": {"feature_id": "F2"},
        }
    ]

    overview = canonical_delivery_overview_for_run(
        run_id="run-1",
        run_events=run_events,
        tool_events=events,
        artifacts=[],
        status="running",
    )

    assert overview.blockers == []
    assert overview.qa_status == "review"


def test_deployment_internal_markdown_is_hidden_from_product_console():
    assert not is_user_facing_artifact("11-deployment-plan.md")
    assert not is_user_facing_artifact("12-deployment-request.md")
    assert is_user_facing_artifact("13-deployment-summary.md")


def test_render_markdown_formats_inline_code_identifiers():
    rendered = render_markdown("Open `US-02` and review the task.")

    assert "<code>US-02</code>" in rendered


def test_html_report_document_forces_report_links_to_new_tab():
    rendered = html_report_document("<html><head><title>Report</title></head><body></body></html>")

    assert '<base target="_blank">' in rendered
    assert rendered.index('<base target="_blank">') > rendered.index("<head>")


def test_render_markdown_formats_codex_style_activity_quote():
    rendered = render_markdown(
        "**2026-05-17 23:00:02 - Builder** `Builder` > I am updating the app."
    )

    assert "<strong>2026-05-17 23:00:02 - Builder</strong>" in rendered
    assert "<code>Builder</code>" in rendered
    assert "<blockquote>I am updating the app.</blockquote>" in rendered


def test_render_markdown_keeps_activity_file_names_non_clickable():
    rendered = render_markdown(
        "- [app.py](C:\\Users\\aliutov\\Projects\\agentic-company\\runs\\x\\app.py)"
    )

    assert "<code>app.py</code>" in rendered
    assert "<a " not in rendered
    assert "C:\\Users" not in rendered


def test_activity_logs_hide_local_paths():
    entry = _business_log_text(
        "Updated files:\n- [app.py](C:\\Users\\aliutov\\Projects\\agentic-company\\runs\\x\\app.py)"
    )

    assert "app.py" not in entry
    assert "C:\\Users" not in entry


def test_activity_logs_hide_internal_artifact_lists():
    entry = _business_log_text(
        "**2026-05-18 04:50:45 - Quality Reviewer**\n\n"
        "> Completed Quality and wrote required artifacts:\n"
        ">\n"
        "> - `08-qa-report-US-01.md`\n"
        "> - `qa-results-US-01.json`\n"
        ">\n"
        "> Contract checks:\n"
        "> Results JSON is valid."
    )

    assert "Quality Reviewer" in entry
    assert ".md" not in entry
    assert ".json" not in entry
    assert "Contract checks" not in entry
    assert "- `" not in entry


def test_activity_logs_hide_codex_executor_name():
    entry = _business_log_text("2026-05-17 23:00:02 - Codex (US-02)")

    assert "Builder (US-02)" in entry
    assert "Codex" not in entry


def test_live_logs_include_upstream_business_analyst_commentary(tmp_path):
    execution_dir = (
        tmp_path / "upstream-planning" / "business-analyst" / "codex" / "exec-requirements"
    )
    execution_dir.mkdir(parents=True)
    (execution_dir / "events.jsonl").write_text(
        '{"codex_execution_id": "codex-run-business-analyst-agent", '
        '"recorded_at": "2026-05-18T01:35:26", '
        '"type": "item.completed", '
        '"item": {"type": "agent_message", "text": "I am clarifying the request."}}\n',
        encoding="utf-8",
    )

    logs = live_log_entries_for_run(tmp_path)

    assert any("Business Analyst" in log for log in logs)
    assert any("I am clarifying the request." in log for log in logs)
    assert not any("Builder" in log for log in logs)
    assert not any("Codex" in log for log in logs)


def test_live_logs_include_upstream_architect_commentary(tmp_path):
    execution_dir = tmp_path / "upstream-planning" / "architect" / "codex" / "exec-architecture"
    execution_dir.mkdir(parents=True)
    (execution_dir / "events.jsonl").write_text(
        '{"recorded_at": "2026-05-18T01:36:26", '
        '"type": "item.completed", '
        '"item": {"type": "agent_message", "text": "Architecture is ready."}}\n',
        encoding="utf-8",
    )

    logs = live_log_entries_for_run(tmp_path)

    assert any("Solution Architect" in log for log in logs)
    assert any("Architecture is ready." in log for log in logs)
    assert not any("Builder" in log for log in logs)
    assert not any("Codex" in log for log in logs)


def test_canonical_activity_includes_codex_agent_message_on_matching_task():
    run_events = [
        RunEvent(
            event_id="run_codex_message",
            project_id=None,
            run_id="run-1",
            work_item_id="PLAN-03",
            agent_id="project-manager-agent",
            event_type="codex_agent_message",
            status="in_progress",
            message="I am decomposing the release into executable work.",
            created_at="2026-05-25T12:00:00",
        )
    ]

    groups = canonical_activity_groups_for_run([], task_id="PLAN-03", run_events=run_events)

    assert groups[0]["owner"] == "Delivery Planner"
    assert "decomposing the release" in str(groups[0]["logs"][0])


def test_canonical_activity_hides_codex_command_events_and_dedupes_final_text():
    run_events = [
        RunEvent(
            event_id="command-start",
            project_id=None,
            run_id="run-1",
            work_item_id="PLAN-01",
            agent_id="business-analyst-agent",
            event_type="codex_command_started",
            status="running",
            message='Started command: "Get-Content requirements"',
            created_at="2026-05-25T12:00:00",
        ),
        RunEvent(
            event_id="codex-final",
            project_id=None,
            run_id="run-1",
            work_item_id="PLAN-01",
            agent_id="business-analyst-agent",
            event_type="codex_agent_message",
            status="in_progress",
            message="Wrote both allowed business analysis artifacts.",
            created_at="2026-05-25T12:01:00",
        ),
        RunEvent(
            event_id="workflow-final",
            project_id=None,
            run_id="run-1",
            work_item_id="PLAN-01",
            agent_id="business-analyst-agent",
            event_type="business_analysis_completed",
            status="done",
            message="Wrote both allowed business analysis artifacts.",
            created_at="2026-05-25T12:01:01",
        ),
    ]

    groups = canonical_activity_groups_for_run([], task_id="PLAN-01", run_events=run_events)
    rendered = "\n".join(str(log) for group in groups for log in group["logs"])

    assert "Started command" not in rendered
    assert rendered.count("Wrote both allowed business analysis artifacts.") == 1


def test_canonical_activity_sorts_mixed_run_and_tool_events_by_time():
    run_events = [
        RunEvent(
            event_id="later-run",
            project_id=None,
            run_id="run-1",
            work_item_id="PLAN-01",
            agent_id="business-analyst-agent",
            event_type="codex_agent_message",
            status="in_progress",
            message="Second update.",
            created_at="2026-05-25T12:02:00Z",
        )
    ]
    tool_events = [
        ToolCallEvent(
            event_id="earlier-tool",
            run_id="run-1",
            work_item_id="PLAN-01",
            agent_id="business-analyst-agent",
            tool_name="runtime_progress",
            tool_call_id="call-1",
            status="in_progress",
            output_summary={
                "dashboard_update": {
                    "comment": "First update.",
                    "status": "in_progress",
                }
            },
            created_at="2026-05-25T12:01:00Z",
        )
    ]

    groups = canonical_activity_groups_for_run(
        tool_events,
        task_id="PLAN-01",
        run_events=run_events,
    )
    rendered_logs = [str(log) for log in groups[0]["logs"]]

    assert "First update." in rendered_logs[0]
    assert "Second update." in rendered_logs[1]


def test_canonical_rendered_logs_filter_by_task_id_for_live_task_detail():
    run_events = [
        RunEvent(
            event_id="ba-message",
            project_id=None,
            run_id="run-1",
            work_item_id="PLAN-01",
            agent_id="business-analyst-agent",
            event_type="codex_agent_message",
            status="in_progress",
            message="BA is refining requirements.",
            created_at="2026-05-25T21:00:00Z",
        ),
        RunEvent(
            event_id="pm-message",
            project_id=None,
            run_id="run-1",
            work_item_id="PLAN-03",
            agent_id="project-manager-agent",
            event_type="codex_agent_message",
            status="in_progress",
            message="PM is shaping the delivery plan.",
            created_at="2026-05-25T21:00:01Z",
        ),
    ]

    logs = canonical_rendered_log_entries_for_run([], run_events, task_id="BA")

    assert len(logs) == 1
    assert "BA is refining requirements." in logs[0]
    assert "PM is shaping" not in logs[0]


def test_board_reopens_task_when_newer_progress_follows_old_done(tmp_path):
    run_events = [
        RunEvent(
            event_id="old-done",
            project_id=None,
            run_id="run-1",
            work_item_id="PLAN-01",
            agent_id="business-analyst-agent",
            event_type="business_analysis_completed",
            status="done",
            message="Requirements brief is ready.",
            created_at="2026-05-25T17:32:12Z",
        ),
        RunEvent(
            event_id="new-progress",
            project_id=None,
            run_id="run-1",
            work_item_id="PLAN-01",
            agent_id="business-analyst-agent",
            event_type="codex_agent_message",
            status="in_progress",
            message="I am inspecting the full requirements.",
            created_at="2026-05-25T21:29:36Z",
        ),
    ]

    board = canonical_board_cards_for_run(tmp_path, [], [], run_events)
    card = board["in_progress"][0]

    assert card.id == "PLAN-01"
    assert card.active
    assert card.completed_at == ""


def test_board_marks_task_done_when_latest_event_is_completion(tmp_path):
    run_events = [
        RunEvent(
            event_id="progress",
            project_id=None,
            run_id="run-1",
            work_item_id="PLAN-01",
            agent_id="business-analyst-agent",
            event_type="codex_agent_message",
            status="in_progress",
            message="I am writing the requirements brief.",
            created_at="2026-05-25T21:29:36Z",
        ),
        RunEvent(
            event_id="done",
            project_id=None,
            run_id="run-1",
            work_item_id="PLAN-01",
            agent_id="business-analyst-agent",
            event_type="business_analysis_completed",
            status="done",
            message="Requirements brief is ready.",
            created_at="2026-05-25T21:32:12Z",
        ),
    ]

    board = canonical_board_cards_for_run(tmp_path, [], [], run_events)
    card = board["done"][0]

    assert card.id == "PLAN-01"
    assert not card.active
    assert card.completed_at == "2026-05-25T21:32:12Z"


def test_board_does_not_reopen_done_task_from_local_time_codex_message(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Tbilisi")
    run_events = [
        RunEvent(
            event_id="done",
            project_id=None,
            run_id="run-1",
            work_item_id="US-rooms",
            agent_id="qa-agent",
            event_type="qa_completed",
            status="qa_passed",
            message="Quality passed.",
            created_at="2026-05-25T19:03:40Z",
        ),
        RunEvent(
            event_id="local-progress",
            project_id=None,
            run_id="run-1",
            work_item_id="US-rooms",
            agent_id="qa-agent",
            event_type="codex_agent_message",
            status="in_progress",
            message="QA_STATUS: passed",
            created_at="2026-05-25T23:03:37",
        ),
    ]

    board = canonical_board_cards_for_run(tmp_path, [], [], run_events)

    card = board["done"][0]
    assert card.id == "US-rooms"
    assert card.status == "Done"


def test_live_logs_show_agent_start_once_without_workflow_noise(tmp_path):
    write_run_events_fixture(
        tmp_path,
        [
            {
                "timestamp": "2026-05-18T01:32:41Z",
                "agent_id": "delivery-graph",
                "event": "delivery_graph_state_written",
                "data": {"status": "initialized"},
            },
            {
                "timestamp": "2026-05-18T01:32:43Z",
                "agent_id": "head-agent",
                "event": "head_worker_started",
                "data": {"node": "business_analyst", "target_agent": "business-analyst-agent"},
            },
            {
                "timestamp": "2026-05-18T01:32:46Z",
                "agent_id": "business-analyst-agent",
                "event": "business_analysis_codex_started",
                "data": {"status": "running"},
            },
            {
                "timestamp": "2026-05-18T01:35:45Z",
                "agent_id": "business-analyst-agent",
                "event": "business_analysis_codex_completed",
                "data": {"status": "done"},
            },
        ],
    )

    logs = live_log_entries_for_run(tmp_path)
    rendered = "\n".join(logs)

    assert "Business Analyst started working" in rendered
    assert "Progress saved" not in rendered
    assert "Delivery workflow" not in rendered
    assert "Business Analyst started\n" not in rendered
    assert "Business Analyst completed" not in rendered
    assert "Coordinator work" not in rendered


def test_work_plan_overview_does_not_duplicate_planning_group(tmp_path):
    upstream = tmp_path / "upstream-planning"
    upstream.mkdir()
    (upstream / "business-analysis.md").write_text("# Requirements\n", encoding="utf-8")
    (upstream / "architecture.md").write_text("# Architecture\n", encoding="utf-8")
    write_run_events_fixture(
        tmp_path,
        [
            {
                "timestamp": "2026-05-18T01:32:43Z",
                "agent_id": "business-analyst-agent",
                "event": "business_analysis_completed",
                "data": {"status": "done"},
            }
        ],
    )

    groups = work_plan_groups_for_run(tmp_path)

    assert [group["name"] for group in groups].count("Planning") == 1


def test_work_plan_cards_include_elapsed_time(tmp_path):
    write_run_events_fixture(
        tmp_path,
        [
            {
                "timestamp": "2026-05-18T01:00:00Z",
                "agent_id": "business-analyst-agent",
                "event": "business_analysis_started",
                "data": {"status": "started"},
            },
            {
                "timestamp": "2026-05-18T01:02:05Z",
                "agent_id": "business-analyst-agent",
                "event": "business_analysis_completed",
                "data": {"status": "done"},
            },
        ],
    )

    card = work_plan_groups_for_run(tmp_path)[0]["cards"][0]

    assert card.started_at == "2026-05-18T01:00:00Z"
    assert card.completed_at == "2026-05-18T01:02:05Z"
    assert card.elapsed_label == "2m"


def test_run_timing_uses_open_run_elapsed(tmp_path):
    write_run_events_fixture(
        tmp_path,
        [
            {
                "timestamp": "2026-05-18T01:00:00Z",
                "agent_id": "head-agent",
                "event": "head_planning_started",
                "data": {},
            }
        ],
    )

    timing = run_timing_for_run(tmp_path)

    assert timing["started_at"] == "2026-05-18T01:00:00Z"
    assert timing["completed_at"] == ""
    assert timing["elapsed_label"]


def test_planning_task_detail_links_requirements_report(tmp_path):
    report = tmp_path / "upstream-planning" / "business-analysis.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Requirements brief\n", encoding="utf-8")

    artifacts = artifacts_for_run(tmp_path)[0]
    detail = task_detail_for_run(tmp_path, "PLAN-01", artifacts)

    assert detail is not None
    assert detail.card.title == "Requirements brief"
    assert [artifact.label for artifact in detail.reports] == ["Requirements brief"]


def test_artifacts_for_run_prefers_registry_records(tmp_path):
    report = tmp_path / "handoff" / "project" / "final" / "release-report.html"
    report.parent.mkdir(parents=True)
    report.write_text("<h1>Release</h1>", encoding="utf-8")
    record = register_artifact(
        tmp_path,
        relative_path="handoff/project/final/release-report.html",
        run_id="run-1",
        owner_agent="documentation-handoff-agent",
        label="Registered release",
        visibility="release",
        artifact_type="release_report",
    )

    artifacts = artifacts_for_run(tmp_path)[0]

    assert artifacts[0].artifact_id == record.artifact_id
    assert artifacts[0].visibility == "release"
    assert artifact_payload_by_id(tmp_path, record.artifact_id)["kind"] == "html"


def test_planning_detail_accepts_live_runtime_aliases(tmp_path):
    report = tmp_path / "upstream-planning" / "business-analysis.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Requirements brief\n", encoding="utf-8")

    artifacts = artifacts_for_run(tmp_path)[0]
    detail = task_detail_for_run(tmp_path, "BA", artifacts)

    assert detail is not None
    assert detail.card.id == "PLAN-01"
    assert [artifact.label for artifact in detail.reports] == ["Requirements brief"]


def test_board_keeps_stable_planning_cards_when_runtime_has_planning_features(tmp_path):
    report = tmp_path / "upstream-planning" / "business-analysis.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Requirements brief\n", encoding="utf-8")
    delivery_state_path(tmp_path).write_text(
        json.dumps(
            {
                "run_id": "run",
                "stage": "business_analysis",
                "status": "business_analysis_completed",
                "feature_queue": [
                    {
                        "id": "BA",
                        "title": "Business analysis",
                        "sprint_id": "planning",
                        "suggested_owner_agent": "business-analyst-agent",
                    }
                ],
                "feature_statuses": {"BA": "done"},
            }
        ),
        encoding="utf-8",
    )

    planning_cards = board_groups_for_run(tmp_path)["Planning"]

    assert [card.id for card in planning_cards] == ["PLAN-01", "PLAN-02", "PLAN-03", "PLAN-04"]


def test_board_shows_pm_planned_features_before_runtime_events(tmp_path):
    queue_path = tmp_path / "upstream-planning" / "project-management"
    queue_path.mkdir(parents=True)
    (queue_path / "candidate-feature-queue.json").write_text(
        json.dumps(
            [
                {
                    "id": "US-rooms",
                    "title": "Rooms and invitations",
                    "sprint_id": "sprint-02",
                    "delivery_order": 20,
                    "suggested_owner_agent": "fullstack-agent",
                },
                {
                    "id": "US-contacts-friends",
                    "title": "Contacts and friends",
                    "sprint_id": "sprint-02",
                    "delivery_order": 21,
                    "suggested_owner_agent": "fullstack-agent",
                },
            ]
        ),
        encoding="utf-8",
    )

    groups = canonical_board_cards_for_run(tmp_path, tool_events=[], artifacts=[], run_events=[])

    todo_ids = [card.id for card in groups["todo"]]
    assert todo_ids == ["US-rooms", "US-contacts-friends"]
    assert all(card.status == "To Do" for card in groups["todo"])


def test_planning_card_moves_to_in_progress_after_start_before_report(tmp_path):
    write_run_events_fixture(
        tmp_path,
        [
            {
                "timestamp": "2026-05-18T01:00:00Z",
                "agent_id": "architect-agent",
                "event": "architecture_started",
                "data": {},
            }
        ],
    )

    planning_cards = board_groups_for_run(tmp_path)["Planning"]
    architecture_card = next(card for card in planning_cards if card.id == "PLAN-02")

    assert architecture_card.status == "In Progress"
    assert architecture_card.column == "in_progress"
    assert architecture_card.active is True
    assert architecture_card.elapsed_label


def test_planning_card_stays_in_progress_when_report_exists_before_completion(tmp_path):
    write_run_events_fixture(
        tmp_path,
        [
            {
                "timestamp": "2026-05-18T01:00:00Z",
                "agent_id": "architect-agent",
                "event": "architecture_started",
                "data": {},
            }
        ],
    )
    report = tmp_path / "upstream-planning" / "architecture.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Architecture draft\n", encoding="utf-8")

    planning_cards = board_groups_for_run(tmp_path)["Planning"]
    architecture_card = next(card for card in planning_cards if card.id == "PLAN-02")

    assert architecture_card.status == "In Progress"
    assert architecture_card.column == "in_progress"
    assert architecture_card.artifact_count == 1


def test_planning_card_moves_to_done_after_completion_and_report(tmp_path):
    write_run_events_fixture(
        tmp_path,
        [
            {
                "timestamp": "2026-05-18T01:00:00Z",
                "agent_id": "architect-agent",
                "event": "architecture_started",
                "data": {},
            },
            {
                "timestamp": "2026-05-18T01:03:00Z",
                "agent_id": "architect-agent",
                "event": "architecture_completed",
                "data": {},
            },
        ],
    )
    report = tmp_path / "upstream-planning" / "architecture.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Architecture final\n", encoding="utf-8")

    planning_cards = board_groups_for_run(tmp_path)["Planning"]
    architecture_card = next(card for card in planning_cards if card.id == "PLAN-02")

    assert architecture_card.status == "Done"
    assert architecture_card.column == "done"
    assert architecture_card.active is False


def test_planning_card_needs_attention_when_completed_without_report(tmp_path):
    write_run_events_fixture(
        tmp_path,
        [
            {
                "timestamp": "2026-05-18T01:00:00Z",
                "agent_id": "architect-agent",
                "event": "architecture_started",
                "data": {},
            },
            {
                "timestamp": "2026-05-18T01:03:00Z",
                "agent_id": "architect-agent",
                "event": "architecture_completed",
                "data": {},
            },
        ],
    )

    planning_cards = board_groups_for_run(tmp_path)["Planning"]
    architecture_card = next(card for card in planning_cards if card.id == "PLAN-02")

    assert architecture_card.status == "Needs attention"
    assert architecture_card.column == "blocked"


def test_task_detail_groups_activity_by_owner(tmp_path):
    card = BoardCard(
        id="US-01",
        title="Build feature",
        owner="Builder",
        sprint="Sprint 1",
        status="Done",
        column="done",
        artifact_count=1,
        active=False,
    )

    groups = _activity_groups_for_card(card, ["2026-05-17 23:00:02 - Builder (US-01)"])

    assert groups[0]["owner"] == "Builder"
    assert "Builder (US-01)" in str(groups[0]["logs"][0])


def test_feature_activity_requires_specific_work_item_match():
    card = BoardCard(
        id="US-01",
        title="First feature",
        owner="Builder",
        sprint="Sprint 1",
        status="Done",
        column="done",
        artifact_count=1,
        active=False,
    )

    assert _log_matches_card("2026-05-18 - Builder (US-01)\n\nWorking on it.", card)
    assert not _log_matches_card("2026-05-18 - Builder (US-02)\n\nWorking on it.", card)
    assert not _log_matches_card(
        "2026-05-18 - Delivery Planner\n\nPlanned feature ids: `US-01`, `US-02`.",
        card,
    )


def test_release_report_card_uses_final_handoff_timing(tmp_path):
    delivery_state_path(tmp_path).write_text(
        json.dumps(
            {
                "run_id": "run",
                "stage": "head",
                "status": "head_delivery_completed",
                "feature_queue": [
                    {
                        "id": "US-demo-deliverables",
                        "title": "Business-facing demo report",
                        "sprint_id": "sprint-02",
                        "suggested_owner_agent": "documentation-handoff-agent",
                    }
                ],
                "feature_statuses": {"US-demo-deliverables": "done"},
            }
        ),
        encoding="utf-8",
    )
    write_run_events_fixture(
        tmp_path,
        [
            {
                "timestamp": "2026-05-18T04:52:29Z",
                "agent_id": "documentation-handoff-agent",
                "event": "handoff_started",
                "data": {"deployment_status": "deployed"},
            },
            {
                "timestamp": "2026-05-18T04:53:31Z",
                "agent_id": "documentation-handoff-agent",
                "event": "handoff_completed",
                "data": {
                    "artifact": "handoff/project/final/release-report.html",
                    "status": "ready",
                },
            },
        ],
    )

    card = board_groups_for_run(tmp_path)["Sprint 2"][0]

    assert card.owner == "Release Reporter"
    assert card.started_at == "2026-05-18T04:52:29Z"
    assert card.completed_at == "2026-05-18T04:53:31Z"
    assert card.elapsed_label == "1m"


def test_planning_activity_does_not_pull_neighbor_agent_logs():
    card = BoardCard(
        id="PLAN-01",
        title="Requirements brief",
        owner="Business Analyst",
        sprint="Planning",
        status="Done",
        column="done",
        artifact_count=1,
        active=False,
    )

    assert not _log_matches_card(
        "2026-05-18 02:21:32 - Solution Architect\n\nI am reading the business analysis artifacts.",
        card,
    )


def test_user_facing_blockers_hide_provider_payload():
    blockers = user_facing_blockers(
        [
            "Head Agent executor failed: Error code: 400 - "
            "{'error': {'message': 'Unrecognized request argument supplied: reasoning_effort'}}"
        ]
    )

    assert blockers == [
        "Coordinator could not start because the selected model settings were incompatible. "
        "The project can be restarted after updating Settings."
    ]
    assert "reasoning_effort" not in blockers[0]
    assert "Error code" not in blockers[0]


def test_publisher_reports_are_not_attached_to_feature_tasks():
    first = BoardCard(
        id="US-01",
        title="First feature",
        owner="Builder",
        sprint="Sprint 1",
        status="Done",
        column="done",
        artifact_count=1,
        active=False,
        order=1,
    )
    deployment = ArtifactView(
        path="13-deployment-summary.md",
        label="Deployment summary",
        agent="deployment-agent",
        business_agent="Publisher",
        kind="markdown",
        technical=False,
        phase="Sprint 1",
        task_id="US-01",
        task_title="",
    )

    assert _reports_for_card(first, [deployment]) == []


def test_slugged_user_story_quality_report_attaches_to_matching_task():
    card = BoardCard(
        id="US-scrollable-workspace",
        title="Single scrollable board usability",
        owner="Builder",
        sprint="Sprint 2",
        status="Done",
        column="done",
        artifact_count=1,
        active=False,
    )
    artifact = ArtifactView(
        path="08-qa-report-US-scrollable-workspace.md",
        label="Quality summary - US-scrollable-workspace",
        agent="qa-agent",
        business_agent="Quality Reviewer",
        kind="markdown",
        technical=False,
        phase="Sprint 2",
        task_id="US-scrollable-workspace",
        task_title="Single scrollable board usability",
    )

    assert _reports_for_card(card, [artifact]) == [artifact]
