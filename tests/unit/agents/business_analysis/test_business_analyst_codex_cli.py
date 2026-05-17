import json
import subprocess
from pathlib import Path

from agentic_company.agents.business_analysis.codex_cli import (
    BUSINESS_ANALYST_WORK_DIR,
    BusinessAnalystCodexRunner,
    build_business_analysis_codex_prompt,
)
from agentic_company.agents.business_analysis.graph import (
    BUSINESS_ANALYSIS_JSON,
    BUSINESS_ANALYSIS_MD,
    BUSINESS_ANALYSIS_REQUEST,
)


def test_business_analysis_prompt_scopes_codex_to_analysis_artifacts(tmp_path):
    requirements = tmp_path / "00-requirements.md"
    requirements.write_text(
        "Sprint Alpha\nF1: Build a task tracker for small teams.\n",
        encoding="utf-8",
    )
    request = {
        "run_id": "run",
        "model": "gpt-5.3-codex",
        "requirements_artifact": "00-requirements.md",
        "expected_outputs": [BUSINESS_ANALYSIS_MD, BUSINESS_ANALYSIS_JSON],
        "incoming_messages": (
            "- Message id: msg-head\n  From: head-agent\n  Content:\n    Analyze this."
        ),
        "available_agents": [
            {
                "agent_id": "business-analyst-agent",
                "name": "Business Analyst Agent",
                "stage": "business_analysis",
                "family": "planning",
                "runtime": "L4 LangGraph Agent Executor + L6 Codex Business Analyst",
            },
            {
                "agent_id": "head-agent",
                "name": "Head Agent",
                "stage": "company_coordination",
                "family": "coordination",
                "runtime": "planned",
            },
        ],
    }

    prompt = build_business_analysis_codex_prompt(request, tmp_path)

    assert BUSINESS_ANALYSIS_MD in prompt
    assert BUSINESS_ANALYSIS_JSON in prompt
    assert "Write only the two allowed business analysis artifacts" in prompt
    assert "Do not modify generated-project files" in prompt
    assert "Do not create sprint plans, feature queues" in prompt
    assert "Azure-oriented deployment infrastructure" in prompt
    assert "Azure deployment is a supported platform capability" in prompt
    assert "Scale the depth of analysis to the source request" in prompt
    assert "Do not inflate a simple request into an enterprise" in prompt
    assert "Preserve minimum BA standards" in prompt
    assert "treat deployable access\n  as the default delivery expectation" in prompt
    assert "local\n  only, prototype only, no deployment" in prompt
    assert "OpenAI/Codex" in prompt
    assert "Available agent registry snapshot" in prompt
    assert "business-analyst-agent: Business Analyst Agent" in prompt
    assert "head-agent: Head Agent" in prompt
    assert "new agents may be added without changing" in prompt
    assert "two artifacts with different audiences" in prompt
    assert "Markdown is the user-facing business analysis brief" in prompt
    assert "Do not mention internal platform agents" in prompt
    assert "JSON is the internal platform contract" in prompt
    assert "notes must stay\n  out of the user-facing Markdown" in prompt
    assert "internal contract for downstream platform roles" in prompt
    assert "Do not copy registry agents into target users" in prompt
    assert "people, customer roles, product owners" in prompt
    assert "put it only in JSON `coordination_notes`" in prompt
    assert "Treat platform execution details as internal coordination context" in prompt
    assert "Do not include tool write policy" in prompt
    assert "agent registry, orchestration routing, or AI-provider details" in prompt
    assert "source_refs" in prompt
    assert "feature ids, sprint ids, milestones, phases" in prompt
    assert "Preserve every distinct feature/source label" in prompt
    assert "collapse many features into a smaller fixed set" in prompt
    assert "limit references to examples" in prompt
    assert "provided_constraints" in prompt
    assert "open_question_triage" in prompt
    assert "fixed agent list" in prompt
    assert "not an allowed list" in prompt
    assert "coordination_notes" in prompt
    assert "Head Agent coordinates this planning flow" in prompt
    assert "Incoming coordinator messages" in prompt
    assert "Analyze this" in prompt
    assert "Do not call them handoffs" in prompt
    assert "Project Manager owns sprint planning" in prompt


def test_business_analyst_codex_runner_maps_valid_contract_to_completed_result(tmp_path):
    (tmp_path / "00-requirements.md").write_text(
        "Build a task tracker for small teams.\n",
        encoding="utf-8",
    )
    request_path = tmp_path / BUSINESS_ANALYSIS_REQUEST
    request_path.parent.mkdir(parents=True)
    request_path.write_text(
        json.dumps(
            {
                "run_id": "run",
                "agent_id": "business-analyst-agent",
                "model": "gpt-5.3-codex",
                "requirements_artifact": "00-requirements.md",
                "expected_outputs": [BUSINESS_ANALYSIS_MD, BUSINESS_ANALYSIS_JSON],
                "codex_resume_thread_id": "",
                "available_agents": [],
                "incoming_messages": "- No incoming coordinator messages were provided.",
            }
        ),
        encoding="utf-8",
    )

    def fake_command(
        command,
        prompt,
        timeout_seconds,
        log_path: Path,
        raw_events_path: Path,
    ):
        (tmp_path / BUSINESS_ANALYSIS_MD).write_text("# Business Analysis\n", encoding="utf-8")
        (tmp_path / BUSINESS_ANALYSIS_JSON).write_text(
            json.dumps(
                {
                    "product_goal": "Help small teams track tasks.",
                    "target_users": ["Team member"],
                    "stakeholders": ["Product owner"],
                    "user_stories": [],
                    "acceptance_criteria": [],
                    "business_rules": [],
                    "scope": [],
                    "non_goals": [],
                    "provided_constraints": [],
                    "assumptions": [],
                    "risks": [],
                    "open_questions": [],
                    "open_question_triage": {},
                    "recommended_product_decisions": [],
                    "coordination_notes": [],
                    "delivery_notes": [],
                }
            ),
            encoding="utf-8",
        )
        log_path.write_text("done\n", encoding="utf-8")
        raw_events_path.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="Business analysis complete.")

    result = BusinessAnalystCodexRunner(command_executor=fake_command).run(tmp_path)

    assert result.status == "business_analysis_completed"
    assert BUSINESS_ANALYSIS_MD in result.output_artifacts
    assert BUSINESS_ANALYSIS_JSON in result.output_artifacts
    assert any(
        artifact.startswith((BUSINESS_ANALYST_WORK_DIR / "codex").as_posix())
        for artifact in result.output_artifacts
    )
    assert result.blocking_findings == []
