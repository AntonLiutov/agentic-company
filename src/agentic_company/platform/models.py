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
    project_archetype: str = "single-service-streamlit"
    feature_queue: list[dict[str, Any]] = field(default_factory=list)
    active_feature: dict[str, Any] | None = None
    completed_feature_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class AgentRunResult:
    agent_id: str
    status: str
    output_artifacts: list[str]
    summary: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
