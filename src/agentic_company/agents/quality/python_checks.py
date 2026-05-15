"""Python, dependency, and Streamlit AppTest checks."""

from __future__ import annotations

import shutil
import sys
import textwrap
from pathlib import Path

from agentic_company.agents.quality.commands import run_command_check, skipped_check
from agentic_company.agents.quality.models import CommandExecutor, QualityCheckResult


def run_uv_sync(
    target_dir: Path,
    *,
    command_executor: CommandExecutor | None,
    timeout_seconds: int,
    commands_log_path: Path,
) -> QualityCheckResult:
    if not (target_dir / "pyproject.toml").exists():
        return skipped_check("Dependency sync", "No pyproject.toml was generated.")
    if not (target_dir / "uv.lock").exists():
        return skipped_check("Dependency sync", "No uv.lock was generated.")
    if not shutil.which("uv"):
        return skipped_check("Dependency sync", "`uv` is not available on PATH.")
    return run_command_check(
        "Dependency sync",
        ["uv", "sync", "--frozen"],
        target_dir,
        command_executor=command_executor,
        timeout_seconds=timeout_seconds,
        commands_log_path=commands_log_path,
    )


def run_python_compile(
    target_dir: Path,
    *,
    command_executor: CommandExecutor | None,
    timeout_seconds: int,
    commands_log_path: Path,
) -> QualityCheckResult:
    if not (target_dir / "app.py").exists():
        return skipped_check("Python compile", "No app.py was generated.")

    if (target_dir / "pyproject.toml").exists() and shutil.which("uv"):
        command = ["uv", "run", "python", "-m", "py_compile", "app.py"]
    else:
        command = [sys.executable, "-m", "py_compile", str(target_dir / "app.py")]

    return run_command_check(
        "Python compile",
        command,
        target_dir,
        command_executor=command_executor,
        timeout_seconds=timeout_seconds,
        commands_log_path=commands_log_path,
    )


def run_streamlit_apptest(
    target_dir: Path,
    *,
    command_executor: CommandExecutor | None,
    timeout_seconds: int,
    commands_log_path: Path,
) -> QualityCheckResult:
    if not (target_dir / "app.py").exists():
        return skipped_check("Streamlit AppTest", "No app.py was generated.")
    if not (target_dir / "pyproject.toml").exists():
        return skipped_check("Streamlit AppTest", "No pyproject.toml was generated.")
    if not shutil.which("uv"):
        return skipped_check("Streamlit AppTest", "`uv` is not available on PATH.")

    script = textwrap.dedent(
        """
        import os

        from streamlit.testing.v1 import AppTest

        APP_TEST_TIMEOUT_SECONDS = 60


        def run_case(name):
            at = AppTest.from_file("app.py")
            at.run(timeout=APP_TEST_TIMEOUT_SECONDS)
            assert not at.exception, f"{name}: {at.exception}"
            print(f"{name}: ok")


        os.environ.pop("OPENAI_API_KEY", None)
        run_case("missing_api_key")

        os.environ["OPENAI_API_KEY"] = "test-key-for-qa"
        os.environ.setdefault("DEFAULT_MODEL", "gpt-4o-mini")
        run_case("configured_environment")
        """
    ).strip()
    return run_command_check(
        "Streamlit AppTest",
        ["uv", "run", "python", "-c", script],
        target_dir,
        command_executor=command_executor,
        timeout_seconds=timeout_seconds,
        commands_log_path=commands_log_path,
    )
