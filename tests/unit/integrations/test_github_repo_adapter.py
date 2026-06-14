from pathlib import Path

import pytest

from agentic_company.integrations.github.repo import GitError, GitHubRepoAdapter, GitRunner
from agentic_company.ports.repo import RepoSpec


class _FakeGh:
    def __init__(self, outputs=None):
        self.calls = []
        self._outputs = outputs or {}

    def run(self, args, *, cwd=None):
        self.calls.append(args)
        return self._outputs.get(args[0] + ":" + args[1], "")


class _FakeGit:
    def __init__(self):
        self.calls = []

    def run(self, args, *, cwd):
        self.calls.append((args, str(cwd)))
        return ""


def test_ensure_repo_new_inits_commits_and_creates_remote(tmp_path: Path):
    gh, git = _FakeGh(), _FakeGit()
    adapter = GitHubRepoAdapter(gh=gh, git=git)
    adapter.ensure_repo(RepoSpec(mode="new", target_dir=tmp_path, repository="o/app", private=True))

    git_cmds = [c[0][:1] for c in git.calls]
    assert ["init"] in git_cmds
    assert ["add"] in git_cmds
    assert ["commit"] in git_cmds
    create = [c for c in gh.calls if c[:2] == ["repo", "create"]]
    assert len(create) == 1
    assert "--push" in create[0]
    assert "--private" in create[0]


def test_ensure_repo_support_clones(tmp_path: Path):
    gh, git = _FakeGh(), _FakeGit()
    adapter = GitHubRepoAdapter(gh=gh, git=git)
    adapter.ensure_repo(RepoSpec(mode="support", target_dir=tmp_path, repository="o/existing"))

    assert ["repo", "clone", "o/existing", str(tmp_path)] in gh.calls
    assert git.calls == []  # support clones, does not init


def test_create_branch_and_commit_push(tmp_path: Path):
    gh, git = _FakeGh(), _FakeGit()
    adapter = GitHubRepoAdapter(gh=gh, git=git)
    adapter.create_branch(tmp_path, "adl/F1")
    adapter.commit_push(tmp_path, "feat: F1", branch="adl/F1")

    cmds = [c[0] for c in git.calls]
    assert ["checkout", "-b", "adl/F1"] in cmds
    assert ["push", "-u", "origin", "adl/F1"] in cmds


def test_open_pr_parses_number_and_url(tmp_path: Path):
    gh = _FakeGh({"pr:create": "https://github.com/o/app/pull/42\n"})
    adapter = GitHubRepoAdapter(gh=gh, git=_FakeGit())
    pr = adapter.open_pr(tmp_path, title="F1", base="main", head="adl/F1")
    assert pr.number == "42"
    assert pr.url == "https://github.com/o/app/pull/42"
    assert pr.branch == "adl/F1"


class _DiffGit:
    def __init__(self, staged):
        self.calls = []
        self._staged = staged

    def run(self, args, *, cwd):
        self.calls.append(args)
        if args[:3] == ["diff", "--cached", "--name-only"]:
            return self._staged
        return ""


def test_commit_push_writes_gitignore_and_unstages_secrets(tmp_path: Path):
    git = _DiffGit(
        ".env\nsrc/app.py\nconfig.key\nagent-runtime.env\nREADME.md\nsecrets/token.txt"
    )
    adapter = GitHubRepoAdapter(gh=_FakeGh(), git=git)
    adapter.commit_push(tmp_path, "feat: F1", branch="adl/F1")

    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert gitignore.count("ADL secrets safety") == 1  # secrets block written once
    removed = {c[-1] for c in git.calls if c[:2] == ["rm", "--cached"]}
    assert {".env", "config.key", "agent-runtime.env", "secrets/token.txt"} <= removed
    assert "src/app.py" not in removed and "README.md" not in removed  # product files kept


def test_secrets_gitignore_is_idempotent(tmp_path: Path):
    adapter = GitHubRepoAdapter(gh=_FakeGh(), git=_DiffGit(""))
    adapter._write_secrets_gitignore(tmp_path)
    adapter._write_secrets_gitignore(tmp_path)
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").count("ADL secrets safety") == 1


def test_capabilities_and_pr_review_actions():
    gh, git = _FakeGh(), _FakeGit()
    adapter = GitHubRepoAdapter(gh=gh, git=git)

    assert adapter.capabilities.pull_request and adapter.capabilities.merge
    assert adapter.capabilities.review_comment

    adapter.comment_pr("https://github.com/o/app/pull/9", "Please fix the empty state")
    adapter.merge_pr("https://github.com/o/app/pull/9")
    assert ["pr", "comment", "https://github.com/o/app/pull/9", "--body",
            "Please fix the empty state"] in gh.calls
    assert any(c[:2] == ["pr", "merge"] and "--squash" in c for c in gh.calls)

    adapter.comment_pr("", "x")  # empty pr -> no-op
    adapter.merge_pr("")  # empty pr -> no-op
    assert sum(1 for c in gh.calls if c[:2] == ["pr", "comment"]) == 1


def test_git_runner_raises_when_git_missing(tmp_path: Path):
    runner = GitRunner(git_binary="definitely-not-a-real-git-xyz")
    with pytest.raises(GitError):
        runner.run(["status"], cwd=tmp_path)
