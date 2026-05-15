"""Create structured repair requests from failed QA evidence."""

from __future__ import annotations

import json
from pathlib import Path

from agentic_company.platform.models import ExecutionRequest

FIX_REQUEST_JSON = "10-fix-request.json"
FIX_REQUEST_MARKDOWN = "10-fix-request.md"


def write_fix_request(run_dir: Path, request: ExecutionRequest) -> list[str]:
    """Write repair request artifacts for a failed QA run."""

    qa_results = _read_json(run_dir / "qa" / "results.json")
    failed_checks = [
        _fix_check_payload(check)
        for check in qa_results.get("checks", [])
        if isinstance(check, dict) and check.get("status") == "failed"
    ]
    evidence = _available_evidence(run_dir)
    payload = {
        "run_id": request.run_id,
        "target_project_dir": request.target_project_dir,
        "requested_by": "qa-agent",
        "assigned_to": request.agent_id,
        "provider": request.provider,
        "model": request.model,
        "status": "fix_requested",
        "source": {
            "qa_report": "08-qa-report.md",
            "qa_results": "qa/results.json",
            "execution_request": "06-execution-request.json",
        },
        "failed_checks": failed_checks,
        "evidence": evidence,
        "instructions": [
            "Inspect the failed QA checks and linked evidence before editing.",
            "Modify only the generated project unless the failure proves the runner is wrong.",
            "Preserve run-local .env values and never print or copy secrets.",
            "After the fullstack fix, rerun QA with force enabled.",
        ],
        "expected_outputs": [
            "Updated generated project files",
            "Updated execution summary or fix summary",
            "Passing QA report after rerun",
        ],
    }

    json_path = run_dir / FIX_REQUEST_JSON
    markdown_path = run_dir / FIX_REQUEST_MARKDOWN
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(payload), encoding="utf-8")
    return [FIX_REQUEST_JSON, FIX_REQUEST_MARKDOWN]


def _fix_check_payload(check: dict[str, object]) -> dict[str, object]:
    return {
        "name": check.get("name", ""),
        "details": check.get("details", ""),
        "command": check.get("command", []),
        "exit_code": check.get("exit_code"),
        "output_excerpt": _excerpt(str(check.get("output", ""))),
    }


def _available_evidence(run_dir: Path) -> list[str]:
    candidates = [
        "08-qa-report.md",
        "qa/results.json",
        "qa/commands.log",
        "qa/docker/build-summary.json",
        "qa/docker/runtime-command.log",
        "qa/docker/compose.log",
        "qa/browser/chat-transcript.json",
        "qa/browser/docker-chat-transcript.json",
        "qa/screenshots/playwright-before-chat.png",
        "qa/screenshots/playwright-chat.png",
        "qa/screenshots/docker-before-chat.png",
        "qa/screenshots/docker-chat.png",
    ]
    return [relative for relative in candidates if (run_dir / relative).exists()]


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _excerpt(value: str, *, max_chars: int = 1200) -> str:
    stripped = value.strip()
    if len(stripped) <= max_chars:
        return stripped
    return stripped[:max_chars].rstrip() + "\n...[truncated]"


def _render_markdown(payload: dict[str, object]) -> str:
    failed_checks = payload.get("failed_checks")
    failed_lines = []
    if isinstance(failed_checks, list):
        for check in failed_checks:
            if not isinstance(check, dict):
                continue
            failed_lines.append(f"- {check.get('name', 'unknown')}: {check.get('details', '')}")
    evidence = payload.get("evidence")
    evidence_lines = [f"- `{item}`" for item in evidence] if isinstance(evidence, list) else []
    instructions = payload.get("instructions")
    instruction_lines = (
        [f"- {item}" for item in instructions] if isinstance(instructions, list) else []
    )

    return f"""# QA Repair Request For Fullstack Agent

Status: {payload.get("status", "fix_requested")}

Requested by:
`{payload.get("requested_by", "")}`

Assigned to:
`{payload.get("assigned_to", "")}`

Target project:
`{payload.get("target_project_dir", "")}`

## Failed Checks

{chr(10).join(failed_lines) or "- No failed checks were found."}

## Evidence To Inspect

{chr(10).join(evidence_lines) or "- No evidence artifacts were found."}

## Instructions

{chr(10).join(instruction_lines) or "- Inspect QA evidence and repair the generated project."}
"""
