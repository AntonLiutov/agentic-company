from types import SimpleNamespace

import pytest

from agentic_company.console.web.product import (
    ArtifactView,
    activity_groups_from_db_events,
    board_cards_from_work_items,
    board_groups_from_work_items,
    canonical_artifacts_for_run,
    delivery_overview_from_work_items,
    task_detail_from_work_items,
)
from agentic_company.platform.artifacts.artifact_registry import artifact_id_for, register_artifact


def test_board_cards_are_materialized_from_db_work_items_only():
    cards = board_cards_from_work_items(
        [
            SimpleNamespace(
                work_item_id="PLAN-01",
                title="Business analysis",
                sprint_id="Planning",
                delivery_order=1,
                status="in_progress",
                lane="in_progress",
                owner_agent="business-analyst-agent",
                active=True,
                artifact_ids=[],
                created_at="2026-05-31T10:00:00Z",
                updated_at="2026-05-31T10:05:00Z",
            ),
            SimpleNamespace(
                work_item_id="US-rooms",
                title="Rooms",
                sprint_id="sprint-01",
                delivery_order=1,
                status="todo",
                lane="todo",
                owner_agent="fullstack-agent",
                active=False,
                artifact_ids=[],
                created_at="2026-05-31T10:00:00Z",
                updated_at="2026-05-31T10:00:00Z",
            ),
        ],
        [],
    )

    assert [card.id for card in cards["in_progress"]] == ["PLAN-01"]
    assert [card.id for card in cards["todo"]] == ["US-rooms"]
    assert cards["in_progress"][0].owner == "Business Analyst"


def test_board_card_counts_only_user_visible_artifacts():
    cards = board_cards_from_work_items(
        [
            SimpleNamespace(
                work_item_id="PLAN-01",
                title="Business analysis",
                sprint_id="Planning",
                delivery_order=1,
                status="done",
                lane="done",
                owner_agent="business-analyst-agent",
                active=False,
                artifact_ids=["internal-prompt", "internal-log", "business-report"],
                created_at="2026-05-31T10:00:00Z",
                updated_at="2026-05-31T10:05:00Z",
            )
        ],
        [
            ArtifactView(
                path="upstream-planning/business-analysis.md",
                label="Business analysis",
                agent="business-analyst-agent",
                business_agent="Business Analyst",
                kind="business_analysis_deliverable",
                technical=False,
                phase="planning",
                task_id="PLAN-01",
                task_title="Business analysis",
                artifact_id="business-report",
                visibility="business",
                artifact_type="business_analysis_deliverable",
            )
        ],
    )

    assert cards["done"][0].artifact_count == 1


def test_open_card_duration_is_capped_when_run_is_not_running():
    cards_by_sprint = board_groups_from_work_items(
        [
            SimpleNamespace(
                work_item_id="F1",
                title="Build app",
                sprint_id="sprint-01",
                delivery_order=1,
                status="in_progress",
                lane="in_progress",
                owner_agent="fullstack-agent",
                active=True,
                artifact_ids=[],
                created_at="2026-06-07T10:00:00Z",
                updated_at="2026-06-07T10:10:00Z",
            )
        ],
        [],
        [
            SimpleNamespace(
                id=1,
                work_item_id="F1",
                owner_agent="fullstack-agent",
                agent_id="fullstack-agent",
                message="Builder started F1.",
                status="in_progress",
                created_at="2026-06-07T10:00:00Z",
            )
        ],
        open_duration_end_at="2026-06-07T10:21:00Z",
    )

    card = cards_by_sprint["Sprint 1"][0]
    assert card.elapsed_label == "21m"
    assert card.completed_at == "2026-06-07T10:21:00Z"


