import json

from agentic_company.agents.quality import graph as quality_graph
from agentic_company.agents.quality.codex_cli import build_quality_codex_prompt
from agentic_company.platform.db.models import ExecutionRequest


def test_quality_prompt_includes_execution_instructions(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agentic_company.agents.quality.codex_cli.render_incoming_messages_for_prompt",
        lambda *args, **kwargs: "- None",
    )

    request = ExecutionRequest(
        run_id="run-1",
        agent_id="qa-agent",
        agent_version="1",
        maturity_level="prototype",
        provider="codex",
        model="gpt-5.4",
        target_project_dir=str(tmp_path / "generated-project"),
        input_artifacts=["00-requirements.md"],
        expected_outputs=["08-qa-report-F1.md", "qa/results-F1.json"],
        instructions=[
            "A git repository is connected. Follow git-pr-workflow; report a verdict "
            "and let the platform merge on a pass.",
        ],
        constraints=[],
        completed_work_item_ids=[],
    )

    prompt = build_quality_codex_prompt(
        request,
        tmp_path,
        {
            "work_item_id": "F1",
            "title": "Core feature",
            "acceptance_criteria": ["Feature works."],
        },
        attempt=1,
        previous_summary="",
    )

    assert "Execution instructions:" in prompt
    assert "git-pr-workflow" in prompt
    assert "let the platform merge" in prompt


def test_quality_request_triggers_git_pr_workflow_skill(tmp_path, monkeypatch):
    # The prompt only gives run context + triggers the skill; the actual review/merge/comment
    # behavior lives in the git-pr-workflow SKILL (asserted in test_skills), not hardcoded here.
    monkeypatch.setattr(
        quality_graph,
        "_run_repo_context",
        lambda run_id: {"repository": "o/app", "base_branch": "main"},
    )
    monkeypatch.setattr(quality_graph, "completed_work_item_ids", lambda *args: [])
    monkeypatch.setattr(
        quality_graph,
        "write_execution_request",
        lambda run_dir, payload: (
            (run_dir / "delivery").mkdir(exist_ok=True)
            or (run_dir / "delivery" / "execution-request.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
        ),
    )

    quality_graph._write_quality_execution_request(
        tmp_path,
        {
            "run_id": "run-1",
            "target_project_dir": str(tmp_path / "generated-project"),
        },
        {"work_item_id": "F1", "sprint_id": "sprint-01"},
    )

    request = json.loads((tmp_path / "delivery" / "execution-request.json").read_text())
    instructions = "\n".join(request["instructions"])
    assert "adl/f1" in instructions  # the work-item branch (run context)
    assert "git-pr-workflow" in instructions  # prompt triggers the skill (behavior lives there)
    assert "platform does NOT touch git" in instructions
