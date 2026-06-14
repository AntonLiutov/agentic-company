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
    mutation = " ".join(next(c for c in gh.calls if any("query=mutation" in a for a in c)))
    # Existing options are re-supplied WITH their ids (non-destructive).
    assert 'id:"id-Todo"' in mutation
    assert 'id:"id-In Progress"' in mutation


def test_ensure_status_columns_is_noop_when_all_present_and_ordered():
    gh = _FakeGh(present=["Todo", "Blocked", "In Progress", "In Review", "Done"])
    mapping, added = ensure_status_columns(gh, status_field_id="F")

    assert added == []
    assert set(mapping) == {"Todo", "Blocked", "In Progress", "In Review", "Done"}
    # No mutation when the desired columns are present AND already in order.
    assert not any(any("query=mutation" in a for a in c) for c in gh.calls)


def test_ensure_status_columns_reorders_when_order_differs():
    # All columns present but in the wrong order -> code reorders them.
    gh = _FakeGh(present=["Done", "In Progress", "Todo", "Blocked", "In Review"])
    mapping, added = ensure_status_columns(gh, status_field_id="F")

    assert added == []  # nothing missing, only a reorder
    assert any(any("query=mutation" in a for a in c) for c in gh.calls)
