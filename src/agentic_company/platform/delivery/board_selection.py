"""Select the board adapter for a run.

GitHub when the run/project has an active ``github`` work-system connection with
a repository; the Projects (kanban) board when a project number + resolved field
ids are supplied, otherwise the issues-only GitHub board, otherwise the internal
board (the safe default). Selection is pure: the DB lookup of the connection and
the GitHub Projects field resolution are the caller's job, so this stays testable
without any I/O.
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
    owner: str = "",
    project_number: int | str = "",
    project_id: str = "",
    status_field_id: str = "",
    status_options: dict[str, str] | None = None,
) -> BoardPort:
    """Return the board adapter for a run; default to the internal board."""

    if system == "github" and repository.strip():
        from agentic_company.integrations.github.cli import GhRunner

        gh = gh or GhRunner()
        # A fully-resolved Projects board (kanban) takes precedence over issues.
        if owner.strip() and str(project_number).strip() and project_id and status_field_id:
            from agentic_company.integrations.github.projects import GitHubProjectsBoardAdapter

            return GitHubProjectsBoardAdapter(
                gh=gh,
                store=store,
                run_id=run_id,
                repository=repository.strip(),
                owner=owner.strip(),
                project_number=project_number,
                project_id=project_id,
                status_field_id=status_field_id,
                status_options=status_options or {},
                connection_id=connection_id,
            )

        from agentic_company.integrations.github.board import GitHubBoardAdapter

        return GitHubBoardAdapter(
            gh=gh,
            store=store,
            run_id=run_id,
            repository=repository.strip(),
            connection_id=connection_id,
        )

    from agentic_company.integrations.board.internal import InternalBoardAdapter

    return InternalBoardAdapter(store, run_id)
