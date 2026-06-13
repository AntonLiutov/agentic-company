"""Canonical work-item status vocabulary, transitions, and lifecycle events.

Defines the five board statuses, an explicit transition table, a token-based
classifier that folds any free-form runtime status string to one canonical
status, and the standard agent lifecycle events. Dependency-free and
side-effect-free.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum


class WorkItemStatus(StrEnum):
    """Canonical board/lane status of a work item."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"


# Whole-token signals, evaluated in precedence order (first match wins).
_BLOCKED_TOKENS = frozenset({"blocked", "failed", "failure", "error", "precondition"})
_DONE_TOKENS = frozenset({"done", "completed", "complete", "passed", "ready", "deployed"})
_REVIEW_TOKENS = frozenset({"qa", "review", "inspect", "implemented"})
_IN_PROGRESS_TOKENS = frozenset({"started", "running", "progress"})
_TODO_TOKENS = frozenset({"todo", "pending", "planned", "backlog"})

# Compounds the token splitter would break apart; matched as substrings.
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

# Negations that must not read as completion despite carrying a done/ready token
# (e.g. "final_handoff_not_ready" is still in flight, not done).
_NOT_DONE_PHRASES = (
    "not_ready",
    "not ready",
    "not_complete",
    "not complete",
    "not_done",
    "not done",
    "incomplete",
    "unfinished",
)

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _tokens(normalized: str) -> frozenset[str]:
    return frozenset(token for token in _TOKEN_SPLIT.split(normalized) if token)


def classify_work_item_status(raw: str | None) -> WorkItemStatus:
    """Fold any runtime status string to one canonical board status.

    Precedence is blocked > done > review > in_progress > todo; an unknown
    non-empty status defaults to in_progress.
    """

    normalized = (raw or "").strip().lower()
    if not normalized:
        return WorkItemStatus.TODO

    tokens = _tokens(normalized)
    if tokens & _BLOCKED_TOKENS or any(phrase in normalized for phrase in _BLOCKED_PHRASES):
        return WorkItemStatus.BLOCKED
    if tokens & _DONE_TOKENS and not any(phrase in normalized for phrase in _NOT_DONE_PHRASES):
        return WorkItemStatus.DONE
    if tokens & _REVIEW_TOKENS:
        return WorkItemStatus.REVIEW
    if tokens & _IN_PROGRESS_TOKENS:
        return WorkItemStatus.IN_PROGRESS
    if tokens & _TODO_TOKENS:
        return WorkItemStatus.TODO
    return WorkItemStatus.IN_PROGRESS


def lane_for_status(raw: str | None) -> str:
    """Kanban lane for a status. Review items live in the ``qa`` lane."""

    status = classify_work_item_status(raw)
    return "qa" if status is WorkItemStatus.REVIEW else status.value


# Legal transitions per status. Forward: todo -> in_progress -> review -> done.
# Any active lane may drop to blocked; blocked recovers into work; done is terminal.
VALID_TRANSITIONS: dict[WorkItemStatus, frozenset[WorkItemStatus]] = {
    WorkItemStatus.TODO: frozenset({WorkItemStatus.IN_PROGRESS, WorkItemStatus.BLOCKED}),
    WorkItemStatus.IN_PROGRESS: frozenset({WorkItemStatus.REVIEW, WorkItemStatus.BLOCKED}),
    WorkItemStatus.REVIEW: frozenset(
        {WorkItemStatus.DONE, WorkItemStatus.IN_PROGRESS, WorkItemStatus.BLOCKED}
    ),
    WorkItemStatus.BLOCKED: frozenset({WorkItemStatus.IN_PROGRESS, WorkItemStatus.REVIEW}),
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
    """Return ``target`` if the move is legal, else raise :class:`InvalidStatusTransition`."""

    if not can_transition(source, target):
        raise InvalidStatusTransition(source, target)
    return target


def is_terminal(status: WorkItemStatus) -> bool:
    """Whether a status has no outgoing transitions."""

    return not VALID_TRANSITIONS.get(status, frozenset())


class FailureMode(StrEnum):
    """Machine-readable reason a work item is not making progress."""

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
    """Derive a failure mode from a status string and optional blocker text.

    ``None`` means the status is successful or in-flight.
    """

    normalized = (status or "").strip().lower()
    blocker_text = " ".join(str(blocker).lower() for blocker in blockers)
    combined = f"{normalized} {blocker_text}".strip()
    if any(phrase in combined for phrase in _PROVIDER_LIMIT_PHRASES):
        return FailureMode.PROVIDER_LIMIT
    tokens = _tokens(normalized)
    if "human" in tokens or "approval" in tokens:
        return FailureMode.HUMAN_APPROVAL_REQUIRED
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


class AgentEvent(StrEnum):
    """Standard lifecycle event an agent emits; stage and agent are separate fields."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    REPAIR_REQUESTED = "repair_requested"


class CoordinatorOutcome(StrEnum):
    """Orchestration-control signals emitted by coordinator agents.

    Unlike :class:`WorkItemStatus`, these are not board lanes: the head and team
    lead use them to decide when planning, a sprint, or delivery has terminated.
    They share one named vocabulary here so the coordinator class stays uniform
    instead of scattering literals across the orchestration tools.
    """

    PLANNING_BLOCKED = "head_planning_blocked"
    DELIVERY_COMPLETED = "head_delivery_completed"
    SPRINT_STARTED = "team_lead_sprint_started"
    SPRINT_HANDOFF_READY = "team_lead_sprint_handoff_ready"
    SPRINT_BLOCKED = "team_lead_sprint_blocked"
    FINAL_HANDOFF_NOT_READY = "team_lead_final_handoff_not_ready"


# Status values that terminate the head coordinator's orchestration loop.
HEAD_TERMINAL_OUTCOMES = frozenset(
    {
        CoordinatorOutcome.DELIVERY_COMPLETED,
        CoordinatorOutcome.PLANNING_BLOCKED,
    }
)


__all__ = [
    "WorkItemStatus",
    "AgentEvent",
    "CoordinatorOutcome",
    "HEAD_TERMINAL_OUTCOMES",
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
