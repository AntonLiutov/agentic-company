import json

from agentic_company.integrations.github.projects import ensure_sprints


class _FakeGh:
    def __init__(self, existing):
        self.calls = []
        self._existing = existing  # list of {"title","number"}
        self._next = 100

    def run(self, args, *, cwd=None):
        self.calls.append(args)
        path = next((a for a in args if a.startswith("repos/")), "")
        if "milestones?state=all" in path:  # list
            return json.dumps(self._existing)
        if path.endswith("/milestones"):  # create -> return the new number
            self._next += 1
            return str(self._next)
        return ""


def test_ensure_sprints_creates_missing_and_reuses_existing():
    gh = _FakeGh(existing=[{"title": "Planning", "number": 1}])
    result = ensure_sprints(gh, repository="o/r", sprints=("Planning", "Sprint 1", "Sprint 2"))

    assert result["Planning"] == 1  # reused, not recreated
    assert result["Sprint 1"] != result["Sprint 2"]  # both created with distinct numbers
    creates = [c for c in gh.calls if "title=Sprint 1" in c or "title=Sprint 2" in c]
    assert len(creates) == 2
    # Planning already existed -> no create call for it.
    assert not any("title=Planning" in c for c in gh.calls)


class _RaceGh:
    """Create 422s (another writer won the race); the milestone shows on re-list."""

    def __init__(self):
        self.calls = []
        self._listed = 0

    def run(self, args, *, cwd=None):
        self.calls.append(args)
        path = next((a for a in args if a.startswith("repos/")), "")
        if "milestones?state=all" in path:
            self._listed += 1
            if self._listed == 1:
                return "[]"  # not there yet
            return json.dumps([{"title": "Sprint 1", "number": 7}])  # the racer's milestone
        if path.endswith("/milestones"):
            raise RuntimeError("gh: Validation Failed (HTTP 422)")
        return ""


def test_ensure_sprints_tolerates_a_concurrent_create_race():
    gh = _RaceGh()
    result = ensure_sprints(gh, repository="o/r", sprints=("Sprint 1",))
    assert result == {"Sprint 1": 7}  # adopted the milestone the racer created


def test_ensure_sprints_is_idempotent_when_all_present():
    gh = _FakeGh(
        existing=[
            {"title": "Planning", "number": 1},
            {"title": "Sprint 1", "number": 2},
        ]
    )
    result = ensure_sprints(gh, repository="o/r", sprints=("Planning", "Sprint 1"))

    assert result == {"Planning": 1, "Sprint 1": 2}
    creates = [c for c in gh.calls if c[:2] == ["api", "repos/o/r/milestones"]]
    assert creates == []  # nothing created
