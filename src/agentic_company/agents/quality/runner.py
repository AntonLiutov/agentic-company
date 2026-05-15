"""Compatibility facade for the Quality Agent LangGraph workflow."""

from __future__ import annotations

from pathlib import Path

from agentic_company.agents.quality.legacy_workflow import (
    QA_COMMANDS_LOG_FILENAME,
    QA_DOCKER_SUMMARY_FILENAME,
    QA_REPORT_FILENAME,
    QA_RESULTS_FILENAME,
    QA_TEST_PLAN_FILENAME,
    run_qa_checks,
    run_quality_workflow_graph,
    summarize_status,
)
from agentic_company.agents.quality.models import CommandExecutor
from agentic_company.platform.models import AgentRunResult


class QualityRunner:
    """Run local checks against the generated project and write QA evidence."""

    qa_filename = QA_REPORT_FILENAME
    test_plan_filename = QA_TEST_PLAN_FILENAME
    results_filename = QA_RESULTS_FILENAME
    commands_log_filename = QA_COMMANDS_LOG_FILENAME
    docker_summary_filename = QA_DOCKER_SUMMARY_FILENAME

    def __init__(
        self,
        *,
        command_executor: CommandExecutor | None = None,
        command_timeout_seconds: int = 300,
        force: bool = False,
    ) -> None:
        self.command_executor = command_executor
        self.command_timeout_seconds = command_timeout_seconds
        self.force = force

    def run(self, run_dir: Path) -> AgentRunResult:
        return run_quality_workflow_graph(
            run_dir,
            command_executor=self.command_executor,
            command_timeout_seconds=self.command_timeout_seconds,
            force=self.force,
        )


__all__ = [
    "QualityRunner",
    "run_qa_checks",
    "summarize_status",
]
