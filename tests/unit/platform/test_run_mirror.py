from agentic_company.integrations.github.board import GitHubBoardAdapter
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


class _Conn:
    def __init__(self, repository, metadata, conn_id=1):
        self.repository = repository
        self.metadata = metadata
        self.id = conn_id


class _Repo:
    def __init__(self, conn):
        self._conn = conn
        self.lookups = 0

    def get_run(self, db_run_id):
        return _Run(project_id=10)

    def get_active_work_system_connection(self, **kwargs):
        self.lookups += 1
        return self._conn

    def upsert_external_work_ref(self, *a, **k):
        return None

    def list_external_work_refs(self, *a, **k):
        return []


def test_no_connection_means_no_mirror():
    assert build_run_mirror(_Repo(None), 1, gh=object()) is None


def test_repository_only_builds_an_issues_mirror():
    mirror = build_run_mirror(_Repo(_Conn("o/app", metadata={})), 1, gh=object())
    assert isinstance(mirror, WorkMirror)
    assert isinstance(mirror._board, GitHubBoardAdapter)


def test_cached_project_metadata_builds_a_kanban_mirror_without_touching_gh():
    md = {
        "owner": "o",
        "project_number": 5,
        "project_id": "PVT_x",
        "status_field_id": "F",
        "status_options": {"Todo": "o-todo", "Done": "o-done"},
    }
    # gh=object() would raise if used; cached ids mean no resolve/ensure call.
    mirror = build_run_mirror(_Repo(_Conn("o/app", metadata=md)), 1, gh=object())
    assert isinstance(mirror._board, GitHubProjectsBoardAdapter)


def test_get_run_mirror_caches_per_run():
    reset_run_mirror()
    repo = _Repo(_Conn("o/app", metadata={}))
    first = get_run_mirror(repo, 7, gh=object())
    second = get_run_mirror(repo, 7, gh=object())
    assert first is second
    assert repo.lookups == 1  # built once, then served from cache
    reset_run_mirror(7)
    get_run_mirror(repo, 7, gh=object())
    assert repo.lookups == 2  # reset forces a rebuild
