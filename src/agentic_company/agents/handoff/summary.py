"""Handoff summary artifacts for generated projects."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agentic_company.agents.quality.fix_request import FIX_REQUEST_JSON, FIX_REQUEST_MARKDOWN
from agentic_company.platform.events import write_event

LOGGER = logging.getLogger(__name__)

HANDOFF_SUMMARY_MARKDOWN = "09-handoff-summary.md"


def write_handoff_summary(run_dir: Path, target_dir: Path, run_id: str) -> str:
    event_log = run_dir / "events.jsonl"
    handoff_path = run_dir / HANDOFF_SUMMARY_MARKDOWN
    write_event(
        event_log,
        run_id,
        "documentation-handoff-agent",
        "handoff_started",
        {"target_project_dir": str(target_dir)},
    )
    handoff = render_handoff_summary(run_dir, target_dir)
    handoff_path.write_text(handoff, encoding="utf-8")
    LOGGER.info("Handoff summary written run_id=%s artifact=%s", run_id, handoff_path.name)
    write_event(
        event_log,
        run_id,
        "documentation-handoff-agent",
        "artifact_written",
        {"artifact": handoff_path.name},
    )
    write_event(
        event_log,
        run_id,
        "documentation-handoff-agent",
        "handoff_ready",
        {"artifact": handoff_path.name},
    )
    return handoff_path.name


def render_handoff_summary(run_dir: Path, target_dir: Path) -> str:
    intake = _read_json(run_dir / "01-intake-brief.json")
    project_name = str(intake.get("project_name", target_dir.name))
    required_config = _list_items(intake.get("required_configuration", []))
    generated_files = "\n".join(
        f"- `{path.relative_to(target_dir)}`"
        for path in sorted(target_dir.rglob("*"))
        if _is_handoff_file(target_dir, path)
    )
    fix_request = _fix_request_summary(run_dir)
    deployment = _deployment_summary(run_dir)

    handoff_status = _handoff_status(run_dir)

    return f"""# Handoff Summary

Status: {handoff_status}

Project: {project_name}

## Generated Project

`{target_dir}`

## Required Configuration

{required_config}

## How To Run

```powershell
Copy-Item .env.example .env
uv sync
uv run streamlit run app.py
```

If Docker artifacts are present:

```powershell
docker compose up --build
```

## Generated Files

{generated_files or "- No generated files found."}

## Fix Request

{fix_request}

## Deployment

{deployment}

## Next Steps

- Share the public URL from `13-deployment-summary.md`.
- Inspect the post-deployment chatbot QA evidence if the deployed app behaves unexpectedly.
- If QA failed, inspect `10-fix-request.md` and send it back to the Fullstack Agent as
  the next Codex repair input.
- Capture any new product issues as follow-up requirements for the next planning run.
"""


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _list_items(value: object) -> str:
    if not isinstance(value, list) or not value:
        return "- None"
    return "\n".join(f"- {item}" for item in value)


def _is_handoff_file(target_dir: Path, path: Path) -> bool:
    if not path.is_file():
        return False
    relative_parts = path.relative_to(target_dir).parts
    ignored_parts = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
    }
    if any(part in ignored_parts for part in relative_parts):
        return False
    return path.name != ".env"


def _fix_request_summary(run_dir: Path) -> str:
    markdown_path = run_dir / FIX_REQUEST_MARKDOWN
    json_path = run_dir / FIX_REQUEST_JSON
    if not markdown_path.exists() and not json_path.exists():
        return "- No fix request was created because QA did not fail."
    return (
        f"- Fix request created: `{FIX_REQUEST_MARKDOWN}`\n"
        f"- Structured repair input: `{FIX_REQUEST_JSON}`"
    )


def _deployment_summary(run_dir: Path) -> str:
    deployment_summary_path = run_dir / "13-deployment-summary.md"
    if deployment_summary_path.exists():
        status = _markdown_status(deployment_summary_path)
        public_url = _markdown_public_url(deployment_summary_path)
        lines = [f"- Deployment status: `{status or 'unknown'}`"]
        if public_url:
            lines.append(f"- Public URL: {public_url}")
        else:
            lines.append("- Public URL: not available")
        lines.append("- Deployment summary: `13-deployment-summary.md`")
        return "\n".join(lines)

    plan_path = run_dir / "11-deployment-plan.json"
    if not plan_path.exists():
        return "- Deployment plan was not generated."
    payload = _read_json(plan_path)
    readiness = payload.get("readiness", "unknown")
    target = payload.get("recommended_target", "unknown")
    return (
        "- Deployment status: `not deployed yet`\n"
        f"- Deployment readiness: `{readiness}`\n"
        f"- Recommended target: `{target}`\n"
        "- Detailed plan: `11-deployment-plan.md`\n"
        "- Deployment request: `12-deployment-request.md`\n"
        "- Public URL: not available until deployment completes."
    )


def _deployment_succeeded(run_dir: Path) -> bool:
    deployment_summary_path = run_dir / "13-deployment-summary.md"
    return (
        deployment_summary_path.exists() and _markdown_status(deployment_summary_path) == "deployed"
    )


def _handoff_status(run_dir: Path) -> str:
    deployment_summary_path = run_dir / "13-deployment-summary.md"
    if not deployment_summary_path.exists():
        return "blocked_pending_deployment"
    deployment_status = _markdown_status(deployment_summary_path)
    if deployment_status == "deployed":
        return "ready_with_deployment"
    return f"blocked_deployment_{deployment_status or 'needs_attention'}"


def _markdown_status(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Status:"):
            return line.split(":", 1)[1].strip()
    return ""


def _markdown_public_url(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "Public URL:" and index + 1 < len(lines):
            value = lines[index + 1].strip()
            return "" if value == "not available" else value
    return ""
