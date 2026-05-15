"""Shared workflow stage names for the delivery loop."""

from __future__ import annotations

from enum import StrEnum


class WorkflowStage(StrEnum):
    """High-level stages shown in the console and used by the delivery graph."""

    PLANNING = "planning"
    # Operator checkpoint after Planning discovers required configuration and before
    # Fullstack, Quality, and Deployment consume environment-bound tools.
    CREDENTIALS = "credentials"
    FULLSTACK = "fullstack"
    QA = "qa"
    DEPLOYMENT = "deployment"
    HANDOFF = "handoff"


def ordered_stages() -> list[WorkflowStage]:
    return [
        WorkflowStage.PLANNING,
        WorkflowStage.CREDENTIALS,
        WorkflowStage.FULLSTACK,
        WorkflowStage.QA,
        WorkflowStage.DEPLOYMENT,
        WorkflowStage.HANDOFF,
    ]
