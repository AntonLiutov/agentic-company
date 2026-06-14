"""Select the board adapter for a run.

GitHub when the run/project has an active ``github`` work-system connection with
a repository; otherwise the internal board (the safe default). The DB lookup of
the connection is the caller's job — this keeps selection pure and testable.
"""

from __future__ import annotations

from agentic_company.ports.board import BoardPort


def select_board(
    *,
    store,
    run_id: int,
    system: str = "",
    repository: str = "",
    connection_id: int | None = None,
    gh=None,
) -> BoardPort:
    """Return the board adapter for a run; default to the internal board."""

    if system == "github" and repository.strip():
        from agentic_company.integrations.github.board import GitHubBoardAdapter
        from agentic_company.integrations.github.cli import GhRunner

        return GitHubBoardAdapter(
            gh=gh or GhRunner(),
            store=store,
            run_id=run_id,
            repository=repository.strip(),
            connection_id=connection_id,
        )

    from agentic_company.integrations.board.internal import InternalBoardAdapter

    return InternalBoardAdapter(store, run_id)
