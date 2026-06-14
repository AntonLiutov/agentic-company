from agentic_company.platform.work_mirror import WorkMirror
from agentic_company.ports.board import BoardComment, BoardItem, BoardRef


class _OkBoard:
    system = "fake"

    def __init__(self):
        self.calls = []

    def ensure_item(self, item):
        self.calls.append(("ensure_item", item.work_item_id))
        return BoardRef(item.work_item_id, self.system, "issue")

    def post_comment(self, comment):
        self.calls.append(("comment", comment.work_item_id))
        return BoardRef(comment.work_item_id, self.system, "comment")

    def set_status(self, work_item_id, status):
        self.calls.append(("status", work_item_id, status))

    def link_pr(self, work_item_id, pr_url, pr_id=""):
        self.calls.append(("pr", work_item_id, pr_url))
        return BoardRef(work_item_id, self.system, "pr")

    def set_milestone(self, work_item_id, milestone_title):
        self.calls.append(("milestone", work_item_id, milestone_title))


class _BrokenBoard:
    system = "broken"

    def ensure_item(self, item):
        raise RuntimeError("github down")

    def post_comment(self, comment):
        raise RuntimeError("github down")

    def set_status(self, work_item_id, status):
        raise RuntimeError("github down")

    def link_pr(self, work_item_id, pr_url, pr_id=""):
        raise RuntimeError("github down")


def test_mirror_forwards_to_board_on_happy_path():
    board = _OkBoard()
    mirror = WorkMirror(board)
    mirror.mirror_item(BoardItem("F1", "Build"))
    mirror.mirror_comment(BoardComment("F1", "progress", idempotency_key="e1"))
    mirror.mirror_status("F1", "done")
    mirror.mirror_pr("F1", "http://x/pr/1")
    assert ("ensure_item", "F1") in board.calls
    assert ("comment", "F1") in board.calls
    assert ("status", "F1", "done") in board.calls
    assert ("pr", "F1", "http://x/pr/1") in board.calls
    mirror.mirror_milestone("F1", "Sprint 1")
    assert ("milestone", "F1", "Sprint 1") in board.calls


def test_mirror_milestone_is_a_noop_when_board_lacks_the_capability():
    class _NoMilestone:
        system = "internal"

    # The internal board has no set_milestone — mirror_milestone must not raise.
    WorkMirror(_NoMilestone()).mirror_milestone("F1", "Sprint 1")


def test_mirror_swallows_board_failures_so_a_run_is_never_broken(caplog):
    mirror = WorkMirror(_BrokenBoard())
    # None of these may raise — a GitHub outage must never break a run.
    mirror.mirror_item(BoardItem("F1", "Build"))
    mirror.mirror_comment(BoardComment("F1", "progress"))
    mirror.mirror_status("F1", "done")
    mirror.mirror_pr("F1", "http://x/pr/1")
    assert "mirror failed" in caplog.text.lower()
