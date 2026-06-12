"""Unit tests for tool-aware work-item transition rules."""

from __future__ import annotations

import pytest

from agentic_company.platform.runtime_db import _effective_transition_status


def _effective(current, requested, *, tool_name, owner_agent, raw=None):
    return _effective_transition_status(
        current_status=current,
        raw_requested_status=raw if raw is not None else requested,
        requested_status=requested,
        owner_agent=owner_agent,
        tool_name=tool_name,
    )


@pytest.mark.parametrize(
    ("current", "requested", "tool", "owner", "expected"),
    [
        # Normal build path.
        ("todo", "in_progress", "run_fullstack", "fullstack-agent", "in_progress"),
        ("in_progress", "review", "codex_exec", "fullstack-agent", "review"),
        # QA closes a feature straight from in_progress/review.
        ("in_progress", "done", "run_qa", "qa-agent", "done"),
        ("review", "done", "run_post_deploy_qa", "qa-agent", "done"),
        # Deployment finishes into review (awaiting post-deploy QA).
        ("in_progress", "deployment_deployed", "codex_exec", "deployment-agent", "review"),
        # A per-sprint handoff is not terminal: the coordination card stays in review.
        ("in_progress", "done", "run_handoff", "documentation-handoff-agent", "review"),
        ("review", "done", "run_handoff", "documentation-handoff-agent", "review"),
        # complete_sprint reopens for the next sprint (review -> in_progress is legal).
        ("review", "in_progress", "complete_sprint", "team-lead-agent", "in_progress"),
        # The final sprint closes the coordination card legally (review -> done).
        ("review", "done", "complete_sprint", "team-lead-agent", "done"),
        # Inspections never move a done card.
        ("done", "in_progress", "inspect_sprint_status", "team-lead-agent", "done"),
        # An illegal reopen of a terminal card is clamped, not persisted.
        ("done", "in_progress", "complete_sprint", "team-lead-agent", "done"),
    ],
)
def test_effective_transition_status(current, requested, tool, owner, expected):
    assert _effective(current, requested, tool_name=tool, owner_agent=owner) == expected


def test_handoff_never_marks_coordination_card_done():
    # The exact bug from run project-20260612-202807: run_handoff moved PLAN-04
    # in_progress -> done at sprint end, then complete_sprint reopened it.
    assert (
        _effective(
            "in_progress",
            "done",
            tool_name="run_handoff",
            owner_agent="documentation-handoff-agent",
        )
        == "review"
    )
