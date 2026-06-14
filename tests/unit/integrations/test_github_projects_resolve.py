import json

from agentic_company.integrations.github.projects import resolve_project_board


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
