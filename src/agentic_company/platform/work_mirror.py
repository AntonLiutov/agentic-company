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
        self._safe(lambda: self._board.ensure_item(item), f"ensure_item {item.work_item_id}")

    def mirror_comment(self, comment: BoardComment) -> None:
        self._safe(lambda: self._board.post_comment(comment), f"comment {comment.work_item_id}")

    def mirror_status(self, work_item_id: str, status: str) -> None:
        self._safe(lambda: self._board.set_status(work_item_id, status), f"status {work_item_id}")

    def mirror_pr(self, work_item_id: str, pr_url: str, pr_id: str = "") -> None:
        self._safe(lambda: self._board.link_pr(work_item_id, pr_url, pr_id), f"pr {work_item_id}")

    def _safe(self, action, what: str) -> None:
        try:
            action()
        except Exception as exc:  # best-effort: a board outage must not break the run
            LOGGER.warning("External board mirror failed (%s): %s", what, exc)
