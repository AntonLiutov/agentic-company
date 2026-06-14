"""Provider-neutral worker execution port."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class UsageTotals:
    """Token usage totals reported by a worker provider."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class WorkRequest:
    """Provider-neutral request to run one worker backend."""

    run_dir: Path
    agent_id: str
    work_item_id: str = ""
    stage: str = ""
    execution_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkResult:
    """Provider-neutral worker result."""

    success: bool
    summary: str
    output_artifacts: list[str]
    error: str = ""
    worker_session_id: str = ""
    provider: str = ""
    usage: UsageTotals | None = None
    status: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkerPort(Protocol):
    """Swappable worker backend boundary."""

    def run(self, request: WorkRequest) -> WorkResult:
        """Run one worker request and return a provider-neutral result."""
