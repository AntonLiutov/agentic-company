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

# ADL status -> board column NAME. Every ADL status gets its own explicit column
# and is always set, so GitHub's "No Status" bucket stays empty (it only appears
# when a status is missing — which, for us, would be a bug).
DEFAULT_ADL_TO_COLUMN = {
    "todo": "Todo",
    "blocked": "Blocked",
    "in_progress": "In Progress",
    "review": "In Review",
    "done": "Done",
}
# With every column ensured present, no status needs a comment fallback.
DEFAULT_ANNOTATE_STATUSES = frozenset()

# Explicit columns ADL ensures on a board, in board order. Edit this tuple to
# reorder the board by code (enforced even if reordered by hand).
DEFAULT_BOARD_COLUMNS = ("Todo", "Blocked", "In Progress", "In Review", "Done")
_COLUMN_COLORS = {
    "Todo": "GRAY",
    "Blocked": "RED",
    "In Progress": "YELLOW",
    "In Review": "PURPLE",
    "Done": "GREEN",
}


# ADL always has a planning sprint; the run's real sprint titles are passed in.
DEFAULT_SPRINTS = ("Planning",)


def _gql_str(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def ensure_sprints(
    gh: GhLike,
    *,
    repository: str,
    sprints: tuple[str, ...] = DEFAULT_SPRINTS,
    descriptions: dict[str, str] | None = None,
) -> dict[str, int]:
    """Ensure the repo has one Milestone per ADL sprint; return ``title -> number``.

    Milestones are how a GitHub board groups / filters / swimlanes by sprint (the
    built-in *Milestone* field on every card). Run once when a GitHub board is
    connected: existing milestones are reused, missing ones created. Idempotent —
    a second call creates nothing. Best-effort by the caller (a milestone failure
    must never break a run).
    """

    descriptions = descriptions or {}
    out = gh.run(
        [
            "api",
            f"repos/{repository}/milestones?state=all&per_page=100",
            "--jq",
            "[.[]|{title,number}]",
        ]
    )
    existing = {m["title"]: int(m["number"]) for m in json.loads(out or "[]")}
    result: dict[str, int] = {}
    for title in sprints:
        if title in existing:
            result[title] = existing[title]
            continue
        args = ["api", f"repos/{repository}/milestones", "-f", f"title={title}", "-f", "state=open"]
        if descriptions.get(title):
            args += ["-f", f"description={descriptions[title]}"]
        created = gh.run(args + ["--jq", ".number"])
        result[title] = int(json.loads(created))
    return result


def ensure_status_columns(
    gh: GhLike,
    *,
    status_field_id: str,
    desired: tuple[str, ...] = DEFAULT_BOARD_COLUMNS,
    remove: tuple[str, ...] = (),
    colors: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Ensure the board's Status field has the desired columns.

    Run once when a GitHub board is connected: queries the current options, adds
    any missing ones, and returns ``(column_name -> option_id, added_columns)``.
    Non-destructive — existing options are re-supplied WITH their ids so their
    option ids (and every card's status) are preserved; only missing columns are
    appended. Idempotent: a second call adds nothing.
    """

    palette = {**_COLUMN_COLORS, **(colors or {})}
    query = "query($f:ID!){node(id:$f){... on ProjectV2SingleSelectField{options{id name color}}}}"
    out = gh.run(["api", "graphql", "-f", f"query={query}", "-F", f"f={status_field_id}"])
    current = json.loads(out)["data"]["node"]["options"]
    have = {o["name"] for o in current}
    missing = [c for c in desired if c not in have]
    # Board order = the order of options: desired columns first, then any custom
    # columns ADL doesn't manage, minus the ones to remove (the redundant default
    # "Todo" — 'todo' lives in the No-Status bucket). Enforced by code.
    target_names = list(desired) + [
        o["name"] for o in current if o["name"] not in desired and o["name"] not in remove
    ]
    current_names = [o["name"] for o in current]
    if current_names == target_names:
        return ({o["name"]: o["id"] for o in current}, [])  # already correct

    by_name = {o["name"]: o for o in current}

    def _opt(name: str) -> str:
        existing = by_name.get(name)
        if existing is not None:  # preserve id + color so cards keep their status
            return (
                f'{{id:"{existing["id"]}", name:"{_gql_str(name)}", '
                f'color:{existing["color"]}, description:""}}'
            )
        return f'{{name:"{_gql_str(name)}", color:{palette.get(name, "GRAY")}, description:""}}'

    # Emit columns in the target order (this is what sets the board column order).
    ordered = [_opt(name) for name in target_names]
    mutation = (
        "mutation($f:ID!){updateProjectV2Field(input:{fieldId:$f,singleSelectOptions:["
        + ",".join(ordered)
        + "]}){projectV2Field{... on ProjectV2SingleSelectField{options{id name}}}}}"
    )
    out = gh.run(["api", "graphql", "-f", f"query={mutation}", "-F", f"f={status_field_id}"])
    opts = json.loads(out)["data"]["updateProjectV2Field"]["projectV2Field"]["options"]
    return ({o["name"]: o["id"] for o in opts}, missing)


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
        item_id = self._project_item_id(work_item_id)
        if not item_id:
            return None
        column = self._map.get(status, "In Progress")
        base = [
            "project",
            "item-edit",
            "--id",
            item_id,
            "--project-id",
            self._project_id,
            "--field-id",
            self._status_field_id,
        ]
        if column == "":
            # 'todo' -> clear the Status so the card sits in the "No Status" bucket.
            self._gh.run(base + ["--clear"])
        else:
            option = self._status_options.get(column)
            if option:
                self._gh.run(base + ["--single-select-option-id", option])
        if status in self._annotate:
            self._issues.post_comment(
                BoardComment(
                    work_item_id,
                    f"ADL status **{status}** (shown under *{column}*).",
                    idempotency_key=f"{work_item_id}:status:{status}",
                )
            )
        return None

    def set_milestone(self, work_item_id: str, milestone_title: str) -> None:
        """Assign the card's issue to a sprint Milestone (board swimlane/group)."""
        self._issues.set_milestone(work_item_id, milestone_title)

    def link_pr(self, work_item_id: str, pr_url: str, pr_id: str = "") -> BoardRef:
        # Native "Linked pull requests": the issues adapter adds a Closes #N
        # reference to the PR body — NOT a separate board card (GitHub automation
        # would drop a duplicate into Todo) and NOT just a comment. The card's
        # built-in field populates and merging the PR closes the issue -> Done.
        return self._issues.link_pr(work_item_id, pr_url, pr_id)

    def _project_item_id(self, work_item_id: str) -> str | None:
        for ref in self._store.list_external_work_refs(
            self._run_id, work_item_id=work_item_id, system=self.system
        ):
            if ref.external_type == "project_item":
                return ref.external_id or None
        return None
