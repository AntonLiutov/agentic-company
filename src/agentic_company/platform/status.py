"""Canonical work-item status vocabulary and transitions.

Single source of truth for the five board/lane statuses. This replaces the
scattered substring-based classifiers (the ``"blocked" in "unblocked"`` class of
bug, R9) with one token-based classifier and one explicit transition table.

Runtime agents and Codex emit free-form status strings such as
``head_planning_blocked``, ``deployment_deployed`` or
``team_lead_sprint_handoff_ready``. :func:`classify_work_item_status` folds any
such string to one canonical :class:`WorkItemStatus` using *whole-token*
matching, so a signal word appearing inside a larger word (``unblocked``,
``nonblocking``) no longer triggers a false positive the way ``token in text``
substring matching did.

This module is intentionally dependency-free and side-effect-free so the
classification and transition rules can be unit-tested without a database or a
Codex worker.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum


class WorkItemStatus(StrEnum):
    """The canonical board/lane status of a work item."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"


# Whole-token signals, evaluated in strict precedence order: a single status
# string may contain several signals (e.g. ``qa_failed`` has both "qa" and
# "failed"); the first matching bucket below wins.
_BLOCKED_TOKENS = frozenset({"blocked", "failed", "failure", "error", "precondition"})
_DONE_TOKENS = frozenset({"done", "completed", "complete", "passed", "ready", "deployed"})
_REVIEW_TOKENS = frozenset({"qa", "review", "inspect", "implemented"})
_IN_PROGRESS_TOKENS = frozenset({"started", "running", "progress"})
_TODO_TOKENS = frozenset({"todo", "pending", "planned", "backlog"})

# Underscore/space compounds that the token splitter would break apart. These
# are specific enough that a plain substring check is safe (no false-positive
# risk), so we keep them as phrases.
_BLOCKED_PHRASES = (
    "needs_repair",
    "needs repair",
    "provider_limit",
    "usage_limit",
    "usage limit",
    "rate_limit",
    "rate limit",
    "quota",
)

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _tokens(normalized: str) -> frozenset[str]:
    return frozenset(token for token in _TOKEN_SPLIT.split(normalized) if token)


def classify_work_item_status(raw: str | None) -> WorkItemStatus:
    """Fold any runtime/agent status string to one canonical board status.

    Token-based, so ``"unblocked"`` is *not* read as blocked. Precedence is
    blocked > done > review > in_progress > todo; an unknown non-empty status
    defaults to ``IN_PROGRESS`` (a run that reported *something* is underway).
    """

    normalized = (raw or "").strip().lower()
    if not normalized:
        return WorkItemStatus.TODO

    tokens = _tokens(normalized)
    if tokens & _BLOCKED_TOKENS or any(phrase in normalized for phrase in _BLOCKED_PHRASES):
        return WorkItemStatus.BLOCKED
    if tokens & _DONE_TOKENS:
        return WorkItemStatus.DONE
    if tokens & _REVIEW_TOKENS:
        return WorkItemStatus.REVIEW
    if tokens & _IN_PROGRESS_TOKENS:
        return WorkItemStatus.IN_PROGRESS
    if tokens & _TODO_TOKENS:
        return WorkItemStatus.TODO
    return WorkItemStatus.IN_PROGRESS


def lane_for_status(raw: str | None) -> str:
    """Kanban lane for a status. ``review`` items live in the ``qa`` lane."""

    status = classify_work_item_status(raw)
    return "qa" if status is WorkItemStatus.REVIEW else status.value


