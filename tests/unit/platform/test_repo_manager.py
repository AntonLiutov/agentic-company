from pathlib import Path

from agentic_company.integrations.github.repo import GitHubRepoAdapter
from agentic_company.platform.repo_manager import _repo_spec, build_run_repo


class _Run:
    def __init__(self, run_dir="/runs/r1", target_project_dir=""):
        self.project_id = 10
        self.run_dir = run_dir
        self.target_project_dir = target_project_dir


class _Conn:
    def __init__(self, repository, metadata=None, default_branch=""):
        self.repository = repository
        self.metadata = metadata or {}
        self.default_branch = default_branch
        self.id = 1


class _Repo:
    def __init__(self, conn):
        self._conn = conn

    def get_run(self, db_run_id):
        return _Run()

    def get_active_work_system_connection(self, **kwargs):
        return self._conn


def test_no_repo_connection_means_local_only():
    assert build_run_repo(_Repo(None), 1, gh=object(), git=object()) is None


def test_builds_github_repo_adapter_and_spec():
    result = build_run_repo(_Repo(_Conn("o/app")), 1, gh=object(), git=object())
    assert result is not None
    adapter, spec = result
    assert isinstance(adapter, GitHubRepoAdapter)
    assert adapter.capabilities.pull_request  # capability advertised
    assert spec.repository == "o/app"
    assert spec.mode == "support"  # default: clone an existing repo
    assert spec.target_dir.name == "generated-project"


def test_repo_spec_honors_new_mode_target_and_branch():
    conn = _Conn("o/app", metadata={"repo_mode": "new"}, default_branch="trunk")
    run = _Run(target_project_dir="/runs/r1/generated-project")
    spec = _repo_spec(run, conn)
    assert spec.mode == "new"
    assert spec.base_branch == "trunk"
    assert str(spec.target_dir) == str(Path("/runs/r1/generated-project"))
