"""GitHub board adapter — mirrors work items onto GitHub Issues via ``gh``.

Idempotent by construction: every issue/comment/PR is recorded in
``external_work_refs`` keyed by a stable idempotency key, and existing refs are
reused before any ``gh`` call, so a retried or resumed run never creates a
duplicate issue or comment. Raises on a gh failure; the work-mirror is the
best-effort boundary that swallows it so a GitHub outage never breaks a run.
"""

from __future__ import annotations

from typing import Any, Protocol

from agentic_company.integrations.github.cli import GhLike
from agentic_company.ports.board import BoardComment, BoardItem, BoardRef


class BoardRefStore(Protocol):
    """The slice of the console repository the GitHub board adapter needs."""

    def upsert_external_work_ref(self, run_id: int, **kwargs: Any) -> Any: ...

    def list_external_work_refs(
        self, run_id: int, *, work_item_id: str = ..., system: str = ...
    ) -> list[Any]: ...


class GitHubBoardAdapter:
    """Maps ADL work items to GitHub Issues. Token stays host-side via ``gh``."""

    system = "github"

    def __init__(
        self,
        *,
        gh: GhLike,
        store: BoardRefStore,
        run_id: int,
        repository: str,
        connection_id: int | None = None,
    ) -> None:
        self._gh = gh
        self._store = store
        self._run_id = run_id
        self._repo = repository
        self._connection_id = connection_id

    def ensure_item(self, item: BoardItem) -> BoardRef:
        existing = self._ref(item.work_item_id, "issue")
        if existing is not None and existing.external_id:
            return BoardRef(
                item.work_item_id, self.system, "issue", existing.external_id, existing.external_url
            )
        url = self._gh.run(
            [
                "issue",
                "create",
                "--repo",
                self._repo,
                "--title",
                f"[{item.work_item_id}] {item.title}",
                "--body",
                item.body or "_Tracked by Agentic Delivery Lab._",
            ]
        ).strip()
        number = url.rsplit("/", 1)[-1]
        self._persist(item.work_item_id, "issue", f"{item.work_item_id}:issue", number, url)
        return BoardRef(item.work_item_id, self.system, "issue", number, url)

    def post_comment(self, comment: BoardComment) -> BoardRef:
        key = (
            comment.idempotency_key or comment.source_event_id or f"{comment.work_item_id}:comment"
        )
        if self._ref_by_key(comment.work_item_id, "comment", key) is not None:
            return BoardRef(comment.work_item_id, self.system, "comment")  # already posted
        issue = self._ref(comment.work_item_id, "issue")
        if issue is None or not issue.external_id:
            return BoardRef(comment.work_item_id, self.system, "comment")  # no issue yet
        self._gh.run(
            ["issue", "comment", issue.external_id, "--repo", self._repo, "--body", comment.body]
        )
        self._persist(
            comment.work_item_id,
            "comment",
            key,
            issue.external_id,
            issue.external_url,
            source_event_id=comment.source_event_id,
        )
        return BoardRef(comment.work_item_id, self.system, "comment", issue.external_id)

    def set_status(self, work_item_id: str, status: str) -> None:
        issue = self._ref(work_item_id, "issue")
        if issue is None or not issue.external_id:
            return None
        if status == "done":
            self._gh.run(["issue", "close", issue.external_id, "--repo", self._repo])
        else:
            self._gh.run(
                [
                    "issue",
                    "edit",
                    issue.external_id,
                    "--repo",
                    self._repo,
                    "--add-label",
                    f"adl:{status}",
                ]
            )
        return None

    def link_pr(self, work_item_id: str, pr_url: str, pr_id: str = "") -> BoardRef:
        self._persist(work_item_id, "pr", pr_url or f"{work_item_id}:pr", pr_id, pr_url)
        return BoardRef(work_item_id, self.system, "pr", pr_id, pr_url)

    # --- internals ---------------------------------------------------------

    def _persist(
        self,
        work_item_id: str,
        external_type: str,
        idempotency_key: str,
        external_id: str,
        external_url: str,
        *,
        source_event_id: str = "",
    ) -> None:
        self._store.upsert_external_work_ref(
            self._run_id,
            work_item_id=work_item_id,
            system=self.system,
            external_type=external_type,
            idempotency_key=idempotency_key,
            source_event_id=source_event_id,
            external_id=external_id,
            external_url=external_url,
            connection_id=self._connection_id,
            sync_status="synced",
        )

    def _ref(self, work_item_id: str, external_type: str) -> Any | None:
        for ref in self._store.list_external_work_refs(
            self._run_id, work_item_id=work_item_id, system=self.system
        ):
            if ref.external_type == external_type:
                return ref
        return None

    def _ref_by_key(self, work_item_id: str, external_type: str, key: str) -> Any | None:
        for ref in self._store.list_external_work_refs(
            self._run_id, work_item_id=work_item_id, system=self.system
        ):
            if ref.external_type == external_type and ref.idempotency_key == key:
                return ref
        return None