# Explicit, exhaustive transition table for the canonical statuses (the product
# flow). Forward progress is todo -> in_progress -> review -> done; any active
# lane can drop to blocked; blocked recovers back into work; done is terminal.
# This replaces the write path's historical last-write-wins behaviour with one
# legal-move map, and is the data model the finalizer (0.4) and future
# gate/reconciler logic build on.
#
# NOTE on external boards: these five are ADL's *internal* canonical statuses.
# Mapping to/from a specific board's workflow (Jira custom states, GitHub
# open/closed+labels, Linear, Azure Boards) is the Board adapter's job (P5):
# `classify_work_item_status` is the inbound fold (runtime string -> canonical),
# and a future `to_board_status(canonical, board)` is the outbound mapping. Keep
# this table board-agnostic.
VALID_TRANSITIONS: dict[WorkItemStatus, frozenset[WorkItemStatus]] = {
    WorkItemStatus.TODO: frozenset({WorkItemStatus.IN_PROGRESS, WorkItemStatus.BLOCKED}),
    WorkItemStatus.IN_PROGRESS: frozenset({WorkItemStatus.REVIEW, WorkItemStatus.BLOCKED}),
    WorkItemStatus.REVIEW: frozenset(
        {WorkItemStatus.DONE, WorkItemStatus.IN_PROGRESS, WorkItemStatus.BLOCKED}
    ),
    # A blocker clearing returns the item to work; without this, blocked is a dead end.
    WorkItemStatus.BLOCKED: frozenset({WorkItemStatus.IN_PROGRESS, WorkItemStatus.REVIEW}),
    # Terminal: a finished item does not silently transition again.
    WorkItemStatus.DONE: frozenset(),
}


class InvalidStatusTransition(ValueError):
    """Raised when an illegal work-item status transition is attempted."""

    def __init__(self, source: WorkItemStatus, target: WorkItemStatus) -> None:
        self.source = source
        self.target = target
        super().__init__(f"Invalid work-item status transition: {source.value} -> {target.value}")


def can_transition(source: WorkItemStatus, target: WorkItemStatus) -> bool:
    """Whether moving from ``source`` to ``target`` is allowed (self-moves ok)."""

    if source is target:
        return True
    return target in VALID_TRANSITIONS.get(source, frozenset())


def transition(source: WorkItemStatus, target: WorkItemStatus) -> WorkItemStatus:
    """Return ``target`` if the move is legal, else raise."""

    if not can_transition(source, target):
        raise InvalidStatusTransition(source, target)
    return target


def is_terminal(status: WorkItemStatus) -> bool:
    """Whether a status has no outgoing transitions (only ``done`` today)."""

    return not VALID_TRANSITIONS.get(status, frozenset())


class FailureMode(StrEnum):
    """Machine-readable reason a work item / run is not making progress."""

    PROVIDER_LIMIT = "provider_limit"
    HUMAN_APPROVAL_REQUIRED = "human_approval_required"
    NEEDS_REPAIR = "needs_repair"
    FAILED = "failed"
    BLOCKED = "blocked"


_PROVIDER_LIMIT_PHRASES = (
    "provider_limit",
    "usage_limit",
    "usage limit",
    "rate_limit",
    "rate limit",
    "quota",
    "capacity",
    "purchase more credits",
)


def classify_failure_mode(
    status: str | None, blockers: Iterable[object] = ()
) -> FailureMode | None:
    """Derive a failure mode from a status string (+ optional blocker text).

    ``None`` means "no failure" — a successful or in-flight status. Shares the
    token approach of :func:`classify_work_item_status` so it inherits the same
    substring-safety, and reuses it for the blocked/failed decision so the two
    classifiers can never disagree.
    """

    normalized = (status or "").strip().lower()
    blocker_text = " ".join(str(blocker).lower() for blocker in blockers)
    combined = f"{normalized} {blocker_text}".strip()
    if any(phrase in combined for phrase in _PROVIDER_LIMIT_PHRASES):
        return FailureMode.PROVIDER_LIMIT
    tokens = _tokens(normalized)
    if "human" in tokens or "approval" in tokens:
        return FailureMode.HUMAN_APPROVAL_REQUIRED
    # Match the compound "needs_repair", not a bare "repair" token, so a success
    # like "completed_after_repair" is not misread as needing repair.
    if "needs_repair" in normalized or "qa_failed" in normalized:
        return FailureMode.NEEDS_REPAIR
    item_status = classify_work_item_status(normalized)
    if item_status is WorkItemStatus.BLOCKED:
        return FailureMode.FAILED
    if item_status is WorkItemStatus.DONE:
        return None
    if any(str(blocker).strip() for blocker in blockers):
        return FailureMode.BLOCKED
    return None


__all__ = [
    "WorkItemStatus",
    "FailureMode",
    "VALID_TRANSITIONS",
    "InvalidStatusTransition",
    "classify_work_item_status",
    "classify_failure_mode",
    "lane_for_status",
    "can_transition",
    "transition",
    "is_terminal",
]
