"""Best-effort mirror of ADL run progress onto an external board.

ADL Postgres is the source of truth; this mirror runs AFTER the durable DB write
(DB-first) and is **best-effort**: every board call is wrapped, so a GitHub/Jira
outage is logged and swallowed and can never break a run or mutate ADL state.
Idempotency is the board adapter's job (refs keyed by event id), so re-mirroring
a retried/resumed event is safe.
"""

from __future__ import annotations

import logging

from agentic_company.ports.board import BoardComment, BoardItem, BoardPort

LOGGER = logging.getLogger("agentic_company.work_mirror")


class WorkMirror:
    """Wraps a BoardPort so external-sync failures never propagate into a run."""

    def __init__(self, board: BoardPort) -> None:
        self._board = board

    def mirror_item(self, item: BoardItem) -> None:
        self._safe(
            lambda: self._board.ensure_item(item),
            f"card for {item.work_item_id} ({item.title})",
        )

    def mirror_comment(self, comment: BoardComment) -> None:
        self._safe(lambda: self._board.post_comment(comment), f"comment on {comment.work_item_id}")

    def mirror_status(self, work_item_id: str, status: str) -> None:
        self._safe(
            lambda: self._board.set_status(work_item_id, status),
            f"{work_item_id} -> status '{status}'",
        )

    def mirror_pr(self, work_item_id: str, pr_url: str, pr_id: str = "") -> None:
        self._safe(
            lambda: self._board.link_pr(work_item_id, pr_url, pr_id),
            f"{work_item_id} -> PR {pr_url}",
        )

    def mirror_milestone(self, work_item_id: str, milestone_title: str) -> None:
        # Optional capability: only boards that group by sprint (GitHub Milestone)
        # implement set_milestone; the internal board has nothing to mirror.
        setter = getattr(self._board, "set_milestone", None)
        if setter is None or not milestone_title:
            return
        self._safe(
            lambda: setter(work_item_id, milestone_title),
            f"{work_item_id} -> sprint '{milestone_title}'",
        )

    def _safe(self, action, what: str) -> None:
        try:
            action()
        except Exception as exc:  # best-effort: a board outage must not break the run
            LOGGER.warning("Board mirror FAILED (%s): %s", what, exc)
        else:
            LOGGER.info("Board mirror: %s", what)  # visible per-step progress
