import agentic_company.platform.delivery.delivery_pr as dp
from agentic_company.ports.repo import PullRequest, RepoCapabilities, RepoSpec


class _Item:
    title = "Core task list workflow"


class _Adapter:
    """The git interface used by platform-owned PR publishing."""

    capabilities = RepoCapabilities(branch=True, pull_request=True, merge=True, review_comment=True)

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

    def merge_pr(self, pr):
        self.calls.append(("merge_pr", pr))

    def comment_pr(self, pr, body):
        self.calls.append(("comment_pr", pr, body))

    agent_pr = None  # set to a PullRequest to simulate an agent-opened PR

    def find_pr(self, target, head):
        self.calls.append(("find_pr", head))
        return self.agent_pr


def _patch(monkeypatch, *, built, item=None):
    monkeypatch.setattr(
        "agentic_company.platform.db.runtime_db._repo_and_run", lambda uid: (object(), 1)
    )
    monkeypatch.setattr(
        "agentic_company.platform.delivery.repo_manager.build_run_repo", lambda *a, **k: built
    )
    monkeypatch.setattr(
        "agentic_company.platform.db.runtime_db.get_work_item", lambda uid, wid: item or _Item()
    )


def _patch_pr_store(monkeypatch, tmp_path):
    """Point the PR store at a temp run workspace (no DB needed)."""
    monkeypatch.setattr(dp, "_run_dir", lambda uid: tmp_path)


def test_should_publish_pr_only_for_code_producers():
    assert dp.should_publish_pr("fullstack-agent")
    assert dp.should_publish_pr("deployment-agent")
    assert not dp.should_publish_pr("qa-agent")
    assert not dp.should_publish_pr("business-analyst-agent")


def test_publish_is_noop_without_repo_host(monkeypatch):
    _patch(monkeypatch, built=None)
    assert dp.publish_work_item_pr("run", "F1") == ""


def test_publish_creates_platform_owned_pr(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    adapter = _Adapter()
    spec = RepoSpec(mode="support", target_dir=tmp_path, repository="o/app", base_branch="main")
    _patch(monkeypatch, built=(adapter, spec))
    _patch_pr_store(monkeypatch, tmp_path)
    mirrored = []
    monkeypatch.setattr(
        "agentic_company.platform.db.runtime_db._submit_pr_mirror",
        lambda uid, wid, url, pid: mirrored.append((wid, url, pid)),
    )

    url = dp.publish_work_item_pr("run", "F1")

    assert url == "https://github.com/o/app/pull/7"
    assert ("branch", "adl/f1", "main") in adapter.calls
    assert ("commit_push", "adl/f1") in adapter.calls
    assert ("open_pr", "[F1] Core task list workflow", "adl/f1") in adapter.calls
    assert mirrored == [("F1", "https://github.com/o/app/pull/7", "7")]
    assert dp.get_work_item_pr("run", "F1")["url"] == "https://github.com/o/app/pull/7"


def test_publish_returns_recorded_pr_without_duplicate_write(tmp_path, monkeypatch):
    adapter = _Adapter()
    spec = RepoSpec(mode="support", target_dir=tmp_path, repository="o/app", base_branch="main")
    _patch(monkeypatch, built=(adapter, spec))
    _patch_pr_store(monkeypatch, tmp_path)
    dp.record_work_item_pr("run", "F1", "https://github.com/o/app/pull/7", "7", "adl/f1")

    assert dp.publish_work_item_pr("run", "F1") == "https://github.com/o/app/pull/7"
    assert adapter.calls == []


def test_record_and_get_work_item_pr_round_trip(tmp_path, monkeypatch):
    _patch_pr_store(monkeypatch, tmp_path)

    assert dp.get_work_item_pr("run", "F1") is None
    dp.record_work_item_pr("run", "F1", "https://github.com/o/app/pull/7", "7", "adl/f1")

    pr = dp.get_work_item_pr("run", "F1")
    assert pr["url"] == "https://github.com/o/app/pull/7"
    assert pr["branch"] == "adl/f1"
    assert pr["merged"] is False
    assert (tmp_path / "delivery" / "work-item-prs.json").exists()


def test_mark_work_item_pr_merged(tmp_path, monkeypatch):
    _patch_pr_store(monkeypatch, tmp_path)
    dp.record_work_item_pr("run", "F1", "https://github.com/o/app/pull/7", "7", "adl/f1")

    dp.mark_work_item_pr_merged("run", "F1")

    assert dp.get_work_item_pr("run", "F1")["merged"] is True
    dp.mark_work_item_pr_merged("run", "F2")  # no PR for F2 -> no-op, no raise


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


def test_ensure_recorded_pr_merged_merges_when_pr_recorded(tmp_path, monkeypatch):
    # Platform-owned merge on a QA pass: the recorded PR is merged host-side.
    adapter = _Adapter()
    spec = RepoSpec(mode="support", target_dir=tmp_path, repository="o/app", base_branch="main")
    _patch(monkeypatch, built=(adapter, spec))
    _patch_pr_store(monkeypatch, tmp_path)
    dp.record_work_item_pr("run", "F1", "https://github.com/o/app/pull/7", "7", "adl/f1")

    outcome = dp.ensure_recorded_pr_merged("run", "F1")

    assert outcome.status == "merged"
    assert ("merge_pr", "https://github.com/o/app/pull/7") in adapter.calls
    assert dp.get_work_item_pr("run", "F1")["merged"] is True


def test_ensure_recorded_pr_merged_is_noop_without_a_pr(tmp_path, monkeypatch):
    # The DEPLOY fix: a redeploy that opened no branch has no recorded PR, so there is
    # nothing to merge — `no_pr` is a benign no-op, NEVER a QA/sprint blocker.
    adapter = _Adapter()
    spec = RepoSpec(mode="support", target_dir=tmp_path, repository="o/app", base_branch="main")
    _patch(monkeypatch, built=(adapter, spec))
    _patch_pr_store(monkeypatch, tmp_path)

    outcome = dp.ensure_recorded_pr_merged("run", "DEPLOY")

    assert outcome.status == "no_pr"
    assert not any(c[0] == "merge_pr" for c in adapter.calls)


def test_ensure_recorded_pr_merged_is_idempotent(tmp_path, monkeypatch):
    adapter = _Adapter()
    spec = RepoSpec(mode="support", target_dir=tmp_path, repository="o/app", base_branch="main")
    _patch(monkeypatch, built=(adapter, spec))
    _patch_pr_store(monkeypatch, tmp_path)
    dp.record_work_item_pr("run", "F1", "https://github.com/o/app/pull/7", "7", "adl/f1")
    dp.mark_work_item_pr_merged("run", "F1")

    outcome = dp.ensure_recorded_pr_merged("run", "F1")

    assert outcome.status == "already_merged"
    assert not any(c[0] == "merge_pr" for c in adapter.calls)
