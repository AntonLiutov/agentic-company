from agentic_company.integrations.board.internal import InternalBoardAdapter
from agentic_company.integrations.github.board import GitHubBoardAdapter
from agentic_company.platform.board_selection import select_board


class _Store:
    def upsert_external_work_ref(self, *a, **k):
        return None

    def list_external_work_refs(self, *a, **k):
        return []


def test_defaults_to_internal_board_with_no_connection():
    board = select_board(store=_Store(), run_id=7)
    assert isinstance(board, InternalBoardAdapter)
    assert board.system == "internal"


def test_selects_github_when_connection_present():
    board = select_board(
        store=_Store(),
        run_id=7,
        system="github",
        repository="o/app",
        connection_id=3,
        gh=object(),
    )
    assert isinstance(board, GitHubBoardAdapter)
    assert board.system == "github"


def test_falls_back_to_internal_when_github_repository_missing():
    board = select_board(store=_Store(), run_id=7, system="github", repository="  ")
    assert isinstance(board, InternalBoardAdapter)
