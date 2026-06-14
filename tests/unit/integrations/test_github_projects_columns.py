import json

from agentic_company.integrations.github.projects import ensure_status_columns


class _FakeGh:
    def __init__(self, present):
        self.calls = []
        self._present = present

    def run(self, args, *, cwd=None):
        self.calls.append(args)
        q = next((a for a in args if a.startswith("query=")), "")
        if q.startswith("query=query"):
            opts = [{"id": f"id-{n}", "name": n, "color": "GRAY"} for n in self._present]
            return json.dumps({"data": {"node": {"options": opts}}})
        full = ["Todo", "In Progress", "In Review", "Blocked", "Done"]
        opts = [{"id": f"id-{n}", "name": n} for n in full]
        return json.dumps({"data": {"updateProjectV2Field": {"projectV2Field": {"options": opts}}}})


def test_ensure_status_columns_adds_missing_and_reports():
    gh = _FakeGh(present=["Todo", "In Progress", "Done"])
    mapping, added = ensure_status_columns(gh, status_field_id="F")

    assert set(added) == {"In Review", "Blocked"}
    assert set(mapping) == {"Todo", "In Progress", "In Review", "Blocked", "Done"}
    # Existing options were re-supplied WITH their ids -> non-destructive.
    mutation = next(c for c in gh.calls if any("query=mutation" in a for a in c))
    assert 'id:"id-Todo"' in " ".join(mutation)


def test_ensure_status_columns_is_noop_when_all_present():
    gh = _FakeGh(present=["Todo", "In Progress", "In Review", "Blocked", "Done"])
    mapping, added = ensure_status_columns(gh, status_field_id="F")

    assert added == []
    assert set(mapping) == {"Todo", "In Progress", "In Review", "Blocked", "Done"}
    # No mutation issued when nothing is missing.
    assert not any(any("query=mutation" in a for a in c) for c in gh.calls)
