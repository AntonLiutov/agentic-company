from agentic_company.console.web.app import (
    _maybe_create_board_connection,
    new_project_form_values,
    normalize_board_adapter,
)


class _FakeRepo:
    def __init__(self, fail=False):
        self.calls = []
        self._fail = fail

    def create_work_system_connection(self, **kwargs):
        if self._fail:
            raise RuntimeError("db down")
        self.calls.append(kwargs)
        return 1


def test_normalize_board_adapter_defaults_to_internal():
    assert normalize_board_adapter("github") == "github"
    assert normalize_board_adapter("internal") == "internal"
    assert normalize_board_adapter("jira") == "internal"
    assert normalize_board_adapter("") == "internal"


def test_form_values_carry_and_clean_board_fields():
    values = new_project_form_values(
        board_adapter="github", repository=" o/app ", project_number=" 5 "
    )
    assert values["board_adapter"] == "github"
    assert values["repository"] == "o/app"
    assert values["project_number"] == "5"


def test_internal_board_creates_no_connection():
    repo = _FakeRepo()
    _maybe_create_board_connection(
        repo,
        project_id=1,
        board_adapter="internal",
        repository="o/app",
        project_owner="",
        project_number="",
    )
    assert repo.calls == []


def test_github_board_creates_connection_with_derived_owner_and_metadata():
    repo = _FakeRepo()
    _maybe_create_board_connection(
        repo,
        project_id=7,
        board_adapter="github",
        repository="AntonLiutov/app",
        project_owner="",  # derived from the repository owner
        project_number="5",
    )
    assert len(repo.calls) == 1
    call = repo.calls[0]
    assert call["project_id"] == 7
    assert call["system"] == "github"
    assert call["repository"] == "AntonLiutov/app"
    assert call["metadata"] == {"owner": "AntonLiutov", "project_number": "5"}


def test_github_board_without_repository_creates_nothing():
    repo = _FakeRepo()
    _maybe_create_board_connection(
        repo,
        project_id=1,
        board_adapter="github",
        repository="  ",
        project_owner="",
        project_number="",
    )
    assert repo.calls == []


def test_connection_failure_is_swallowed():
    # A DB error must not break project creation (run falls back to internal board).
    _maybe_create_board_connection(
        _FakeRepo(fail=True),
        project_id=1,
        board_adapter="github",
        repository="o/app",
        project_owner="org",
        project_number="",
    )
