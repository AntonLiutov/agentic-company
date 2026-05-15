"""QA report rendering."""

from __future__ import annotations

import json
from pathlib import Path

from agentic_company.agents.quality.models import QualityCheckResult, QualityTestPlanItem


def render_qa_report(
    run_dir: Path,
    target_dir: Path,
    checks: list[QualityCheckResult],
    status: str,
    test_plan: list[QualityTestPlanItem],
) -> str:
    intake = _read_json(run_dir / "01-intake-brief.json")
    criteria = _list_items(intake.get("acceptance_criteria", []))
    plan_rows = "\n".join(
        f"| {item.stage} | {item.name} | {'yes' if item.required else 'optional'} | {item.intent} |"
        for item in test_plan
    )
    check_rows = "\n".join(
        f"| {check.status} | {check.name} | {check.details} |" for check in checks
    )
    coverage_summary = _coverage_summary(checks, status)
    docker_summary = _docker_summary(run_dir / "qa" / "docker" / "build-summary.json")

    return f"""# QA Report

Status: {status}

## Acceptance Criteria

{criteria}

## Test Plan

| Stage | Check | Required | Intent |
| --- | --- | --- | --- |
{plan_rows}

## Automated Checks

| Status | Check | Evidence |
| --- | --- | --- |
{check_rows}

## Coverage Summary

{coverage_summary}

## Docker Build Observability

{docker_summary}

## Evidence

- Test plan: `qa/test-plan.json`
- Structured results: `qa/results.json`
- Command log: `qa/commands.log`
- Browser transcript: `qa/browser/chat-transcript.json`
- Docker browser transcript: `qa/browser/docker-chat-transcript.json`
- Browser screenshots: `qa/screenshots/playwright-before-chat.png`,
  `qa/screenshots/playwright-chat.png`
- Docker screenshots: `qa/screenshots/docker-before-chat.png`, `qa/screenshots/docker-chat.png`
- Docker logs: `qa/docker/compose.log`
- Docker runtime command log: `qa/docker/runtime-command.log`
- Docker build summary: `qa/docker/build-summary.json`
- Generated QA scripts: `qa/scripts/docker_runtime_e2e.py`,
  `qa/scripts/playwright_live_chat_e2e.py`

## Manual Follow-up

- Inspect browser screenshots and transcript if live chat QA fails.
- If browser dependency setup fails, verify network access for Playwright package and Chromium
  install.
- If Docker build was intentionally skipped, run `docker compose up --build` before deployment.
"""


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _list_items(value: object) -> str:
    if not isinstance(value, list) or not value:
        return "- None"
    return "\n".join(f"- {item}" for item in value)


def _coverage_summary(checks: list[QualityCheckResult], status: str) -> str:
    passed = {check.name for check in checks if check.status == "passed"}
    failed = [check.name for check in checks if check.status == "failed"]
    skipped = [check.name for check in checks if check.status == "skipped"]

    proven: list[str] = []
    if any(name.startswith("Expected output:") for name in passed):
        proven.append("Expected project artifacts were generated.")
    if "Secret scan" in passed:
        proven.append("No obvious secret values were found in generated text artifacts.")
    if "README operational docs" in passed:
        proven.append("Generated README explains local setup, Docker setup, and required env vars.")
    if {"Dependency sync", "Python compile", "Streamlit AppTest"} <= passed:
        proven.append(
            "The generated app installs, compiles, and handles missing/configured env paths."
        )
    if {"Docker Compose config", "Docker runtime E2E"} <= passed:
        proven.append("The generated app builds and runs through Docker Compose.")
    if "Playwright live chat E2E" in passed:
        proven.append("A browser can send a real chat prompt and receive an assistant response.")

    not_covered = [
        "Cross-browser matrix beyond the configured Playwright Chromium smoke path.",
        "Mobile, accessibility, visual regression, load, and cloud deployment validation.",
        "Automatic QA failure repair; failed runs still need a human or future fix loop.",
    ]

    lines = ["**Proven by this run:**", *[f"- {item}" for item in proven or ["No checks passed."]]]
    if failed:
        lines.extend(["", "**Failed checks:**", *[f"- {name}" for name in failed]])
    if skipped:
        lines.extend(["", "**Skipped checks:**", *[f"- {name}" for name in skipped]])
    if status == "passed":
        lines.extend(["", "**Not covered yet:**", *[f"- {item}" for item in not_covered]])
    return "\n".join(lines)


def _docker_summary(path: Path) -> str:
    if not path.exists():
        return "- Docker runtime summary was not available."

    summary = _read_json(path)
    if summary.get("status") != "available":
        return f"- {summary.get('reason', 'Docker runtime summary was not available.')}"

    slowest = summary.get("slowest_step")
    if not isinstance(slowest, dict):
        return "- Docker runtime log was available, but no BuildKit steps were detected."

    observations = summary.get("observations", [])
    observation_lines = (
        [f"- {item}" for item in observations]
        if isinstance(observations, list)
        else ["- No Docker build observations were recorded."]
    )
    dependency_seconds = float(summary.get("dependency_sync_seconds") or 0)
    return "\n".join(
        [
            f"- Slowest Docker step: `{slowest.get('label', 'unknown')}` "
            f"({float(slowest.get('seconds') or 0):.1f}s).",
            f"- Dependency sync time: {dependency_seconds:.1f}s.",
            f"- Cached Docker steps observed: {summary.get('cached_steps', 0)}.",
            *observation_lines,
        ]
    )
