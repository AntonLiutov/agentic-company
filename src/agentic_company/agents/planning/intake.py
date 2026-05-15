"""Deterministic intake parsing for the first runnable pipeline."""

from __future__ import annotations

from pathlib import Path

from agentic_company.agents.planning.models import IntakeBrief

SECTION_KEYS = {
    "goal": "goal",
    "target user": "target_user",
    "core features": "core_features",
    "required configuration": "required_configuration",
    "preferred stack": "preferred_stack",
    "non-goals": "non_goals",
    "acceptance criteria": "acceptance_criteria",
}


def parse_requirements(path: Path) -> IntakeBrief:
    """Parse the small markdown requirements format used by the MVP pipeline."""
    text = path.read_text(encoding="utf-8")
    fields: dict[str, object] = {
        "project_name": path.stem.replace("-", " ").title(),
        "goal": "",
        "target_user": "",
        "core_features": [],
        "required_configuration": [],
        "preferred_stack": [],
        "non_goals": [],
        "acceptance_criteria": [],
    }

    active_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.lower().startswith("project name:"):
            fields["project_name"] = line.split(":", 1)[1].strip()
            active_key = None
            continue

        normalized = line.rstrip(":").lower()
        if normalized in SECTION_KEYS:
            active_key = SECTION_KEYS[normalized]
            continue

        if active_key is None:
            continue

        if line.startswith("- "):
            value = line[2:].strip()
            values = fields[active_key]
            if isinstance(values, list):
                values.append(value)
            continue

        current = fields[active_key]
        if isinstance(current, str):
            fields[active_key] = f"{current} {line}".strip()

    return IntakeBrief(
        project_name=str(fields["project_name"]),
        source_path=str(path),
        goal=str(fields["goal"]),
        target_user=str(fields["target_user"]),
        core_features=list(fields["core_features"]),
        required_configuration=list(fields["required_configuration"]),
        preferred_stack=list(fields["preferred_stack"]),
        non_goals=list(fields["non_goals"]),
        acceptance_criteria=list(fields["acceptance_criteria"]),
        open_questions=_open_questions(fields),
    )


def _open_questions(fields: dict[str, object]) -> list[str]:
    questions: list[str] = []
    if not fields["goal"]:
        questions.append("What is the primary goal of the application?")
    if not fields["target_user"]:
        questions.append("Who is the target user?")
    if not fields["acceptance_criteria"]:
        questions.append("What acceptance criteria define MVP completion?")
    return questions
