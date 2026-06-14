"""Provider-neutral board (issue-tracker) port.

A BoardPort mirrors ADL work items onto an external system (GitHub Issues, an
internal board, ...). Every operation is **idempotent** (keyed by a stable
idempotency key, typically a run event id) and **best-effort**: a board outage
must never mutate ADL state, which stays authoritative in Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BoardItem:
    """A work item to surface on an external board."""

    work_item_id: str
    title: str
    body: str = ""
    status: str = ""
    labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BoardComment:
    """An idempotent progress comment on a board item.

    ``idempotency_key`` is normally the run event id, so a retried or resumed run
    re-posting the same event does not create a duplicate comment.
    """

    work_item_id: str
    body: str
    idempotency_key: str = ""
    source_event_id: str = ""


@dataclass(frozen=True, slots=True)
class BoardRef:
    """The external reference produced by a board operation."""

    work_item_id: str
    system: str
    external_type: str  # "issue" | "comment" | "pr"
    external_id: str = ""
    external_url: str = ""


class BoardPort(Protocol):
    """Swappable issue-tracker boundary. All ops idempotent + best-effort."""

    system: str

    def ensure_item(self, item: BoardItem) -> BoardRef:
        """Create or find the board item for a work item; return its ref."""

    def post_comment(self, comment: BoardComment) -> BoardRef:
        """Post a progress comment idempotently; return the comment ref."""

    def set_status(self, work_item_id: str, status: str) -> None:
        """Reflect the work item's status on the board (labels/state)."""

    def link_pr(self, work_item_id: str, pr_url: str, pr_id: str = "") -> BoardRef:
        """Record a pull request linked to the work item; return its ref."""
