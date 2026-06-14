import json

import agentic_company.platform.run_mirror as run_mirror_mod
import agentic_company.platform.runtime_db as rdb
from agentic_company.integrations.github.projects import GitHubProjectsBoardAdapter
from agentic_company.platform.run_mirror import (
    build_run_mirror,
    get_run_mirror,
    reset_run_mirror,
)
from agentic_company.platform.runtime_db import _mirror_seed_work_items, _sprint_title
from agentic_company.platform.work_mirror import WorkMirror


class _Run:
    def __init__(self, project_id):
        self.project_id = project_id


class _Project:
    def __init__(self, name):
        self.name = name


class _Conn:
    def __init__(self, repository, metadata, conn_id=1):
        self.repository = repository
        self.metadata = metadata
        self.id = conn_id


class _Repo:
    def __init__(self, conn):
        self._conn = conn
        self.lookups = 0
        self.saved_metadata = None

    def get_run(self, db_run_id):
        return _Run(project_id=10)

    def get_project(self, project_id):
        return _Project(name="My App")

    def get_active_work_system_connection(self, **kwargs):
        self.lookups += 1
        return self._conn

    def update_work_system_connection_metadata(self, connection_id, metadata):
        self.saved_metadata = metadata

    def upsert_external_work_ref(self, *a, **k):
        return None

    def list_external_work_refs(self, *a, **k):
        return []


class _ProvisionGh:
    def __init__(self):
        self.calls = []

    def run(self, args, *, cwd=None):
        self.calls.append(args)
        if args[:2] == ["project", "create"]:
            return json.dumps({"number": 9, "id": "PVT_new", "url": "https://x/9"})
        if args[:2] == ["project", "view"]:
            return json.dumps({"id": "PVT_new"})
        if args[:2] == ["project", "field-list"]:
            return json.dumps(
                {
                    "fields": [
                        {"name": "Status", "id": "F", "options": [{"name": "Todo", "id": "o-todo"}]}
                    ]
                }
            )
        if args[:2] == ["api", "graphql"]:
            query = next((a for a in args if a.startswith("query=")), "")
            if query.startswith("query=query"):
                return json.dumps(
                    {
                        "data": {
                            "node": {"options": [{"id": "o-todo", "name": "Todo", "color": "GRAY"}]}
                        }
                    }
                )
            opts = [{"id": f"o-{n}", "name": n} for n in ("Todo", "Done")]
            return json.dumps(
                {"data": {"updateProjectV2Field": {"projectV2Field": {"options": opts}}}}
            )
        return ""


CACHED_MD = {
    "owner": "o",
    "project_number": "5",
    "project_id": "PVT_x",
    "status_field_id": "F",
    "status_options": {"Todo": "o-todo", "Done": "o-done"},
}


def test_no_connection_means_no_mirror():
    assert build_run_mirror(_Repo(None), 1, gh=object()) is None


def test_cached_project_metadata_builds_a_kanban_mirror_without_touching_gh():
    # gh=object() would raise if used; cached ids mean no resolve/provision call.
    mirror = build_run_mirror(_Repo(_Conn("o/app", metadata=dict(CACHED_MD))), 1, gh=object())
    assert isinstance(mirror._board, GitHubProjectsBoardAdapter)


def test_fresh_github_connection_provisions_a_board_and_caches_it():
    repo = _Repo(_Conn("o/app", metadata={"owner": "o"}))
    gh = _ProvisionGh()
    mirror = build_run_mirror(repo, 1, gh=gh)

    assert isinstance(mirror, WorkMirror)
    assert isinstance(mirror._board, GitHubProjectsBoardAdapter)
    # A new board was created and linked to the repo.
    assert any(c[:2] == ["project", "create"] for c in gh.calls)
    assert ["project", "link", "9", "--owner", "o", "--repo", "o/app"] in gh.calls
    # Its ids were persisted back onto the connection for later runs to reuse.
    assert repo.saved_metadata["project_number"] == "9"
    assert repo.saved_metadata["project_id"] == "PVT_new"


def test_get_run_mirror_caches_per_run():
    reset_run_mirror()
    repo = _Repo(_Conn("o/app", metadata=dict(CACHED_MD)))
    first = get_run_mirror(repo, 7, gh=object())
    second = get_run_mirror(repo, 7, gh=object())
    assert first is second
    assert repo.lookups == 1  # built once, then served from cache
    reset_run_mirror(7)
    get_run_mirror(repo, 7, gh=object())
    assert repo.lookups == 2  # reset forces a rebuild


class _SeedBoard:
    system = "fake"

    def __init__(self):
        self.calls = []

    def ensure_item(self, item):
        self.calls.append(("item", item.work_item_id))

    def post_comment(self, comment):
        pass

    def set_status(self, work_item_id, status):
        self.calls.append(("status", work_item_id, status))

    def link_pr(self, work_item_id, pr_url, pr_id=""):
        pass

    def set_milestone(self, work_item_id, milestone_title):
        self.calls.append(("milestone", work_item_id, milestone_title))


class _SeedItem:
    def __init__(self, wid, sprint):
        self.work_item_id = wid
        self.title = f"Build {wid}"
        self.status = "todo"
        self.sprint_id = sprint
        self.owner_agent = "fullstack-agent"
        self.source_refs = []


class _SeedRepo:
    def list_work_items(self, db_run_id):
        return [_SeedItem("F1", "s1"), _SeedItem("F2", "s1"), _SeedItem("F3", "s2")]


