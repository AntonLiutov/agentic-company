import agentic_company.platform.delivery.delivery_pr as dp
from agentic_company.ports.repo import RepoCapabilities, RepoSpec


class _Adapter:
    """Minimal repo adapter for the run-repo-context / clone-at-start tests."""

    capabilities = RepoCapabilities(branch=True, pull_request=True, merge=True, review_comment=True)

    def __init__(self):
        self.calls = []

    def ensure_repo(self, spec):
        self.calls.append(("ensure_repo", spec.mode))


def _patch(monkeypatch, *, built):
    monkeypatch.setattr(
        "agentic_company.platform.db.runtime_db._repo_and_run", lambda uid: (object(), 1)
    )
    monkeypatch.setattr(
        "agentic_company.platform.delivery.repo_manager.build_run_repo", lambda *a, **k: built
    )


def test_run_repo_context_returns_repo_and_base(monkeypatch):
    spec = RepoSpec(mode="support", target_dir=None, repository="o/app", base_branch="main")
    _patch(monkeypatch, built=(_Adapter(), spec))
    assert dp.run_repo_context("run") == {"repository": "o/app", "base_branch": "main"}


def test_run_repo_context_is_none_without_repo_host(monkeypatch):
    _patch(monkeypatch, built=None)
    assert dp.run_repo_context("run") is None


def test_ensure_run_repo_clones_support_once(tmp_path, monkeypatch):
    dp.reset_repo_state()
    adapter = _Adapter()
    spec = RepoSpec(mode="support", target_dir=tmp_path / "generated-project", repository="o/app")
    _patch(monkeypatch, built=(adapter, spec))

    dp.ensure_run_repo("run-z")
    dp.ensure_run_repo("run-z")  # cached -> no second clone

    assert sum(1 for c in adapter.calls if c[0] == "ensure_repo") == 1
    dp.reset_repo_state()
