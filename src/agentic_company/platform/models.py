"""Platform-level data contracts shared across specialist agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ExecutionRequest:
    run_id: str
    agent_id: str
    agent_version: str
    maturity_level: str
    provider: str
    model: str
    target_project_dir: str
    input_artifacts: list[str]
    expected_outputs: list[str]
    instructions: list[str]
    constraints: list[str]
    work_item: dict[str, Any] = field(default_factory=dict)
    completed_work_item_ids: list[str] = field(default_factory=list)
    execution_id: str = ""
    execution_intent: str = ""
    parent_message_id: str = ""
    codex_resume_thread_id: str = ""
    handoff_scope: str = ""
    handoff_sprint_id: str = ""
    handoff_output_dir: str = ""
    handoff_expected_outputs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class AgentRunResult:
    agent_id: str
    status: str
    output_artifacts: list[str]
    summary: str
    execution_id: str = ""
    codex_thread_id: str = ""
    blocking_findings: list[str] = field(default_factory=list)
    fix_request_artifacts: list[str] = field(default_factory=list)
    recommended_next_action: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
