"""Docker runtime QA log summarization."""

from __future__ import annotations

import json
import re
from pathlib import Path


def write_docker_build_summary(run_dir: Path) -> Path:
    """Write a compact summary of the Docker runtime QA build log."""

    summary_path = run_dir / "qa" / "docker" / "build-summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary = summarize_docker_runtime_log(run_dir / "qa" / "docker" / "runtime-command.log")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary_path


def summarize_docker_runtime_log(log_path: Path) -> dict[str, object]:
    if not log_path.exists():
        return {
            "status": "not_available",
            "reason": "Docker runtime command log was not written.",
            "steps": [],
            "observations": [],
        }

    text = log_path.read_text(encoding="utf-8", errors="replace")
    step_labels: dict[str, str] = {}
    steps: list[dict[str, object]] = []
    downloaded_packages: list[str] = []
    prepared_packages = ""
    cached_steps = 0

    for line in text.splitlines():
        label_match = re.match(r"^(#\d+)\s+\[(.+?)\]\s+(.+)$", line)
        if label_match:
            step_id, stage, label = label_match.groups()
            step_labels[step_id] = f"[{stage}] {label}"
            continue

        if " CACHED" in line:
            cached_steps += 1

        download_match = re.match(r"^#\d+\s+[\d.]+\s+Downloaded\s+(.+)$", line)
        if download_match:
            downloaded_packages.append(download_match.group(1).strip())
            continue

        prepared_match = re.match(r"^#\d+\s+[\d.]+\s+Prepared\s+(.+)$", line)
        if prepared_match:
            prepared_packages = prepared_match.group(1).strip()
            continue

        done_match = re.match(r"^(#\d+)\s+DONE\s+([\d.]+)s$", line)
        if done_match:
            step_id, seconds = done_match.groups()
            steps.append(
                {
                    "id": step_id,
                    "label": step_labels.get(step_id, step_id),
                    "seconds": float(seconds),
                }
            )

    dependency_steps = [
        step
        for step in steps
        if "uv sync" in str(step["label"]) or "dependency" in str(step["label"]).lower()
    ]
    slowest_step = max(steps, key=lambda step: float(step["seconds"]), default=None)
    dependency_seconds = max(
        (float(step["seconds"]) for step in dependency_steps),
        default=0.0,
    )
    observations = _build_observations(
        text,
        dependency_seconds=dependency_seconds,
        downloaded_packages=downloaded_packages,
        cached_steps=cached_steps,
    )

    return {
        "status": "available",
        "steps": steps,
        "slowest_step": slowest_step,
        "dependency_sync_seconds": dependency_seconds,
        "downloaded_packages": downloaded_packages,
        "prepared_packages": prepared_packages,
        "cached_steps": cached_steps,
        "observations": observations,
    }


def _build_observations(
    text: str,
    *,
    dependency_seconds: float,
    downloaded_packages: list[str],
    cached_steps: int,
) -> list[str]:
    observations: list[str] = []
    if dependency_seconds >= 300:
        observations.append("Docker dependency sync dominated runtime QA; inspect uv cache reuse.")
    if downloaded_packages:
        observations.append(
            "Docker build downloaded packages during QA: "
            + ", ".join(downloaded_packages[:8])
            + ("." if len(downloaded_packages) <= 8 else ", ...")
        )
    if "--mount=type=cache,target=/root/.cache/uv" not in text and dependency_seconds >= 30:
        observations.append(
            "Docker build output did not show the uv BuildKit cache mount guidance."
        )
    if cached_steps:
        observations.append(f"Docker reused {cached_steps} cached build step(s).")
    if not observations:
        observations.append("No obvious Docker build bottleneck was detected.")
    return observations
