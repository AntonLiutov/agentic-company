"""Static and lightweight QA checks for generated projects."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from agentic_company.agents.quality.files import iter_project_files, read_text_safely
from agentic_company.agents.quality.models import QualityCheckResult

LOGGER = logging.getLogger(__name__)


def check_expected_outputs(
    target_dir: Path,
    expected_outputs: list[str],
) -> list[QualityCheckResult]:
    return [
        QualityCheckResult(
            name=f"Expected output: {output}",
            status="passed" if (target_dir / output).exists() else "failed",
            command=[],
            exit_code=None,
            details=(
                "File exists in generated project."
                if (target_dir / output).exists()
                else "Expected file is missing from generated project."
            ),
        )
        for output in expected_outputs
    ]


def check_no_secrets(target_dir: Path) -> QualityCheckResult:
    findings: list[str] = []
    secret_patterns = [
        re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
        re.compile(r"(?i)(api[_-]?key|token|secret)\s*=\s*['\"][^'\"]{8,}['\"]"),
    ]
    for path in iter_project_files(target_dir):
        text = read_text_safely(path)
        if not text:
            continue
        if any(pattern.search(text) for pattern in secret_patterns):
            findings.append(str(path.relative_to(target_dir)))

    result = QualityCheckResult(
        name="Secret scan",
        status="failed" if findings else "passed",
        command=[],
        exit_code=None,
        details=(
            "Potential secrets found in generated files: " + ", ".join(findings)
            if findings
            else "No obvious secret values found outside ignored local files."
        ),
    )
    LOGGER.info("QA check completed name=%s status=%s", result.name, result.status)
    return result


def check_readme_operational_docs(target_dir: Path) -> QualityCheckResult:
    readme_path = target_dir / "README.md"
    if not readme_path.exists():
        return QualityCheckResult(
            name="README operational docs",
            status="failed",
            command=[],
            exit_code=None,
            details="README.md is missing.",
        )

    readme = read_text_safely(readme_path).lower()
    required_terms = ["uv", "docker", "openai_api_key", "default_model"]
    missing = [term for term in required_terms if term not in readme]
    return QualityCheckResult(
        name="README operational docs",
        status="failed" if missing else "passed",
        command=[],
        exit_code=None,
        details=(
            "README is missing operational terms: " + ", ".join(missing)
            if missing
            else "README explains uv, Docker, and required environment variables."
        ),
    )
