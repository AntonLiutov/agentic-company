from pathlib import Path

import pytest

from agentic_company.agents.head.contracts import HEAD_TOOL_CONTRACT_REGISTRY
from agentic_company.agents.team_lead.contracts import TEAM_LEAD_TOOL_CONTRACT_REGISTRY

from agentic_company.platform.skills import (
    DEFAULT_SKILL_CATALOG,
    KNOWN_ARTIFACT_TYPES,
    KNOWN_DASHBOARD_STATUSES,
    KNOWN_RISK_LEVELS,
    KNOWN_VISIBILITIES,
    SKILL_CATALOG_DIR,
    applicable_skills_for_agent,
    provision_native_skills,
    render_skill_instructions,
    select_skills_for_agent,
)
from agentic_company.platform.contracts.tool_contracts import CODEX_EXEC_TOOL_CONTRACT


def test_skill_catalog_loads_initial_skills_with_unique_ids():
    expected = {
        "requirements-analysis",
        "architecture-design",
        "sprint-planning",
        "frontend-build",
        "web-app-aesthetics",
        "browser-smoke-qa",
        "no-placeholder-check",
        "deployment-check",
        "release-reporting",
        "repair-loop",
        "screenshot-review",
        "git-pr-workflow",
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
    assert _ids("fullstack-agent", "fullstack") == [
        "frontend-build",
        "git-pr-workflow",
        "web-app-aesthetics",
    ]
    assert _ids("deployment-agent", "deployment") == ["deployment-check", "git-pr-workflow"]
    assert _ids("documentation-handoff-agent", "handoff") == ["release-reporting"]
    assert _ids("team-lead-agent", "team_lead") == ["repair-loop"]


def test_qa_selects_smoke_placeholder_and_screenshot_skills():
    assert _ids("qa-agent", "qa") == [
        "browser-smoke-qa",
        "git-pr-workflow",
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
        "git-pr-workflow",
        "web-app-aesthetics",
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


def test_provision_native_skills_writes_codex_discoverable_catalog(tmp_path):
    # Codex auto-discovers skills from `<workspace>/.agents/skills/<id>/SKILL.md` and
    # triggers them by their `description` (progressive disclosure). We provision the
    # WHOLE catalog there verbatim — every authored SKILL.md, frontmatter intact.
    skills_root = provision_native_skills(tmp_path)

    assert skills_root == tmp_path / ".agents" / "skills"
    for skill in DEFAULT_SKILL_CATALOG.all():
        native = skills_root / skill.skill_id / "SKILL.md"
        assert native.is_file(), f"{skill.skill_id} not provisioned for Codex discovery"
        text = native.read_text(encoding="utf-8")
        # Codex-native frontmatter: name + description are what it reads first.
        frontmatter = text.split("---", 2)[1]
        assert "name:" in frontmatter and "description:" in frontmatter
        # It is the authored file verbatim (full playbook body present), NOT a sidecar.
        assert text == Path(skill.source_path).read_text(encoding="utf-8")
    # the ADL sidecar (adl.yaml) is internal — it must NOT be shipped into Codex's path
    assert not list(skills_root.rglob("adl.yaml"))


def test_provision_native_skills_is_idempotent_and_refreshes(tmp_path):
    first = provision_native_skills(tmp_path)
    qa = first / "browser-smoke-qa" / "SKILL.md"
    qa.write_text("STALE", encoding="utf-8")  # simulate a stale copy

    provision_native_skills(tmp_path)  # re-provision refreshes verbatim, never raises

    assert qa.read_text(encoding="utf-8") != "STALE"


def test_each_agent_resolves_to_its_own_scoped_skills():
    # Per-role applicability is the contract Codex's description-triggering enforces at
    # runtime: the builder gets the styling skill, never a QA-only one.
    fullstack = [s.skill_id for s in applicable_skills_for_agent("fullstack-agent")]
    assert "web-app-aesthetics" in fullstack and "frontend-build" in fullstack
    assert "browser-smoke-qa" not in fullstack  # a QA-only skill never leaks in

    # an agent with no applicable skills resolves to nothing (no noise)
    assert applicable_skills_for_agent("nonexistent-agent") == ()


def test_codex_worker_agent_ids_resolve_to_their_planner_skills():
    # Codex workers run under *-codex-agent ids that differ from the planner ids
    # skills target (qa-codex-agent vs qa-agent). Applicability MUST still resolve, or
    # QA/Deployment/Handoff workers silently get zero skills — the exact bug the
    # phase-2 analysis caught (the smoke harness used planner ids and missed it).
    from agentic_company.agents.deployment.codex_cli import DEPLOYMENT_CODEX_AGENT_ID
    from agentic_company.agents.handoff.codex_cli import HANDOFF_CODEX_AGENT_ID
    from agentic_company.agents.quality.codex_cli import QUALITY_CODEX_AGENT_ID

    qa = [s.skill_id for s in applicable_skills_for_agent(QUALITY_CODEX_AGENT_ID)]
    assert "git-pr-workflow" in qa and "browser-smoke-qa" in qa

    dep = [s.skill_id for s in applicable_skills_for_agent(DEPLOYMENT_CODEX_AGENT_ID)]
    assert "git-pr-workflow" in dep and "deployment-check" in dep

    hand = [s.skill_id for s in applicable_skills_for_agent(HANDOFF_CODEX_AGENT_ID)]
    assert "release-reporting" in hand


def _runtime_specialist_skill_map():
    """The EXACT trace_agent_id each specialist Codex worker passes to the runner,
    mapped to a skill it MUST carry. This is the contract the whole skill-delivery
    redesign rests on — if any of these resolves to zero skills, that agent silently
    loses its playbook (the bug that stopped QA from merging)."""
    from agentic_company.agents.architecture.codex_cli import ARCHITECT_AGENT_ID
    from agentic_company.agents.business_analysis.codex_cli import BUSINESS_ANALYST_AGENT_ID
    from agentic_company.agents.deployment.codex_cli import DEPLOYMENT_CODEX_AGENT_ID
    from agentic_company.agents.handoff.codex_cli import HANDOFF_CODEX_AGENT_ID
    from agentic_company.agents.project_manager.codex_cli import PROJECT_MANAGER_AGENT_ID
    from agentic_company.agents.quality.codex_cli import QUALITY_CODEX_AGENT_ID

    return {
        BUSINESS_ANALYST_AGENT_ID: "requirements-analysis",
        ARCHITECT_AGENT_ID: "architecture-design",
        PROJECT_MANAGER_AGENT_ID: "sprint-planning",
        "fullstack-agent": "frontend-build",  # fullstack passes request.agent_id
        QUALITY_CODEX_AGENT_ID: "browser-smoke-qa",
        DEPLOYMENT_CODEX_AGENT_ID: "deployment-check",
        HANDOFF_CODEX_AGENT_ID: "release-reporting",
    }


@pytest.mark.parametrize("runtime_id,must_have", list(_runtime_specialist_skill_map().items()))
def test_every_specialist_codex_worker_loads_its_skills(runtime_id, must_have):
    skills = [s.skill_id for s in applicable_skills_for_agent(runtime_id)]
    assert must_have in skills, f"{runtime_id} is MISSING {must_have}; got {skills}"


def test_all_pr_producing_agents_carry_git_pr_workflow_at_runtime():
    # The agents that branch/commit/open/merge PRs must ALL carry git-pr-workflow under
    # their RUNTIME id — otherwise they cannot do any git/PR work (e.g. QA cannot merge).
    from agentic_company.agents.deployment.codex_cli import DEPLOYMENT_CODEX_AGENT_ID
    from agentic_company.agents.quality.codex_cli import QUALITY_CODEX_AGENT_ID

    for rid in ("fullstack-agent", QUALITY_CODEX_AGENT_ID, DEPLOYMENT_CODEX_AGENT_ID):
        skills = [s.skill_id for s in applicable_skills_for_agent(rid)]
        assert "git-pr-workflow" in skills, f"{rid} cannot do PR/git work; got {skills}"


def test_qa_runtime_worker_can_review_and_merge():
    # Direct regression for "QA не смерджил": the QA worker (runtime id) carries BOTH the
    # browser QA skill AND the PR workflow, and that workflow instructs merge-on-pass.
    from agentic_company.agents.quality.codex_cli import QUALITY_CODEX_AGENT_ID

    skills = [s.skill_id for s in applicable_skills_for_agent(QUALITY_CODEX_AGENT_ID)]
    assert "git-pr-workflow" in skills and "browser-smoke-qa" in skills
    body = DEFAULT_SKILL_CATALOG.get("git-pr-workflow").body
    assert "gh pr merge" in body  # the merge command QA must run on a pass


def test_both_coordinators_select_the_repair_loop_skill():
    # Head and Team Lead are LangChain agents (skills via select_skills_for_agent paste).
    for agent_id, stage in (("head-agent", "head"), ("team-lead-agent", "team_lead")):
        ids = [s.skill_id for s in select_skills_for_agent(agent_id=agent_id, stage=stage).selections]
        assert "repair-loop" in ids, f"{agent_id} missing repair-loop; got {ids}"


def test_runner_provisions_native_skills_into_worker_cwd(tmp_path):
    # The real failure: most ADL workers (the Builder among them) run with cwd = run_dir,
    # a NON-git workspace, and Codex scans $CWD/.agents/skills FIRST. Provisioning only
    # the parent silently missed them, so the Builder fell back to a bundled skill. The
    # runner MUST provision the cwd ITSELF so $CWD/.agents/skills resolves.
    from agentic_company.integrations.codex import runner

    runner._NATIVE_SKILLS_READY.clear()
    run_dir = tmp_path / "run-workspace"  # cwd = run_dir, no .git (like the real Builder)
    run_dir.mkdir()
    command = ["codex", "exec", "--cd", str(run_dir), "-"]

    runner._ensure_native_skills(command)

    skills_root = run_dir / ".agents" / "skills"  # == $CWD/.agents/skills
    assert (skills_root / "git-pr-workflow" / "SKILL.md").is_file()
    assert (skills_root / "browser-smoke-qa" / "SKILL.md").is_file()

    # idempotent: a second call short-circuits via the per-workspace guard (no re-copy)
    (skills_root / "git-pr-workflow" / "SKILL.md").write_text("X", encoding="utf-8")
    runner._ensure_native_skills(command)
    assert (skills_root / "git-pr-workflow" / "SKILL.md").read_text(encoding="utf-8") == "X"
    runner._NATIVE_SKILLS_READY.clear()


def test_exclude_adl_scaffolding_writes_every_pattern(tmp_path):
    # The deliverable git must never carry ADL run scaffolding. Every pattern lands in
    # .git/info/exclude, idempotently — regression for the qa/ + execution-summary.md leak.
    from agentic_company.integrations.codex import runner

    repo = tmp_path / "generated-project"
    (repo / ".git" / "info").mkdir(parents=True)

    runner._exclude_adl_scaffolding_from_git(repo)
    excl = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()
    assert set(runner._ADL_SCAFFOLDING_EXCLUDES) <= set(excl)
    # root-anchored so a legit nested src/qa/ is never excluded
    assert "/qa/" in excl and "/execution-summary.md" in excl and "/debug.log" in excl

    runner._exclude_adl_scaffolding_from_git(repo)  # idempotent: no duplicate lines
    excl2 = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()
    assert excl == excl2


def test_runner_provisions_native_skills_inside_generated_git_repo(tmp_path):
    # When generated-project is a cloned git repo, Codex native skill discovery stops
    # at that repo root and never reaches <run>/.agents/skills. The runner must also
    # provision inside generated-project, while excluding the runtime-only folder from git.
    from agentic_company.integrations.codex import runner

    runner._NATIVE_SKILLS_READY.clear()
    target = tmp_path / "generated-project"
    (target / ".git" / "info").mkdir(parents=True)
    command = ["codex", "exec", "--cd", str(target), "-"]

    runner._ensure_native_skills(command)

    parent_skills = tmp_path / ".agents" / "skills"
    repo_skills = target / ".agents" / "skills"
    assert (parent_skills / "git-pr-workflow" / "SKILL.md").is_file()
    assert (repo_skills / "git-pr-workflow" / "SKILL.md").is_file()
    assert ".agents/" in (target / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    runner._NATIVE_SKILLS_READY.clear()


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
