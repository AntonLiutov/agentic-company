import json

from agentic_company.integrations.github.projects import (
    provision_project_board,
    resolve_project_board,
)


class _FakeGh:
    def run(self, args, *, cwd=None):
        if args[:2] == ["project", "view"]:
            return json.dumps({"id": "PVT_x", "number": 5})
        if args[:2] == ["project", "field-list"]:
            return json.dumps(
                {
                    "fields": [
                        {"name": "Title", "id": "F_title"},
                        {
                            "name": "Status",
                            "id": "PVTSSF_x",
                            "options": [
                                {"name": "Todo", "id": "o-todo"},
                                {"name": "Done", "id": "o-done"},
                            ],
                        },
                    ]
                }
            )
        return ""


def test_resolve_project_board_extracts_ids_and_status_options():
    board = resolve_project_board(_FakeGh(), owner="o", project_number=5)

    assert board.project_id == "PVT_x"
    assert board.status_field_id == "PVTSSF_x"
    assert board.status_options == {"Todo": "o-todo", "Done": "o-done"}


def test_resolve_project_board_tolerates_missing_status_field():
    class _NoStatus:
        def run(self, args, *, cwd=None):
            if args[:2] == ["project", "view"]:
                return json.dumps({"id": "PVT_x"})
            return json.dumps({"fields": [{"name": "Title", "id": "F_title"}]})

    board = resolve_project_board(_NoStatus(), owner="o", project_number=5)
    assert board.project_id == "PVT_x"
    assert board.status_field_id == ""
    assert board.status_options == {}


class _ProvisionGh:
    """Emulates the gh calls provision_project_board makes."""

    def __init__(self):
        self.calls = []

    def run(self, args, *, cwd=None):
        self.calls.append(args)
        if args[:2] == ["project", "create"]:
            return json.dumps({"number": 9, "id": "PVT_new", "url": "https://x/9"})
        if args[:2] == ["project", "view"]:
            return json.dumps({"id": "PVT_new"})
        if args[:2] == ["project", "field-list"]:
            return json.dumps(
                {
                    "fields": [
                        {"name": "Status", "id": "F", "options": [{"name": "Todo", "id": "o-todo"}]}
                    ]
                }
            )
        if args[:2] == ["api", "graphql"]:
            query = next((a for a in args if a.startswith("query=")), "")
            if query.startswith("query=query"):
                return json.dumps(
                    {
                        "data": {
                            "node": {"options": [{"id": "o-todo", "name": "Todo", "color": "GRAY"}]}
                        }
                    }
                )
            opts = [
                {"id": f"o-{n}", "name": n}
                for n in ("Todo", "Blocked", "In Progress", "In Review", "Done")
            ]
            return json.dumps(
                {"data": {"updateProjectV2Field": {"projectV2Field": {"options": opts}}}}
            )
        return ""


def test_provision_project_board_creates_links_and_shapes_columns():
    gh = _ProvisionGh()
    number, board = provision_project_board(gh, owner="o", repository="o/app", title="ADL · App")

    assert number == "9"
    assert board.project_id == "PVT_new"
    assert board.status_field_id == "F"
    # The board was linked to the repo and its status columns ensured.
    assert ["project", "link", "9", "--owner", "o", "--repo", "o/app"] in gh.calls
    assert "Blocked" in board.status_options and "In Review" in board.status_options
