from agentic_company.platform.agent.team_spec import (
    TeamPreset,
    estimate_team_preset,
    team_spec,
)


def test_standard_team_preset_reproduces_current_roster():
    spec = team_spec("standard")

    assert spec.preset is TeamPreset.STANDARD
    assert [role.role_id for role in spec.roles] == [
        "business-analyst-agent",
        "architect-agent",
        "project-manager-agent",
        "team-lead-agent",
        "fullstack-agent",
        "qa-agent",
        "deployment-agent",
        "documentation-handoff-agent",
        "codex-review-agent",
    ]


def test_team_estimator_is_advisory_by_complexity():
    assert estimate_team_preset(complexity="simple", requires_deployment=False) is TeamPreset.SMALL
    assert estimate_team_preset(complexity="complex", requires_deployment=False) is TeamPreset.LARGE
    assert estimate_team_preset(complexity="simple", requires_deployment=True) is TeamPreset.LARGE
