import json

from agentic_company.platform.work_item_contracts import (
    pm_sprints_from_run_dir,
    pm_work_items_from_run_dir,
)


def test_pm_work_items_contract_rejects_missing_required_fields(tmp_path):
    pm_dir = tmp_path / "upstream-planning" / "project-management"
    pm_dir.mkdir(parents=True)
    (pm_dir / "planned-work-items.json").write_text(
        json.dumps(
            [
                {
                    "id": "US-1",
                    "title": "Feature",
                    "sprint_id": "sprint-01",
                    "delivery_order": 1,
                }
            ]
        ),
        encoding="utf-8",
    )

    try:
        pm_work_items_from_run_dir(tmp_path)
    except ValueError as exc:
        assert "suggested_owner_agent" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid PM work item contract must fail")


def test_pm_sprints_contract_rejects_empty_sprint_list(tmp_path):
    pm_dir = tmp_path / "upstream-planning" / "project-management"
    pm_dir.mkdir(parents=True)
    (pm_dir / "release-plan.json").write_text('{"sprints": []}', encoding="utf-8")

    try:
        pm_sprints_from_run_dir(tmp_path)
    except ValueError as exc:
        assert "produced no sprints" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("empty PM sprint contract must fail")
