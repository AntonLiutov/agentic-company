import json

from agentic_company.console.web.product import (
    ArtifactView,
    BoardCard,
    _activity_groups_for_card,
    _business_log_text,
    _log_matches_card,
    _reports_for_card,
    agent_catalog,
    artifacts_for_run,
    board_groups_for_run,
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


def test_status_label_hides_internal_agent_and_graph_terms():
    assert status_label("head") == "Coordinator"
    assert status_label("head_delivery_completed") == "Delivery Complete"
    assert status_label("business_analysis_completed") == "Requirements Ready"
    assert status_label("project_management_completed") == "Delivery Plan Ready"
    assert status_label("deployment_deployed") == "Published"
    assert status_label("feature_queue_qa_completed_deployment_ready") == "Ready for Publishing"


def test_agent_catalog_uses_role_initials():
    initials = {agent["name"]: agent["initials"] for agent in agent_catalog()}

    assert initials["Coordinator"] == "CO"
    assert initials["Business Analyst"] == "BA"
    assert initials["Solution Architect"] == "SA"
    assert initials["Delivery Planner"] == "DP"
    assert initials["Delivery Lead"] == "DL"
    assert initials["Builder"] == "B"
    assert initials["Quality Reviewer"] == "QR"
    assert initials["Publisher"] == "P"
    assert initials["Release Reporter"] == "RP"


def test_handoff_reports_expose_only_html_release_reports():
    assert is_user_facing_artifact("handoff/sprints/sprint-01/release-report.html")
    assert not is_user_facing_artifact("handoff/sprints/sprint-01/09-handoff-summary.md")
    assert not is_user_facing_artifact("handoff/sprints/sprint-01/release-evidence.json")


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


def test_live_logs_show_agent_start_once_without_workflow_noise(tmp_path):
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                '{"timestamp": "2026-05-18T01:32:41", "agent_id": "delivery-graph", '
                '"event": "delivery_graph_state_written", "data": {"status": "initialized"}}',
                '{"timestamp": "2026-05-18T01:32:43", "agent_id": "head-agent", '
                '"event": "head_worker_started", '
                '"data": {"node": "business_analyst", "target_agent": "business-analyst-agent"}}',
                '{"timestamp": "2026-05-18T01:32:46", "agent_id": "business-analyst-agent", '
                '"event": "business_analysis_codex_started", "data": {"status": "running"}}',
                '{"timestamp": "2026-05-18T01:35:45", "agent_id": "business-analyst-agent", '
                '"event": "business_analysis_codex_completed", "data": {"status": "done"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
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
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        '{"timestamp": "2026-05-18T01:32:43", "agent_id": "business-analyst-agent", '
        '"event": "business_analysis_completed", "data": {"status": "done"}}\n',
        encoding="utf-8",
    )

    groups = work_plan_groups_for_run(tmp_path)

    assert [group["name"] for group in groups].count("Planning") == 1


def test_work_plan_cards_include_elapsed_time(tmp_path):
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                '{"timestamp": "2026-05-18T01:00:00", "agent_id": "business-analyst-agent", '
                '"event": "business_analysis_started", "data": {"status": "started"}}',
                '{"timestamp": "2026-05-18T01:02:05", "agent_id": "business-analyst-agent", '
                '"event": "business_analysis_completed", "data": {"status": "done"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    card = work_plan_groups_for_run(tmp_path)[0]["cards"][0]

    assert card.started_at == "2026-05-18T01:00:00Z"
    assert card.completed_at == "2026-05-18T01:02:05Z"
    assert card.elapsed_label == "2m"


def test_run_timing_uses_open_run_elapsed(tmp_path):
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        '{"timestamp": "2026-05-18T01:00:00", "agent_id": "head-agent", '
        '"event": "head_planning_started", "data": {}}\n',
        encoding="utf-8",
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
    (tmp_path / ".delivery-state.json").write_text(
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

    assert [card.id for card in planning_cards] == ["PLAN-01", "PLAN-02", "PLAN-03"]


def test_planning_card_moves_to_in_progress_after_start_before_report(tmp_path):
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        '{"timestamp": "2026-05-18T01:00:00", "agent_id": "architect-agent", '
        '"event": "architecture_started", "data": {}}\n',
        encoding="utf-8",
    )

    planning_cards = board_groups_for_run(tmp_path)["Planning"]
    architecture_card = next(card for card in planning_cards if card.id == "PLAN-02")

    assert architecture_card.status == "In Progress"
    assert architecture_card.column == "in_progress"
    assert architecture_card.active is True
    assert architecture_card.elapsed_label


def test_planning_card_stays_in_progress_when_report_exists_before_completion(tmp_path):
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        '{"timestamp": "2026-05-18T01:00:00", "agent_id": "architect-agent", '
        '"event": "architecture_started", "data": {}}\n',
        encoding="utf-8",
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
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                '{"timestamp": "2026-05-18T01:00:00", "agent_id": "architect-agent", '
                '"event": "architecture_started", "data": {}}',
                '{"timestamp": "2026-05-18T01:03:00", "agent_id": "architect-agent", '
                '"event": "architecture_completed", "data": {}}',
            ]
        )
        + "\n",
        encoding="utf-8",
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
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                '{"timestamp": "2026-05-18T01:00:00", "agent_id": "architect-agent", '
                '"event": "architecture_started", "data": {}}',
                '{"timestamp": "2026-05-18T01:03:00", "agent_id": "architect-agent", '
                '"event": "architecture_completed", "data": {}}',
            ]
        )
        + "\n",
        encoding="utf-8",
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
    (tmp_path / ".delivery-state.json").write_text(
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
    (tmp_path / "events.jsonl").write_text(
        "\n".join(
            [
                '{"timestamp": "2026-05-18T04:52:29", '
                '"agent_id": "documentation-handoff-agent", '
                '"event": "handoff_started", "data": {"deployment_status": "deployed"}}',
                '{"timestamp": "2026-05-18T04:53:31", '
                '"agent_id": "documentation-handoff-agent", '
                '"event": "handoff_completed", '
                '"data": {"artifact": "handoff/project/final/release-report.html", '
                '"status": "ready"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
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
