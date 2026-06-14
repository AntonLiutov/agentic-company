import json

from agentic_company.integrations.github.projects import GitHubProjectsBoardAdapter
from agentic_company.platform.run_mirror import (
    build_run_mirror,
    get_run_mirror,
    reset_run_mirror,
)
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
