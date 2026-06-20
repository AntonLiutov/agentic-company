"""Deterministic run finalization.

A delivery run terminates in exactly one canonical :class:`RunStatus`, derived
from its final delivery state by :func:`resolve_run_status`. The function is pure
and side-effect-free so the run's outcome is decided in one place rather than by
whichever code path happens to write the runs row last.
"""

from __future__ import annotations

from enum import StrEnum

from agentic_company.platform.db.state import DeliveryState
from agentic_company.platform.status.status import WorkItemStatus, classify_work_item_status


class RunStatus(StrEnum):
    """Canonical lifecycle status of a delivery run.

    Distinct from :class:`~agentic_company.platform.status.status.WorkItemStatus`: a run
    is an execution, not a board lane, so it carries execution outcomes such as
    ``running`` and ``stopped`` rather than ``todo``/``review``.
    """

    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    STOPPED = "stopped"
    FAILED_TO_START = "failed_to_start"


# Statuses past which a run's outcome is settled; the first one written wins.
TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.BLOCKED,
        RunStatus.FAILED,
        RunStatus.STOPPED,
        RunStatus.FAILED_TO_START,
    }
)


def resolve_run_status(state: DeliveryState) -> RunStatus:
    """Derive a run's terminal status from its final delivery state.

    A stopped run stays stopped; any blocker or blocked outcome yields
    ``BLOCKED``; a done outcome yields ``COMPLETED``; and any other state at
    finalization is treated as ``FAILED`` so an incomplete run never reports
    success.

    This folds the in-memory delivery state only. Reconciling against the
    persisted world (sprints, work items, stop intent) is a later step.
    """

    raw = str(state.get("status") or "")
    if raw == RunStatus.STOPPED:
        return RunStatus.STOPPED
    if state.get("blockers"):
        return RunStatus.BLOCKED
    canonical = classify_work_item_status(raw)
    if canonical is WorkItemStatus.DONE:
        return RunStatus.COMPLETED
    if canonical is WorkItemStatus.BLOCKED:
        return RunStatus.BLOCKED
    return RunStatus.FAILED


def is_terminal_run_status(status: str) -> bool:
    """Return whether a run status string is a settled terminal outcome."""

    return status in TERMINAL_RUN_STATUSES


__all__ = [
    "RunStatus",
    "TERMINAL_RUN_STATUSES",
    "resolve_run_status",
    "is_terminal_run_status",
]
