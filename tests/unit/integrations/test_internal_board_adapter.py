from dataclasses import dataclass

from agentic_company.integrations.board.internal import InternalBoardAdapter
from agentic_company.ports.board import BoardComment, BoardItem


@dataclass
class _Ref:
    id: int


class _FakeStore:
    """Mimics the idempotent upsert_external_work_ref conflict key."""

    def __init__(self) -> None:
        self.rows: dict[tuple, int] = {}
        self.calls = 0

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
        self.calls += 1
        key = (run_id, work_item_id, system, external_type, idempotency_key)
        self.rows.setdefault(key, len(self.rows) + 1)
        return _Ref(id=self.rows[key])


def test_internal_board_records_refs_under_internal_system():
    store = _FakeStore()
    board = InternalBoardAdapter(store, run_id=7)

    issue = board.ensure_item(BoardItem("F1", "Build F1"))
    assert issue.system == "internal"
    assert issue.external_type == "issue"

    pr = board.link_pr("F1", "http://x/pr/1", pr_id="1")
    assert pr.external_url == "http://x/pr/1"
    assert board.set_status("F1", "done") is None


def test_internal_board_comment_is_idempotent_by_event_id():
    store = _FakeStore()
    board = InternalBoardAdapter(store, run_id=7)

    first = board.post_comment(BoardComment("F1", "progress", idempotency_key="evt-1"))
    again = board.post_comment(BoardComment("F1", "progress", idempotency_key="evt-1"))

    # Same event id -> same row, no duplicate (the conflict key collapses them).
    assert first.external_id == again.external_id
    assert len(store.rows) == 1
    assert store.calls == 2
