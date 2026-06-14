from dataclasses import dataclass, replace

from agentic_company.integrations.github.projects import GitHubProjectsBoardAdapter
from agentic_company.ports.board import BoardItem


@dataclass
class _Ref:
    external_type: str
    idempotency_key: str
    external_id: str = ""
    external_url: str = ""


class _FakeStore:
    def __init__(self):
        self.refs = {}

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


class _FakeGh:
    def __init__(self):
        self.calls = []

    def run(self, args, *, cwd=None):
        self.calls.append(args)
        if args[:2] == ["issue", "create"]:
            return "https://github.com/o/r/issues/5\n"
        if args[:2] == ["project", "item-add"] and "--format" in args:
            return '{"id":"PVTI_item5"}'
        return ""


STATUS_OPTIONS = {"Todo": "o-todo", "In Progress": "o-prog", "Blocked": "o-blk", "Done": "o-done"}


def _adapter(gh, store):
    return GitHubProjectsBoardAdapter(
        gh=gh,
        store=store,
        run_id=1,
        repository="o/r",
        owner="o",
        project_number=2,
        project_id="PVT_x",
        status_field_id="PVTSSF_x",
        status_options=STATUS_OPTIONS,
    )


def test_ensure_item_creates_issue_and_adds_card_once():
    gh, store = _FakeGh(), _FakeStore()
    board = _adapter(gh, store)
    board.ensure_item(BoardItem("F1", "Build F1"))

    assert any(c[:2] == ["issue", "create"] for c in gh.calls)
    adds = [c for c in gh.calls if c[:2] == ["project", "item-add"] and "--format" in c]
    assert len(adds) == 1  # issue added to the board

    # idempotent: second ensure_item neither re-creates the issue nor re-adds the card
    board.ensure_item(BoardItem("F1", "Build F1"))
    creates = [c for c in gh.calls if c[:2] == ["issue", "create"]]
    adds = [c for c in gh.calls if c[:2] == ["project", "item-add"] and "--format" in c]
    assert len(creates) == 1
    assert len(adds) == 1


def test_set_status_moves_card_to_mapped_column():
    gh, store = _FakeGh(), _FakeStore()
    board = _adapter(gh, store)
    board.ensure_item(BoardItem("F1", "Build F1"))

    board.set_status("F1", "done")
    edits = [c for c in gh.calls if c[:2] == ["project", "item-edit"]]
    assert len(edits) == 1
    assert "o-done" in edits[0]  # Done column option
    assert "PVTI_item5" in edits[0]  # the right card


def test_review_maps_to_in_progress_with_an_annotation_comment():
    gh, store = _FakeGh(), _FakeStore()
    board = _adapter(gh, store)
    board.ensure_item(BoardItem("F1", "Build F1"))

    board.set_status("F1", "review")
    edits = [c for c in gh.calls if c[:2] == ["project", "item-edit"]]
    assert "o-prog" in edits[-1]  # review shares the In Progress column
    comments = [c for c in gh.calls if c[:2] == ["issue", "comment"]]
    assert len(comments) == 1  # the sub-status is annotated on the issue


def test_link_pr_comments_the_pr_on_the_issue_not_a_separate_card():
    gh, store = _FakeGh(), _FakeStore()
    board = _adapter(gh, store)
    board.ensure_item(BoardItem("F1", "Build F1"))

    board.link_pr("F1", "https://github.com/o/r/pull/9", pr_id="9")
    comments = [c for c in gh.calls if c[:2] == ["issue", "comment"]]
    assert any("Pull request:" in " ".join(c) for c in comments)
    # The PR is linked on the issue, NOT added as a separate board card.
    pr_adds = [c for c in gh.calls if c[:2] == ["project", "item-add"] and "pull/9" in " ".join(c)]
    assert len(pr_adds) == 0
