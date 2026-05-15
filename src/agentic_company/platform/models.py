"""Platform-level data contracts shared across specialist agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass


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
