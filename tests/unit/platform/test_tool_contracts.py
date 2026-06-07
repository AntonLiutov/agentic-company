from agentic_company.console.web.db import _normalize_work_item_status
from agentic_company.platform.tool_contracts import (
    dashboard_status_from_runtime_status,
    failure_mode_from_status,
)


def test_completed_after_repair_is_success_not_blocked() -> None:
    assert dashboard_status_from_runtime_status("completed_after_repair") == "done"
    assert (
        dashboard_status_from_runtime_status("project_management_completed_after_repair") == "done"
    )
    assert _normalize_work_item_status("completed_after_repair") == "done"
    assert _normalize_work_item_status("project_management_completed_after_repair") == "done"
    assert failure_mode_from_status("completed_after_repair") is None


def test_needs_repair_remains_blocked() -> None:
    assert dashboard_status_from_runtime_status("needs_repair") == "blocked"
    assert _normalize_work_item_status("needs_repair") == "blocked"
    assert failure_mode_from_status("needs_repair") == "needs_repair"
