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


def test_quality_request_delegates_merge_only_when_pr_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(
        quality_graph,
        "_run_repo_context",
        lambda run_id: {"repository": "o/app", "base_branch": "main"},
    )
    monkeypatch.setattr(
        quality_graph,
        "_work_item_pr",
        lambda run_id, work_item_id: {"url": "https://github.com/o/app/pull/7"},
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
    assert "https://github.com/o/app/pull/7" in instructions  # the recorded PR is referenced
    # the PLATFORM performs the merge on a pass; QA must NOT run gh pr merge itself (the
    # workspace-write worker sandbox has no merge credentials and would only 401).
    assert "PLATFORM performs the merge" in instructions
    assert "you do not run `gh pr merge`" in instructions


def test_quality_request_does_not_invent_pr_gate_without_recorded_pr(tmp_path, monkeypatch):
    monkeypatch.setattr(
        quality_graph,
        "_run_repo_context",
        lambda run_id: {"repository": "o/app", "base_branch": "main"},
    )
    monkeypatch.setattr(quality_graph, "_work_item_pr", lambda run_id, work_item_id: None)
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
        {"work_item_id": "DEPLOY", "sprint_id": "sprint-02"},
    )

    request = json.loads((tmp_path / "delivery" / "execution-request.json").read_text())
    instructions = "\n".join(request["instructions"])
    assert "Do not invent a PR gate" in instructions
    assert "gh pr merge" not in instructions
