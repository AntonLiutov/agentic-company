from dataclasses import dataclass, replace

import pytest

from agentic_company.integrations.github.board import GitHubBoardAdapter
from agentic_company.integrations.github.cli import GhError, GhRunner
from agentic_company.ports.board import BoardComment, BoardItem


@dataclass
class _Ref:
    external_type: str
    idempotency_key: str
    external_id: str = ""
    external_url: str = ""


class _FakeGh:
    def __init__(self, outputs: dict[str, str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self._outputs = outputs or {}

    def run(self, args: list[str]) -> str:
        self.calls.append(args)
        return self._outputs.get(args[0] + ":" + args[1], "")


class _FakeStore:
    def __init__(self) -> None:
        self.refs: dict[tuple, _Ref] = {}

    def upsert_external_work_ref(
        self,
        run_id,
        *,
        work_item_id,
        system,
        external_type,
        idempotency_key="",
        source_event_id="",
        external_id="",
        external_url="",
        connection_id=None,
        sync_status="pending",
        last_sync_error="",
    ):
        key = (run_id, work_item_id, system, external_type, idempotency_key)
        self.refs[key] = _Ref(external_type, idempotency_key, external_id, external_url)
        return replace(self.refs[key])

    def list_external_work_refs(self, run_id, *, work_item_id="", system=""):
        return [
            r
            for (rid, wid, sysn, _t, _k), r in self.refs.items()
            if rid == run_id
            and (not work_item_id or wid == work_item_id)
            and (not system or sysn == system)
        ]


def _adapter(gh, store):
    return GitHubBoardAdapter(gh=gh, store=store, run_id=7, repository="o/r", connection_id=3)


def test_ensure_item_creates_issue_then_reuses_it():
    gh = _FakeGh({"issue:create": "https://github.com/o/r/issues/12\n"})
    store = _FakeStore()
    board = _adapter(gh, store)

    first = board.ensure_item(BoardItem("F1", "Build F1"))
    assert first.external_id == "12"
    assert first.external_url == "https://github.com/o/r/issues/12"
    creates = [c for c in gh.calls if c[:2] == ["issue", "create"]]
    assert len(creates) == 1

    # Idempotent: a second ensure_item reuses the stored issue, no new gh create.
    again = board.ensure_item(BoardItem("F1", "Build F1"))
    assert again.external_id == "12"
    creates = [c for c in gh.calls if c[:2] == ["issue", "create"]]
    assert len(creates) == 1


def test_post_comment_is_idempotent_by_event_id():
    gh = _FakeGh({"issue:create": "https://github.com/o/r/issues/12\n"})
    store = _FakeStore()
    board = _adapter(gh, store)
    board.ensure_item(BoardItem("F1", "Build F1"))

    board.post_comment(BoardComment("F1", "started", idempotency_key="evt-1"))
    board.post_comment(BoardComment("F1", "started", idempotency_key="evt-1"))

    comments = [c for c in gh.calls if c[:2] == ["issue", "comment"]]
    assert len(comments) == 1  # same event id -> posted once


def test_set_status_done_closes_issue():
    gh = _FakeGh({"issue:create": "https://github.com/o/r/issues/12\n"})
    store = _FakeStore()
    board = _adapter(gh, store)
    board.ensure_item(BoardItem("F1", "Build F1"))

    board.set_status("F1", "done")
    assert ["issue", "close", "12", "--repo", "o/r"] in gh.calls


def test_link_pr_adds_closes_reference_to_pr_body():
    gh = _FakeGh(
        {
            "issue:create": "https://github.com/o/r/issues/12\n",
            "pr:view": "Opened by Agentic Delivery Lab.",
        }
    )
    store = _FakeStore()
    board = _adapter(gh, store)
    board.ensure_item(BoardItem("F1", "Build F1"))

    board.link_pr("F1", "https://github.com/o/r/pull/20", pr_id="20")
    edits = [c for c in gh.calls if c[:2] == ["pr", "edit"]]
    assert len(edits) == 1
    assert "Closes #12" in edits[0][-1]  # native "Linked pull requests"


def test_link_pr_is_idempotent_when_issue_already_referenced():
    gh = _FakeGh(
        {
            "issue:create": "https://github.com/o/r/issues/12\n",
            "pr:view": "Work for the feature. Closes #12",
        }
    )
    store = _FakeStore()
    board = _adapter(gh, store)
    board.ensure_item(BoardItem("F1", "Build F1"))

    board.link_pr("F1", "https://github.com/o/r/pull/20", pr_id="20")
    edits = [c for c in gh.calls if c[:2] == ["pr", "edit"]]
    assert edits == []  # already linked -> no body rewrite

    # #1 must not match the existing "#12" reference (word boundary).
    gh2 = _FakeGh(
        {"issue:create": "https://github.com/o/r/issues/1\n", "pr:view": "Closes #12"}
    )
    board2 = _adapter(gh2, _FakeStore())
    board2.ensure_item(BoardItem("F2", "Build F2"))
    board2.link_pr("F2", "https://github.com/o/r/pull/21", pr_id="21")
    assert any(c[:2] == ["pr", "edit"] for c in gh2.calls)  # #1 != #12 -> still links


def test_set_milestone_ensures_then_assigns_and_caches_the_milestone():
    gh = _FakeGh(
        {
            "issue:create": "https://github.com/o/r/issues/12\n",
            "api:repos/o/r/milestones?state=all&per_page=100": "[]",
            "api:repos/o/r/milestones": "1",  # created milestone number
        }
    )
    store = _FakeStore()
    board = _adapter(gh, store)
    board.ensure_item(BoardItem("F1", "Build F1"))

    board.set_milestone("F1", "Sprint 1")
    edits = [c for c in gh.calls if c[:2] == ["issue", "edit"]]
    assert ["issue", "edit", "12", "--repo", "o/r", "--milestone", "Sprint 1"] in edits
    creates = [c for c in gh.calls if c[:2] == ["api", "repos/o/r/milestones"] and "-f" in c]
    assert len(creates) == 1  # the milestone was ensured once

    board.set_milestone("F1", "Sprint 1")  # same sprint -> cached, no re-ensure
    creates = [c for c in gh.calls if c[:2] == ["api", "repos/o/r/milestones"] and "-f" in c]
    assert len(creates) == 1

    board.set_milestone("F1", "")  # empty title -> no-op
    edits = [c for c in gh.calls if c[:2] == ["issue", "edit"]]
    assert len(edits) == 2  # two Sprint 1 assignments; the empty one was skipped


def test_gh_runner_raises_when_gh_missing():
    runner = GhRunner(gh_binary="definitely-not-a-real-binary-xyz")
    with pytest.raises(GhError):
        runner.run(["issue", "list"])