def test_task_detail_requires_exact_db_work_item_id():
    work_items = [
        SimpleNamespace(
            work_item_id="PLAN-01",
            title="Business analysis",
            sprint_id="Planning",
            delivery_order=1,
            status="done",
            lane="done",
            owner_agent="business-analyst-agent",
            active=False,
            artifact_ids=[],
            created_at="2026-05-31T10:00:00Z",
            updated_at="2026-05-31T10:01:00Z",
        )
    ]

    assert task_detail_from_work_items("PLAN-01", work_items, [], []) is not None
    assert task_detail_from_work_items("BA", work_items, [], []) is None


def test_activity_is_task_scoped_and_user_facing_only():
    events = [
        SimpleNamespace(
            work_item_id="US-rooms",
            owner_agent="fullstack-agent",
            message="Rooms are implemented and ready for review.",
            created_at="2026-05-31T10:00:00Z",
        ),
        SimpleNamespace(
            work_item_id="US-contacts",
            owner_agent="fullstack-agent",
            message="Contacts are implemented.",
            created_at="2026-05-31T10:01:00Z",
        ),
    ]

    groups = activity_groups_from_db_events(events, task_id="US-rooms")

    assert len(groups) == 1
    assert groups[0]["owner"] == "Builder"
    assert "Rooms are implemented" in groups[0]["logs"][0]
    assert "2026-05-31 10:00:00 - Builder" in groups[0]["logs"][0]
    assert "2026-05-31T10:00:00Z" not in groups[0]["logs"][0]
    assert "Contacts are implemented" not in groups[0]["logs"][0]


def test_artifact_registry_records_need_explicit_task_metadata(tmp_path):
    report = tmp_path / "qa" / "US-rooms" / "08-qa-report-US-rooms.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Quality summary\n", encoding="utf-8")
    record = register_artifact(
        tmp_path,
        artifact_id=artifact_id_for("run", "qa/US-rooms/08-qa-report-US-rooms.md"),
        relative_path="qa/US-rooms/08-qa-report-US-rooms.md",
        run_id="run",
        owner_agent="qa-agent",
        artifact_type="qa_report",
        visibility="qa_evidence",
        label="Quality summary",
        source_tool="run_qa",
        work_item_id="US-rooms",
    )

    business, technical = canonical_artifacts_for_run(tmp_path, [record])

    assert not technical
    assert business[0].task_id == "US-rooms"
    assert business[0].label == "Quality summary - US-rooms"


def test_planning_contract_artifact_types_are_user_facing(tmp_path):
    report = tmp_path / "upstream-planning" / "business-analysis.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Business analysis\n", encoding="utf-8")
    record = register_artifact(
        tmp_path,
        artifact_id=artifact_id_for("run", "upstream-planning/business-analysis.md"),
        relative_path="upstream-planning/business-analysis.md",
        run_id="run",
        owner_agent="business-analyst-agent",
        artifact_type="business_analysis_deliverable",
        visibility="business",
        label="Business analysis",
        source_tool="run_business_analyst",
        work_item_id="PLAN-01",
    )

    business, technical = canonical_artifacts_for_run(tmp_path, [record])

    assert not technical
    assert business[0].task_id == "PLAN-01"
    assert business[0].artifact_type == "business_analysis_deliverable"


def test_qa_evidence_artifact_type_is_user_facing(tmp_path):
    report = tmp_path / "qa" / "US-rooms" / "results-US-rooms.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"status": "passed"}\n', encoding="utf-8")
    record = register_artifact(
        tmp_path,
        artifact_id=artifact_id_for("run", "qa/US-rooms/results-US-rooms.json"),
        relative_path="qa/US-rooms/results-US-rooms.json",
        run_id="run",
        owner_agent="qa-agent",
        artifact_type="qa_evidence",
        visibility="qa_evidence",
        label="QA evidence",
        source_tool="run_qa",
        work_item_id="US-rooms",
    )

    business, technical = canonical_artifacts_for_run(tmp_path, [record])

    assert not technical
    assert business[0].task_id == "US-rooms"
    assert business[0].artifact_type == "qa_evidence"


