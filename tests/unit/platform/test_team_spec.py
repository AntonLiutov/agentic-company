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


def test_standard_delivery_workers_match_the_runtime_worker_slots():
    # Non-self-referential guard: the preset's delivery workers must equal the runtime's
    # FIXED worker slots (TeamLeadWorkers). If a preset ever claims more workers than the
    # roster can hold, this fails — making "presets are advisory, arity is fixed" a tested
    # fact (true dynamic arity is the deferred follow-up).
    from agentic_company.agents.team_lead.tools import TeamLeadWorkers

    spec = team_spec("standard")
    worker_roles = [
        r
        for r in spec.roles
        if r.stage in {"delivery", "quality", "deployment", "handoff"}
        and r.role_id != "team-lead-agent"  # the orchestrator, not a worker slot
    ]
    assert len(worker_roles) == len(TeamLeadWorkers.__dataclass_fields__)
