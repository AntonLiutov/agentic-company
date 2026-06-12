from agentic_company.agents.head.contracts import HEAD_TOOL_CONTRACT_REGISTRY
from agentic_company.agents.team_lead.contracts import TEAM_LEAD_TOOL_CONTRACT_REGISTRY
from agentic_company.platform.skills import (
    DEFAULT_SKILL_CATALOG,
    KNOWN_ARTIFACT_TYPES,
    KNOWN_DASHBOARD_STATUSES,
    KNOWN_RISK_LEVELS,
    KNOWN_VISIBILITIES,
    SKILL_CATALOG_DIR,
    render_skill_instructions,
    select_skills_for_agent,
)
from agentic_company.platform.tool_contracts import CODEX_EXEC_TOOL_CONTRACT


def test_skill_catalog_loads_initial_skills_with_unique_ids():
    expected = {
        "requirements-analysis",
        "architecture-design",
        "sprint-planning",
        "frontend-build",
        "browser-smoke-qa",
        "no-placeholder-check",
        "deployment-check",
        "release-reporting",
        "repair-loop",
        "screenshot-review",
    }

    assert set(DEFAULT_SKILL_CATALOG.ids()) == expected
    assert len(DEFAULT_SKILL_CATALOG.ids()) == len(DEFAULT_SKILL_CATALOG.all())
    for skill_id in expected:
        assert (SKILL_CATALOG_DIR / skill_id / "SKILL.md").exists()
        assert (SKILL_CATALOG_DIR / skill_id / "agents" / "adl.yaml").exists()


def test_initial_skills_are_contract_and_dashboard_ready():
    available_tools = {
        CODEX_EXEC_TOOL_CONTRACT.tool_name,
        *HEAD_TOOL_CONTRACT_REGISTRY.names(),
        *TEAM_LEAD_TOOL_CONTRACT_REGISTRY.names(),
    }

    for skill in DEFAULT_SKILL_CATALOG.all():
        assert skill.version
        assert skill.body
        assert skill.instructions
        assert skill.applies_to_agents
        assert skill.required_tools
        assert skill.expected_artifacts
        assert skill.examples
        assert skill.source_path.endswith("SKILL.md")
        assert skill.contract_path.endswith("agents\\adl.yaml") or skill.contract_path.endswith(
            "agents/adl.yaml"
        )
        assert len(skill.contract_hash) == 16
        assert skill.status == "active"
        assert skill.trust_level == "system"
        assert skill.governance is not None
        assert skill.validation is not None
        assert skill.governance.risk_level in KNOWN_RISK_LEVELS
        assert skill.dashboard_status in KNOWN_DASHBOARD_STATUSES
        assert "internal" in skill.external_systems_supported
        assert {"github", "jira", "azure_devops"}.issubset(skill.external_systems_supported)
        for tool_name in skill.required_tools:
            assert tool_name in available_tools
        for artifact in skill.expected_artifacts:
            assert artifact.artifact_type in KNOWN_ARTIFACT_TYPES
            assert artifact.visibility in KNOWN_VISIBILITIES


def test_skill_frontmatter_is_portable_and_adl_contract_is_sidecar():
    for skill in DEFAULT_SKILL_CATALOG.all():
        text = (SKILL_CATALOG_DIR / skill.skill_id / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        assert "skill_id:" not in frontmatter
        assert "required_tools:" not in frontmatter
        assert "expected_artifacts:" not in frontmatter
        assert "name:" in frontmatter
        assert "description:" in frontmatter


def test_default_skill_selection_by_agent():
    assert _ids("business-analyst-agent", "business_analysis") == ["requirements-analysis"]
    assert _ids("architect-agent", "architecture") == ["architecture-design"]
    assert _ids("project-manager-agent", "project_management") == ["sprint-planning"]
    assert _ids("fullstack-agent", "fullstack") == ["frontend-build"]
    assert _ids("deployment-agent", "deployment") == ["deployment-check"]
    assert _ids("documentation-handoff-agent", "handoff") == ["release-reporting"]
    assert _ids("team-lead-agent", "team_lead") == ["repair-loop"]


def test_qa_selects_smoke_placeholder_and_screenshot_skills():
    assert _ids("qa-agent", "qa") == [
        "browser-smoke-qa",
        "no-placeholder-check",
        "screenshot-review",
    ]


def test_repair_context_adds_repair_loop_for_specialist():
    selection = select_skills_for_agent(
        agent_id="fullstack-agent",
        stage="fullstack",
        delivery_state={"run_id": "run", "status": "qa_failed"},
    )

    assert [item.skill_id for item in selection.selections] == [
        "frontend-build",
        "repair-loop",
    ]


def test_render_skill_instructions_contains_selected_skills_only():
    selection = select_skills_for_agent(agent_id="qa-agent", stage="qa")
    rendered = render_skill_instructions(selection)

    assert "Selected runtime skills" in rendered
    assert "browser-smoke-qa" in rendered
    assert "no-placeholder-check" in rendered
    assert "screenshot-review" in rendered
    assert "requirements-analysis" not in rendered
    assert "Playbook:" in rendered
    assert "Contract hints:" in rendered
    assert "passed_with_limited_visual_evidence" in rendered
    assert "do not assume the full skill catalog" in rendered


def test_selected_skill_trace_data_includes_source_and_contract_hash():
    selection = select_skills_for_agent(agent_id="fullstack-agent", stage="fullstack")
    data = selection.to_trace_data()[0]

    assert data["skill_id"] == "frontend-build"
    assert data["source_path"].endswith("SKILL.md")
    assert len(data["contract_hash"]) == 16
    assert data["expected_artifacts"][0]["artifact_type"] == "execution_summary"


def _ids(agent_id: str, stage: str) -> list[str]:
    selection = select_skills_for_agent(agent_id=agent_id, stage=stage)
    return [item.skill_id for item in selection.selections]
