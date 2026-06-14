"""Internal board adapter — the default board when no external system is wired.

With no GitHub/Jira connection configured the run still works end-to-end: this
adapter records each work-item ref under ``system='internal'`` via the existing
idempotent ``upsert_external_work_ref`` store, performing **no external I/O**. It
also keeps the BoardPort honest as a non-GitHub second adapter, so the neutral
DTOs cannot ossify GitHub-shaped.
"""

from __future__ import annotations

from typing import Any, Protocol

from agentic_company.ports.board import BoardComment, BoardItem, BoardRef


class ExternalRefStore(Protocol):
    """The slice of the console repository the board adapters depend on."""

    def upsert_external_work_ref(
        self,
        run_id: int,
        *,
        work_item_id: str,
        system: str,
        external_type: str,
        idempotency_key: str = ...,
        source_event_id: str = ...,
        external_id: str = ...,
        external_url: str = ...,
        connection_id: int | None = ...,
        sync_status: str = ...,
        last_sync_error: str = ...,
    ) -> Any:
        """Idempotently persist an external work ref and return it."""


class InternalBoardAdapter:
    """Records work-item progress on ADL's own Postgres board (no external I/O)."""

    system = "internal"

    def __init__(self, store: ExternalRefStore, run_id: int) -> None:
        self._store = store
        self._run_id = run_id

    def ensure_item(self, item: BoardItem) -> BoardRef:
        ref = self._store.upsert_external_work_ref(
            self._run_id,
            work_item_id=item.work_item_id,
            system=self.system,
            external_type="issue",
            idempotency_key=f"{item.work_item_id}:issue",
            sync_status="synced",
        )
        return BoardRef(item.work_item_id, self.system, "issue", external_id=str(ref.id))

    def post_comment(self, comment: BoardComment) -> BoardRef:
        key = (
            comment.idempotency_key or comment.source_event_id or f"{comment.work_item_id}:comment"
        )
        ref = self._store.upsert_external_work_ref(
            self._run_id,
            work_item_id=comment.work_item_id,
            system=self.system,
            external_type="comment",
            idempotency_key=key,
            source_event_id=comment.source_event_id,
            sync_status="synced",
        )
        return BoardRef(comment.work_item_id, self.system, "comment", external_id=str(ref.id))

    def set_status(self, work_item_id: str, status: str) -> None:
        # The internal board IS work_items.status; nothing external to mirror.
        return None

    def link_pr(self, work_item_id: str, pr_url: str, pr_id: str = "") -> BoardRef:
        self._store.upsert_external_work_ref(
            self._run_id,
            work_item_id=work_item_id,
            system=self.system,
            external_type="pr",
            idempotency_key=pr_url or f"{work_item_id}:pr",
            external_id=pr_id,
            external_url=pr_url,
            sync_status="synced",
        )
        return BoardRef(work_item_id, self.system, "pr", external_id=pr_id, external_url=pr_url)
