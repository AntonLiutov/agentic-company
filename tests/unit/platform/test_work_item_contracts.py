import json

import pytest

from agentic_company.platform.work_item_contracts import (
    pm_sprints_from_run_dir,
    pm_work_items_from_run_dir,
)


def _pm_dir(tmp_path):
    pm_dir = tmp_path / "upstream-planning" / "project-management"
    pm_dir.mkdir(parents=True)
    return pm_dir


def test_pm_work_items_contract_rejects_missing_required_fields(tmp_path):
    pm_dir = _pm_dir(tmp_path)
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


def test_pm_work_items_contract_loads_aliases_and_optional_lists(tmp_path):
    pm_dir = _pm_dir(tmp_path)
    (pm_dir / "planned-work-items.json").write_text(
        json.dumps(
            [
                {
                    "id": "F1",
                    "name": "Build the slice",
                    "sprint_id": "sprint-01",
                    "delivery_order": "7",
                    "owner_agent": "fullstack-agent",
                    "source_refs": "release-plan.json",
                    "acceptance_criteria": ["works"],
                    "definition_of_done": None,
                    "dependencies": {"id": "PLAN-03"},
                    "qa_notes": ("browser", "api"),
                    "deployment_notes": "review app",
                }
            ]
        ),
        encoding="utf-8",
    )

    items = pm_work_items_from_run_dir(tmp_path)

    assert items == [
        {
            "id": "F1",
            "title": "Build the slice",
            "sprint_id": "sprint-01",
            "delivery_order": 7,
            "status": "todo",
            "suggested_owner_agent": "fullstack-agent",
            "source_refs": ["release-plan.json"],
            "acceptance_criteria": ["works"],
            "definition_of_done": [],
            "dependencies": ["{'id': 'PLAN-03'}"],
            "qa_notes": ["browser", "api"],
            "deployment_notes": "review app",
        }
    ]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"items": []}, "must be a JSON array"),
        ([None], "must be an object"),
        ([{}], "missing id"),
        ([], "produced no work items"),
    ],
)
def test_pm_work_items_contract_rejects_invalid_shapes(tmp_path, payload, message):
    pm_dir = _pm_dir(tmp_path)
    (pm_dir / "planned-work-items.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        pm_work_items_from_run_dir(tmp_path)


def test_pm_work_items_contract_rejects_missing_or_invalid_file(tmp_path):
    with pytest.raises(ValueError, match="Missing PM work-item contract artifact"):
        pm_work_items_from_run_dir(tmp_path)

    pm_dir = _pm_dir(tmp_path)
    (pm_dir / "planned-work-items.json").write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid PM work-item contract JSON"):
        pm_work_items_from_run_dir(tmp_path)


def test_pm_sprints_contract_rejects_empty_sprint_list(tmp_path):
    pm_dir = _pm_dir(tmp_path)
    (pm_dir / "release-plan.json").write_text('{"sprints": []}', encoding="utf-8")

    try:
        pm_sprints_from_run_dir(tmp_path)
    except ValueError as exc:
        assert "produced no sprints" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("empty PM sprint contract must fail")


def test_pm_sprints_contract_loads_aliases_and_final_flag(tmp_path):
    pm_dir = _pm_dir(tmp_path)
    (pm_dir / "release-plan.json").write_text(
        json.dumps(
            {
                "sprints": [
                    {
                        "id": "sprint-02",
                        "name": "Release",
                        "delivery_order": "2",
                        "status": "running",
                        "is_final_sprint": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    sprints = pm_sprints_from_run_dir(tmp_path)

    assert sprints == [
        {
            "sprint_id": "sprint-02",
            "title": "Release",
            "delivery_order": 2,
            "status": "running",
            "is_final": True,
            "source_refs": ["upstream-planning/project-management/release-plan.json"],
        }
    ]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "produced no sprints"),
        ({"sprints": {}}, "must include a sprints JSON array"),
        ({"sprints": [None]}, "must be an object"),
        ({"sprints": [{}]}, "missing sprint_id"),
    ],
)
def test_pm_sprints_contract_rejects_invalid_shapes(tmp_path, payload, message):
    pm_dir = _pm_dir(tmp_path)
    (pm_dir / "release-plan.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        pm_sprints_from_run_dir(tmp_path)


def test_pm_sprints_contract_rejects_missing_or_invalid_file(tmp_path):
    with pytest.raises(ValueError, match="Missing PM release-plan contract artifact"):
        pm_sprints_from_run_dir(tmp_path)

    pm_dir = _pm_dir(tmp_path)
    (pm_dir / "release-plan.json").write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid PM release-plan contract JSON"):
        pm_sprints_from_run_dir(tmp_path)
