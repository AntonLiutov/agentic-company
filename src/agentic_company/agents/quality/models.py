"""Quality agent data structures for QA checks."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

CommandExecutor = Callable[
    [Sequence[str], Path, int],
    subprocess.CompletedProcess[str],
]


@dataclass(slots=True)
class QualityCheckResult:
    name: str
    status: str
    command: list[str]
    exit_code: int | None
    details: str
    output: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class QualityTestPlanItem:
    name: str
    stage: str
    intent: str
    required: bool = True

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
