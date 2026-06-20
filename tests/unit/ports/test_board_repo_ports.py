from pathlib import Path

from agentic_company.ports.board import BoardComment, BoardItem, BoardPort, BoardRef
from agentic_company.ports.repo import PullRequest, RepoPort, RepoSpec


def test_board_dtos_are_provider_neutral():
    item = BoardItem(
        work_item_id="F1", title="Build", body="b", status="in_progress", labels=("x",)
    )
    comment = BoardComment(work_item_id="F1", body="progress", idempotency_key="evt-1")
    ref = BoardRef(work_item_id="F1", system="github", external_type="issue", external_id="7")
    assert item.work_item_id == "F1"
    assert comment.idempotency_key == "evt-1"
    assert ref.external_type == "issue"
    # No provider-specific field leaks into the neutral DTOs.
    assert set(BoardRef.__dataclass_fields__) == {
        "work_item_id",
        "system",
        "external_type",
        "external_id",
        "external_url",
    }


def test_board_port_protocol_accepts_a_minimal_impl():
    class _Stub:
        system = "internal"

        def ensure_item(self, item: BoardItem) -> BoardRef:
            return BoardRef(item.work_item_id, self.system, "issue")

        def post_comment(self, comment: BoardComment) -> BoardRef:
            return BoardRef(comment.work_item_id, self.system, "comment")

        def set_status(self, work_item_id: str, status: str) -> None:
            return None

        def link_pr(self, work_item_id: str, pr_url: str, pr_id: str = "") -> BoardRef:
            return BoardRef(work_item_id, self.system, "pr", external_url=pr_url)

    board: BoardPort = _Stub()
    assert board.ensure_item(BoardItem("F1", "t")).external_type == "issue"
    assert board.link_pr("F1", "http://x/pr/1").external_url == "http://x/pr/1"


def test_repo_dtos_and_protocol():
    spec = RepoSpec(
        mode="support", target_dir=Path("/tmp/run"), repository="o/r", work_branch="adl/F1"
    )
    assert spec.mode == "support"
    assert spec.private is True

    class _Stub:
        system = "github"

        def ensure_repo(self, spec: RepoSpec) -> None:
            return None

        def create_branch(self, target_dir: Path, branch: str, *, base: str = "") -> None:
            return None

        def commit_push(self, target_dir: Path, message: str, *, branch: str = "") -> None:
            return None

        def open_pr(self, target_dir, *, title, body="", base="", head="") -> PullRequest:
            return PullRequest(number="1", url="http://x/pr/1", branch=head)

    repo: RepoPort = _Stub()
    pr = repo.open_pr(Path("/tmp/run"), title="t", head="adl/F1")
    assert pr.url == "http://x/pr/1"
    assert pr.branch == "adl/F1"
