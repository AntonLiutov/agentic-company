"""Unit tests for the canonical work-item status module.

These are pure (no DB, no Codex), so they exercise the classification and
transition rules directly.
"""

from __future__ import annotations

import pytest

from agentic_company.platform.status import (
    FailureMode,
    InvalidStatusTransition,
    WorkItemStatus,
    can_transition,
    classify_failure_mode,
    classify_work_item_status,
    is_terminal,
    lane_for_status,
    transition,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Empty / planning vocabulary.
        ("", WorkItemStatus.TODO),
        (None, WorkItemStatus.TODO),
        ("pending", WorkItemStatus.TODO),
        ("planned", WorkItemStatus.TODO),
        ("backlog", WorkItemStatus.TODO),
        # Real runtime/agent status strings.
        ("head_planning_blocked", WorkItemStatus.BLOCKED),
        ("team_lead_sprint_blocked", WorkItemStatus.BLOCKED),
        ("qa_failed", WorkItemStatus.BLOCKED),
        ("work_item_precondition_failed", WorkItemStatus.BLOCKED),
        ("provider_limit", WorkItemStatus.BLOCKED),
        ("qa_provider_limit", WorkItemStatus.BLOCKED),
        ("needs_repair", WorkItemStatus.BLOCKED),
        ("deployment_deployed", WorkItemStatus.DONE),
        ("team_lead_sprint_handoff_ready", WorkItemStatus.DONE),
        ("head_delivery_completed", WorkItemStatus.DONE),
        ("qa_passed", WorkItemStatus.DONE),
        ("qa", WorkItemStatus.REVIEW),
        ("in_review", WorkItemStatus.REVIEW),
        ("inspection_failed", WorkItemStatus.BLOCKED),  # blocked precedence over review
        ("team_lead_sprint_started", WorkItemStatus.IN_PROGRESS),
        ("running", WorkItemStatus.IN_PROGRESS),
        ("todo", WorkItemStatus.TODO),
    ],
)
def test_classify_known_statuses(raw, expected):
    assert classify_work_item_status(raw) is expected


@pytest.mark.parametrize(
    ("raw", "must_not_be"),
    [
        # The R9 substring bug: "blocked" lives inside "unblocked".
        ("unblocked", WorkItemStatus.BLOCKED),
        ("nonblocking", WorkItemStatus.BLOCKED),
        # "fail" inside an unrelated word must not flip to blocked.
        ("failsafe_enabled", WorkItemStatus.BLOCKED),
    ],
)
def test_classify_no_substring_false_positives(raw, must_not_be):
    assert classify_work_item_status(raw) is not must_not_be


def test_classify_unknown_defaults_to_in_progress():
    assert classify_work_item_status("some_unmapped_status") is WorkItemStatus.IN_PROGRESS


def test_lane_for_status_maps_review_to_qa():
    assert lane_for_status("qa") == "qa"
    assert lane_for_status("head_planning_blocked") == "blocked"
    assert lane_for_status("deployment_deployed") == "done"


def test_workitemstatus_is_a_plain_string():
    # StrEnum members must compare/serialize as their value for DB + JSON paths.
    assert WorkItemStatus.BLOCKED == "blocked"
    assert str(WorkItemStatus.DONE) == "done"


def test_transition_allows_recovery_and_forward():
    assert can_transition(WorkItemStatus.TODO, WorkItemStatus.IN_PROGRESS)
    assert can_transition(WorkItemStatus.IN_PROGRESS, WorkItemStatus.REVIEW)
    assert can_transition(WorkItemStatus.REVIEW, WorkItemStatus.DONE)
    assert can_transition(WorkItemStatus.BLOCKED, WorkItemStatus.IN_PROGRESS)  # recovery
    assert can_transition(WorkItemStatus.BLOCKED, WorkItemStatus.REVIEW)  # recovery
    assert can_transition(WorkItemStatus.TODO, WorkItemStatus.TODO)  # self-move
    assert transition(WorkItemStatus.REVIEW, WorkItemStatus.DONE) is WorkItemStatus.DONE


def test_done_is_terminal():
    assert is_terminal(WorkItemStatus.DONE)
    assert not is_terminal(WorkItemStatus.IN_PROGRESS)
    assert not can_transition(WorkItemStatus.DONE, WorkItemStatus.REVIEW)
    assert not can_transition(WorkItemStatus.DONE, WorkItemStatus.IN_PROGRESS)
    with pytest.raises(InvalidStatusTransition):
        transition(WorkItemStatus.DONE, WorkItemStatus.IN_PROGRESS)


def test_forward_skips_are_rejected():
    # No jumping past the pipeline.
    assert not can_transition(WorkItemStatus.TODO, WorkItemStatus.REVIEW)
    assert not can_transition(WorkItemStatus.TODO, WorkItemStatus.DONE)
    assert not can_transition(WorkItemStatus.IN_PROGRESS, WorkItemStatus.DONE)


@pytest.mark.parametrize(
    ("status", "blockers", "expected"),
    [
        ("qa_provider_limit", (), FailureMode.PROVIDER_LIMIT),
        ("head_planning_blocked", (), FailureMode.FAILED),
        ("qa_failed", (), FailureMode.NEEDS_REPAIR),
        ("needs_human_approval", (), FailureMode.HUMAN_APPROVAL_REQUIRED),
        ("deployment_deployed", (), None),
        ("head_delivery_completed", (), None),
        ("completed_after_repair", (), None),  # "repair" token must not flip a success
        ("project_management_completed_after_repair", (), None),
        ("in_progress", ("waiting on upstream",), FailureMode.BLOCKED),
        ("running", (), None),
    ],
)
def test_classify_failure_mode(status, blockers, expected):
    assert classify_failure_mode(status, blockers) is expected