def test_seed_dispatches_every_item_at_once(monkeypatch):
    submitted = []
    monkeypatch.setattr(rdb, "_submit_item_mirror", lambda uid, wid: submitted.append((uid, wid)))

    _mirror_seed_work_items(_SeedRepo(), 1, "run-uid-1")

    # the whole backlog is dispatched in one pass (mirrored in parallel, not 1-by-1)
    assert submitted == [
        ("run-uid-1", "F1"),
        ("run-uid-1", "F2"),
        ("run-uid-1", "F3"),
    ]


def test_mirror_work_item_now_only_emits_what_changed(monkeypatch):
    rdb._MIRROR_CARDED.clear()
    rdb._MIRROR_STATUS.clear()
    rdb._MIRROR_MILESTONED.clear()
    board = _SeedBoard()
    item = _SeedItem("Z1", "s1")  # status 'todo'
    monkeypatch.setattr(rdb, "_repo_and_run", lambda uid: (object(), 1))
    monkeypatch.setattr(run_mirror_mod, "get_run_mirror", lambda repo, dbid, **k: WorkMirror(board))
    monkeypatch.setattr(rdb, "get_work_item", lambda uid, wid: item)
    monkeypatch.setattr(rdb, "_sprint_title", lambda sid: "Sprint 1")

    rdb.mirror_work_item_now("run", "Z1")
    rdb.mirror_work_item_now("run", "Z1")  # nothing changed -> no extra gh calls
    assert [c for c in board.calls if c[0] == "item"] == [("item", "Z1")]
    assert [c for c in board.calls if c[0] == "status"] == [("status", "Z1", "todo")]
    assert [c for c in board.calls if c[0] == "milestone"] == [("milestone", "Z1", "Sprint 1")]

    item.status = "done"
    rdb.mirror_work_item_now("run", "Z1")  # status changed -> exactly one more status call
    assert [c for c in board.calls if c[0] == "status"] == [
        ("status", "Z1", "todo"),
        ("status", "Z1", "done"),
    ]


class _RichItem:
    work_item_id = "F1"
    title = "Core task list workflow"
    status = "in_progress"
    sprint_id = "sprint-01"
    owner_agent = "fullstack-agent"
    source_refs = ["US-01", "AC-01"]


class _CommentBoard:
    system = "fake"

    def __init__(self):
        self.items = []
        self.comments = []

    def ensure_item(self, item):
        self.items.append(item)

    def post_comment(self, comment):
        self.comments.append(comment)

    def set_status(self, *a, **k):
        pass

    def link_pr(self, *a, **k):
        pass


def test_issue_body_is_meaningful_not_a_placeholder():
    body = rdb._issue_body(_RichItem())
    assert "`F1`" in body
    assert "Sprint 1" in body  # sprint label, not raw sprint-01
    assert "Builder" in body  # owner friendly name
    assert "Core task list workflow" in body
    assert "US-01" in body


def test_response_comment_posts_role_message_and_filters_artifacts(monkeypatch):
    rdb._NO_MIRROR_RUNS.discard("run")
    board = _CommentBoard()
    monkeypatch.setattr(rdb, "_repo_and_run", lambda uid: (object(), 1))
    monkeypatch.setattr(run_mirror_mod, "get_run_mirror", lambda repo, dbid, **k: WorkMirror(board))
    monkeypatch.setattr(rdb, "get_work_item", lambda uid, wid: _RichItem())

    rdb.mirror_response_comment_now(
        "run",
        "F1",
        "fullstack-agent",
        "Implemented F1 as a static SPA in browser memory only.",
        ["a.md", "b.png", "c.json", "d.mmd", "e.txt"],
        "msg-1",
    )
    assert len(board.comments) == 1
    comment = board.comments[0]
    assert comment.body.startswith("**Builder**")
    assert "Implemented F1" in comment.body
    assert "a.md" in comment.body and "d.mmd" in comment.body
    assert "b.png" not in comment.body  # png deferred
    assert "c.json" not in comment.body and "e.txt" not in comment.body  # only md/csv/mmd
    assert comment.idempotency_key == "F1:msg:msg-1"
    assert len(board.items) == 1  # the card is ensured before commenting


def test_artifact_section_embeds_content_inline(monkeypatch):
    class _Repo:
        def get_artifact_content(self, db_run_id, aid):
            return {"content_text": "graph TD; A-->B" if aid.endswith(".mmd") else "# Notes\nbody"}

    monkeypatch.setattr(
        "agentic_company.platform.artifact_registry.artifact_id_for",
        lambda run, path: path,
    )
    section = rdb._artifact_section(
        _Repo(), 1, "run", ["diagram.mmd", "notes.md", "data.csv", "shot.png", "x.json"]
    )
    assert "```mermaid" in section and "graph TD" in section  # mermaid renders natively
    assert "<details>" in section and "notes.md" in section  # md collapsed
    assert "```csv" in section  # csv as a code block
    assert "shot.png" not in section  # png deferred
    assert "x.json" not in section  # json excluded


def test_sprint_title_matches_adl_board_labels():
    # mirrors ADL's own board labels via sprint_label, NOT the descriptive title
    assert _sprint_title("sprint-01") == "Sprint 1"
    assert _sprint_title("sprint-2") == "Sprint 2"
    assert _sprint_title("planning") == "Planning"
    assert _sprint_title("") == ""
