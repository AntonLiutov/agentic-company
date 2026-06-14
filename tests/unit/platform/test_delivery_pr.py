import agentic_company.platform.delivery_pr as dp
from agentic_company.ports.repo import PullRequest, RepoCapabilities, RepoSpec


class _Item:
    title = "Core task list workflow"


class _Adapter:
    capabilities = RepoCapabilities(
        branch=True, pull_request=True, merge=True, review_comment=True
    )

    def __init__(self):
        self.calls = []

    def ensure_repo(self, spec):
        self.calls.append(("ensure_repo", spec.mode))

    def create_branch(self, target, branch, *, base=""):
        self.calls.append(("branch", branch, base))

    def commit_push(self, target, message, *, branch=""):
        self.calls.append(("commit_push", branch))

    def open_pr(self, target, *, title, body="", base="", head=""):
        self.calls.append(("open_pr", title, head))
        return PullRequest(number="7", url="https://github.com/o/app/pull/7", branch=head)


def _patch(monkeypatch, *, built, item=None):
    monkeypatch.setattr(
        "agentic_company.platform.runtime_db._repo_and_run", lambda uid: (object(), 1)
    )
    monkeypatch.setattr(
        "agentic_company.platform.repo_manager.build_run_repo", lambda *a, **k: built
    )
    monkeypatch.setattr(
        "agentic_company.platform.runtime_db.get_work_item", lambda uid, wid: item or _Item()
    )


def test_should_publish_pr_only_for_code_producers():
    assert dp.should_publish_pr("fullstack-agent")
    assert dp.should_publish_pr("deployment-agent")
    assert not dp.should_publish_pr("qa-agent")
    assert not dp.should_publish_pr("business-analyst-agent")


def test_publish_is_noop_without_repo_host(monkeypatch):
    _patch(monkeypatch, built=None)
    assert dp.publish_work_item_pr("run", "F1") == ""


def test_publish_opens_pr_and_mirrors_on_support_repo(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()  # repo already cloned at run start
    adapter = _Adapter()
    spec = RepoSpec(mode="support", target_dir=tmp_path, repository="o/app", base_branch="main")
    _patch(monkeypatch, built=(adapter, spec))
    mirrored = []
    monkeypatch.setattr(
        "agentic_company.platform.runtime_db._submit_pr_mirror",
        lambda uid, wid, url, pid: mirrored.append((wid, url, pid)),
    )

    url = dp.publish_work_item_pr("run", "F1")

    assert url == "https://github.com/o/app/pull/7"
    assert ("branch", "adl/f1", "main") in adapter.calls
    assert ("commit_push", "adl/f1") in adapter.calls
    assert any(c[0] == "open_pr" for c in adapter.calls)
    assert mirrored == [("F1", "https://github.com/o/app/pull/7", "7")]
    assert not any(c[0] == "ensure_repo" for c in adapter.calls)  # already cloned


def test_publish_skips_support_repo_that_was_not_cloned(tmp_path, monkeypatch):
    adapter = _Adapter()  # no .git -> not cloned at start
    spec = RepoSpec(mode="support", target_dir=tmp_path, repository="o/app")
    _patch(monkeypatch, built=(adapter, spec))
    assert dp.publish_work_item_pr("run", "F1") == ""
    assert adapter.calls == []  # nothing attempted -> graceful skip


def test_ensure_run_repo_clones_support_once(tmp_path, monkeypatch):
    dp.reset_repo_state()
    adapter = _Adapter()
    spec = RepoSpec(mode="support", target_dir=tmp_path / "generated-project", repository="o/app")
    _patch(monkeypatch, built=(adapter, spec))

    dp.ensure_run_repo("run-z")
    dp.ensure_run_repo("run-z")  # cached -> no second clone

    assert sum(1 for c in adapter.calls if c[0] == "ensure_repo") == 1
    dp.reset_repo_state()
