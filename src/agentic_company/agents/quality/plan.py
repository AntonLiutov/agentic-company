"""QA test planning for generated projects."""

from __future__ import annotations

from agentic_company.agents.quality.models import QualityTestPlanItem


def build_test_plan(expected_outputs: list[str]) -> list[QualityTestPlanItem]:
    return [
        QualityTestPlanItem(
            name="Expected artifact inventory",
            stage="artifact",
            intent=f"Confirm generated project includes {len(expected_outputs)} expected files.",
        ),
        QualityTestPlanItem(
            name="Secret scan",
            stage="security",
            intent="Check generated text files for obvious committed API keys or tokens.",
        ),
        QualityTestPlanItem(
            name="README operational docs",
            stage="handoff",
            intent="Confirm generated documentation explains uv, Docker, and required env vars.",
        ),
        QualityTestPlanItem(
            name="Dependency sync",
            stage="build",
            intent="Install generated project dependencies from the lock file with uv.",
        ),
        QualityTestPlanItem(
            name="Python compile",
            stage="static",
            intent="Compile the generated Streamlit app entrypoint.",
        ),
        QualityTestPlanItem(
            name="Streamlit AppTest",
            stage="framework",
            intent="Run the generated app with missing and configured environment paths.",
        ),
        QualityTestPlanItem(
            name="Docker Compose config",
            stage="container",
            intent="Validate generated Docker Compose syntax and service configuration.",
        ),
        QualityTestPlanItem(
            name="Docker runtime E2E",
            stage="container",
            intent=(
                "Build and run the generated Docker Compose app, then verify the chat flow "
                "through a browser against the containerized service."
            ),
        ),
        QualityTestPlanItem(
            name="Playwright live chat E2E",
            stage="browser",
            intent=(
                "Launch the generated app, send a real chat prompt, wait for an assistant "
                "response, and capture browser evidence."
            ),
        ),
    ]
