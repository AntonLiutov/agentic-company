import subprocess

from agentic_company.console.web.db import ConsoleRepository
from agentic_company.platform.delivery.codex_review import (
    CodexReviewRequest,
    CodexReviewRunner,
    build_codex_review_prompt,
)


def test_codex_review_runner_uses_read_only_sandbox(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _create_run(tmp_path, monkeypatch, run_dir, "run")

    def executor(command, prompt, timeout_seconds, log_path, raw_events_path):
        assert "--sandbox" in command
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "--output-last-message" in command
        assert "Read only" in prompt
        output_path = command[command.index("--output-last-message") + 1]
        assert str(output_path).endswith("summary.md")
        return subprocess.CompletedProcess(command, 0, stdout="Review says revise.", stderr="")

    result = CodexReviewRunner(command_executor=executor).run(
        CodexReviewRequest(
            run_id="run",
            run_dir=run_dir,
            requesting_agent="team-lead-agent",
            target_agent="documentation-handoff-agent",
            purpose="Review report.",
            question="Is it aligned?",
            artifact_refs=["handoff/release-report.html"],
            correlation_id="sprint-01",
        )
    )

    assert result.status == "reviewed"
    assert result.content == "Review says revise."
    assert result.summary_artifact.endswith("summary.md")
    assert result.summary_artifact.startswith("team-lead/codex-review/")
    assert result.summary_artifact in result.artifact_refs
    assert result.log_artifact in result.artifact_refs
    assert result.raw_events_artifact in result.artifact_refs


def test_codex_review_runner_can_resume_existing_session(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _create_run(tmp_path, monkeypatch, run_dir, "run")

    def executor(command, prompt, timeout_seconds, log_path, raw_events_path):
        assert command[-3:] == ["resume", "thread-review", "-"]
        return subprocess.CompletedProcess(command, 0, stdout="Review resumed.", stderr="")

    result = CodexReviewRunner(command_executor=executor).run(
        CodexReviewRequest(
            run_id="run",
            run_dir=run_dir,
            requesting_agent="team-lead-agent",
            target_agent="documentation-handoff-agent",
            purpose="Review report.",
            question="Is it aligned?",
            artifact_refs=["handoff/release-report.html"],
            codex_resume_thread_id="thread-review",
        )
    )

    assert result.status == "reviewed"
    assert result.codex_thread_id == "thread-review"


def test_codex_review_runner_uses_requesting_agent_artifact_owner(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _create_run(tmp_path, monkeypatch, run_dir, "run")

    def executor(command, prompt, timeout_seconds, log_path, raw_events_path):
        return subprocess.CompletedProcess(command, 0, stdout="Head review complete.", stderr="")

    result = CodexReviewRunner(command_executor=executor).run(
        CodexReviewRequest(
            run_id="run",
            run_dir=run_dir,
            requesting_agent="head-agent",
            target_agent="architect-agent",
            purpose="Review architecture.",
            question="Is this ready for PM?",
            artifact_refs=["upstream-planning/architecture.md"],
        )
    )

    assert result.status == "reviewed"
    assert result.summary_artifact.startswith("head/codex-review/")


def test_codex_review_prompt_is_artifact_grounded(tmp_path):
    prompt = build_codex_review_prompt(
        CodexReviewRequest(
            run_id="run",
            run_dir=tmp_path,
            requesting_agent="business-analyst-agent",
            target_agent="architect-agent",
            purpose="Inspect architecture notes.",
            question="What is missing?",
            artifact_refs=["docs/architecture.md"],
        )
    )

    assert "business-analyst-agent" in prompt
    assert "architect-agent" in prompt
    assert "docs/architecture.md" in prompt
    assert "Do not edit" in prompt


def test_codex_review_prompt_is_generic_and_uses_question_as_source_of_truth(tmp_path):
    prompt = build_codex_review_prompt(
        CodexReviewRequest(
            run_id="run",
            run_dir=tmp_path,
            requesting_agent="team-lead-agent",
            target_agent="documentation-handoff-agent",
            purpose="Review handoff.",
            question="Is the handoff business aligned?",
            artifact_refs=[
                "handoff/sprints/sprint-02/release-report.html",
            ],
        )
    )

    assert "Treat the Question and Purpose as the source of truth" in prompt
    assert "Artifact references:" in prompt


def _create_run(tmp_path, monkeypatch, run_dir, run_uid):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email=f"{run_uid}@example.test",
        username=f"user-{run_uid}",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Review",
        request_text="Review",
        mode="internal_tool",
        complexity="simple",
        status="running",
    )
    repo.create_run(
        project_id=project.id,
        run_uid=run_uid,
        run_dir=run_dir,
        status="running",
        mode="internal_tool",
        reasoning="medium",
    )