def test_fullstack_implementation_artifacts_are_internal_inventory(tmp_path):
    source = tmp_path / "generated-project" / "web" / "app.js"
    source.parent.mkdir(parents=True)
    source.write_text("console.log('app')\n", encoding="utf-8")
    record = register_artifact(
        tmp_path,
        artifact_id=artifact_id_for("run", "generated-project/web/app.js"),
        relative_path="generated-project/web/app.js",
        run_id="run",
        owner_agent="fullstack-agent",
        artifact_type="implementation_artifact",
        visibility="developer",
        label="Implementation artifact: app.js",
        source_tool="codex_exec",
        work_item_id="F1",
    )

    business, technical = canonical_artifacts_for_run(tmp_path, [record])

    assert not business
    assert technical[0].task_id == "F1"
    assert technical[0].artifact_type == "implementation_artifact"


def test_generated_project_artifacts_require_implementation_contract_type(tmp_path):
    source = tmp_path / "generated-project" / "web" / "app.js"
    source.parent.mkdir(parents=True)
    source.write_text("console.log('app')\n", encoding="utf-8")
    record = register_artifact(
        tmp_path,
        artifact_id=artifact_id_for("run", "generated-project/web/app.js"),
        relative_path="generated-project/web/app.js",
        run_id="run",
        owner_agent="fullstack-agent",
        artifact_type="codex_output",
        visibility="developer",
        label="Implementation artifact: app.js",
        source_tool="codex_exec",
        work_item_id="F1",
    )

    business, technical = canonical_artifacts_for_run(tmp_path, [record])

    assert not business
    assert technical[0].task_id == "F1"


def test_artifact_registry_without_work_item_does_not_derive_task_from_path(tmp_path):
    report = tmp_path / "qa" / "US-rooms" / "08-qa-report-US-rooms.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Quality summary\n", encoding="utf-8")
    record = register_artifact(
        tmp_path,
        artifact_id=artifact_id_for("run", "qa/US-rooms/08-qa-report-US-rooms.md"),
        relative_path="qa/US-rooms/08-qa-report-US-rooms.md",
        run_id="run",
        owner_agent="qa-agent",
        artifact_type="qa_report",
        visibility="qa_evidence",
        label="Quality summary",
        source_tool="run_qa",
    )

    business, _ = canonical_artifacts_for_run(tmp_path, [record])

    assert business[0].task_id == ""


def test_delivery_overview_uses_db_work_item_blockers():
    overview = delivery_overview_from_work_items(
        run_id="run-1",
        status="running",
        artifacts=[],
        work_items=[
            SimpleNamespace(
                work_item_id="US-account-delete",
                title="Account deletion",
                sprint_id="sprint-04",
                delivery_order=8,
                status="blocked",
                lane="blocked",
                owner_agent="qa-agent",
                active=False,
                artifact_ids=[],
                blocker="Account deletion failed QA.",
                created_at="2026-05-31T10:00:00Z",
                updated_at="2026-05-31T10:05:00Z",
            )
        ],
    )

    assert overview.stage == "blocked"
    assert overview.active_work_item_id is None
    assert overview.blockers == ["Account deletion failed QA."]


def test_artifact_payload_for_record_rejects_internal_registry_record(tmp_path):
    from agentic_company.console.web.product import artifact_payload_for_record
    from agentic_company.platform.artifacts.artifact_registry import artifact_id_for, register_artifact

    log_path = tmp_path / "generated-project" / "execution.log"
    log_path.parent.mkdir()
    log_path.write_text("secret", encoding="utf-8")
    record = register_artifact(
        tmp_path,
        artifact_id=artifact_id_for("run", "generated-project/execution.log"),
        relative_path="generated-project/execution.log",
        run_id="run",
        owner_agent="fullstack-agent",
        artifact_type="codex_log",
        visibility="internal",
        label="Internal log",
        source_tool="codex_exec",
    )

    with pytest.raises(ValueError):
        artifact_payload_for_record(record, None)
