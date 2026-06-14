"""GitHub Projects board adapter — issues as cards on a Project, status columns moved.

Prod-grade and host-adaptive. ADL statuses are mapped to the board's OWN columns
via a configurable map, so a board missing a column (GitHub's default board has
no "In Review") degrades gracefully: the card sits in the nearest column and a
comment records the real ADL sub-status. The same shape fits Jira / Azure DevOps
adapters — a different status map + field ids, the same BoardPort.

Issues back the cards (not draft items) because a prod board item must carry the
PR link, comments and detail; the Project card is the kanban *view* of that issue.
"""

from __future__ import annotations

import json

from agentic_company.integrations.github.board import BoardRefStore, GitHubBoardAdapter
from agentic_company.integrations.github.cli import GhLike
from agentic_company.ports.board import BoardComment, BoardItem, BoardRef

# ADL status -> the GitHub Projects column NAME it belongs in. GitHub's default
# board has Todo / In Progress / Blocked / Done (no In Review), so 'review'
# shares the In Progress column and is annotated (see ANNOTATE_STATUSES).
DEFAULT_ADL_TO_COLUMN = {
    "todo": "Todo",
    "in_progress": "In Progress",
    "review": "In Progress",
    "done": "Done",
    "blocked": "Blocked",
}
# ADL statuses without a dedicated column on the default board -> add a comment.
DEFAULT_ANNOTATE_STATUSES = frozenset({"review"})


class GitHubProjectsBoardAdapter:
    """Issues-as-cards on a GitHub Project board, with configurable status columns."""

    system = "github"

    def __init__(
        self,
        *,
        gh: GhLike,
        store: BoardRefStore,
        run_id: int,
        repository: str,
        owner: str,
        project_number: int | str,
        project_id: str,
        status_field_id: str,
        status_options: dict[str, str],
        adl_to_column: dict[str, str] | None = None,
        annotate_statuses: frozenset[str] = DEFAULT_ANNOTATE_STATUSES,
        connection_id: int | None = None,
    ) -> None:
        self._gh = gh
        self._store = store
        self._run_id = run_id
        self._owner = owner
        self._project_number = str(project_number)
        self._project_id = project_id
        self._status_field_id = status_field_id
        self._status_options = dict(status_options)  # column NAME -> option id
        self._map = dict(adl_to_column or DEFAULT_ADL_TO_COLUMN)
        self._annotate = annotate_statuses
        self._connection_id = connection_id
        # Issue create/comment/PR-link reuse the idempotent issues adapter.
        self._issues = GitHubBoardAdapter(
            gh=gh,
            store=store,
            run_id=run_id,
            repository=repository,
            connection_id=connection_id,
        )

    def ensure_item(self, item: BoardItem) -> BoardRef:
        issue = self._issues.ensure_item(item)
        if self._project_item_id(item.work_item_id) is None and issue.external_url:
            out = self._gh.run(
                [
                    "project",
                    "item-add",
                    self._project_number,
                    "--owner",
                    self._owner,
                    "--url",
                    issue.external_url,
                    "--format",
                    "json",
                ]
            )
            item_id = ""
            if out.strip():
                item_id = str(json.loads(out).get("id", ""))
            self._store.upsert_external_work_ref(
                self._run_id,
                work_item_id=item.work_item_id,
                system=self.system,
                external_type="project_item",
                idempotency_key=f"{item.work_item_id}:project_item",
                external_id=item_id,
                external_url=issue.external_url,
                connection_id=self._connection_id,
                sync_status="synced",
            )
        return issue

    def post_comment(self, comment: BoardComment) -> BoardRef:
        return self._issues.post_comment(comment)

    def set_status(self, work_item_id: str, status: str) -> None:
        column = self._map.get(status, "In Progress")
        option = self._status_options.get(column)
        item_id = self._project_item_id(work_item_id)
        if option and item_id:
            self._gh.run(
                [
                    "project",
                    "item-edit",
                    "--id",
                    item_id,
                    "--project-id",
                    self._project_id,
                    "--field-id",
                    self._status_field_id,
                    "--single-select-option-id",
                    option,
                ]
            )
        if status in self._annotate:
            self._issues.post_comment(
                BoardComment(
                    work_item_id,
                    f"ADL status **{status}** (shown under *{column}*).",
                    idempotency_key=f"{work_item_id}:status:{status}",
                )
            )
        return None

    def link_pr(self, work_item_id: str, pr_url: str, pr_id: str = "") -> BoardRef:
        ref = self._issues.link_pr(work_item_id, pr_url, pr_id)
        if pr_url:
            # Link the PR on the work item's issue only — NOT as a separate board
            # card. GitHub's project automation drops every new card into the
            # default Todo column, which would clutter the board with a duplicate
            # for an already-Done item. One card per work item; PRs link to it.
            self._issues.post_comment(
                BoardComment(
                    work_item_id,
                    f"Pull request: {pr_url}",
                    idempotency_key=f"{work_item_id}:prlink:{pr_url}",
                )
            )
        return ref

    def _project_item_id(self, work_item_id: str) -> str | None:
        for ref in self._store.list_external_work_refs(
            self._run_id, work_item_id=work_item_id, system=self.system
        ):
            if ref.external_type == "project_item":
                return ref.external_id or None
        return None
