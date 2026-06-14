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


def test_gh_runner_raises_when_gh_missing():
    runner = GhRunner(gh_binary="definitely-not-a-real-binary-xyz")
    with pytest.raises(GhError):
        runner.run(["issue", "list"])
