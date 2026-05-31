import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

from agentic_company.agents.deployment.codex_cli import (
    DeploymentCodexRunner,
    build_deployment_codex_prompt,
    read_deployment_contract,
)
from agentic_company.platform.artifacts import load_execution_request
from agentic_company.platform.state import DELIVERY_STATE_SNAPSHOT


def test_deployment_codex_runner_accepts_contract_artifacts(tmp_path):
    run_dir = _create_run(tmp_path)

    def executor(command: Sequence[str], prompt: str, timeout: int, log: Path, raw: Path):
        (run_dir / "deployment").mkdir(parents=True, exist_ok=True)
        (run_dir / "deployment" / "result.json").write_text(
            json.dumps(
                {
                    "status": "deployed",
                    "target_environment": "azure-container-apps-dev",
                    "topology_summary": "Codex inferred two compose services.",
                    "deployment_targets": [
                        {
                            "service": "web",
                            "runtime": "container-app",
                            "image": "agentic-web:latest",
                            "public_url": "https://web.example.com",
                        }
                    ],
                    "public_urls": ["https://web.example.com"],
                    "post_deploy_qa_targets": ["https://web.example.com"],
                    "actions_performed": [],
                    "blockers": [],
                    "risks": [],
                }
            ),
            encoding="utf-8",
        )
        for path in [
            "11-deployment-plan.json",
            "12-deployment-request.json",
        ]:
            (run_dir / path).write_text('{"status":"deployed"}', encoding="utf-8")
        for path in [
            "11-deployment-plan.md",
            "12-deployment-request.md",
            "13-deployment-summary.md",
        ]:
            (run_dir / path).write_text("# Deployment\n", encoding="utf-8")
        summary = Path(command[-2])
        summary.write_text(
            "Deployment complete.\n\nDEPLOYMENT_STATUS: deployed\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = DeploymentCodexRunner(command_executor=executor).run(run_dir)

    assert result.agent_id == "deployment-codex-agent"
    assert result.status == "deployment_deployed"
    assert "deployment/result.json" in result.output_artifacts
    assert "13-deployment-summary.md" in result.output_artifacts


def test_deployment_codex_runner_fails_when_contract_missing(tmp_path):
    run_dir = _create_run(tmp_path)

    def executor(command: Sequence[str], prompt: str, timeout: int, log: Path, raw: Path):
        summary = Path(command[-2])
        summary.write_text("I forgot the required status.\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = DeploymentCodexRunner(command_executor=executor, contract_attempts=1).run(run_dir)

    assert result.status == "deployment_failed"
    payload = json.loads((run_dir / "deployment" / "result.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["contract_errors"]


def test_deployment_codex_runner_recovers_artifacts_from_generated_project(tmp_path):
    run_dir = _create_run(tmp_path)
    request = load_execution_request(run_dir)
    target_dir = Path(request.target_project_dir)

    def executor(command: Sequence[str], prompt: str, timeout: int, log: Path, raw: Path):
        (target_dir / "deployment").mkdir(parents=True, exist_ok=True)
        (target_dir / "deployment" / "result.json").write_text(
            json.dumps(
                {
                    "status": "blocked",
                    "target_environment": "azure-container-apps-dev",
                    "topology_summary": "Deployment blocked by missing Azure login.",
                    "deployment_targets": [],
                    "public_urls": [],
                    "post_deploy_qa_targets": [],
                    "resource_changes": [],
                    "actions_performed": [],
                    "blockers": ["Azure login is required."],
                    "risks": [],
                }
            ),
            encoding="utf-8",
        )
        for path in [
            "11-deployment-plan.json",
            "12-deployment-request.json",
        ]:
            (target_dir / path).write_text('{"status":"blocked"}', encoding="utf-8")
        for path in [
            "11-deployment-plan.md",
            "12-deployment-request.md",
            "13-deployment-summary.md",
        ]:
            (target_dir / path).write_text("# Deployment\n", encoding="utf-8")
        summary = Path(command[-2])
        summary.write_text("Deployment blocked.\n\nDEPLOYMENT_STATUS: blocked\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = DeploymentCodexRunner(command_executor=executor, contract_attempts=1).run(run_dir)

    assert result.status == "deployment_blocked"
    assert (run_dir / "deployment" / "result.json").exists()
    assert (run_dir / "13-deployment-summary.md").exists()


def test_deployment_codex_runner_recovers_contract_artifacts_from_generated_deployment_folder(
    tmp_path,
):
    run_dir = _create_run(tmp_path)
    request = load_execution_request(run_dir)
    target_dir = Path(request.target_project_dir)

    def executor(command: Sequence[str], prompt: str, timeout: int, log: Path, raw: Path):
        (target_dir / "deployment").mkdir(parents=True, exist_ok=True)
        (target_dir / "deployment" / "result.json").write_text(
            json.dumps(
                {
                    "status": "deployed",
                    "target_environment": "azure-container-apps-dev",
                    "topology_summary": "Deployment succeeded from generated-project fallback.",
                    "deployment_targets": [],
                    "public_urls": ["https://web.example.com"],
                    "post_deploy_qa_targets": ["https://web.example.com"],
                    "resource_changes": [],
                    "actions_performed": [],
                    "blockers": [],
                    "risks": [],
                }
            ),
            encoding="utf-8",
        )
        for path in [
            "11-deployment-plan.json",
            "12-deployment-request.json",
        ]:
            (target_dir / "deployment" / path).write_text('{"status":"deployed"}', encoding="utf-8")
        for path in [
            "11-deployment-plan.md",
            "12-deployment-request.md",
            "13-deployment-summary.md",
        ]:
            (target_dir / "deployment" / path).write_text("# Deployment\n", encoding="utf-8")
        summary = Path(command[-2])
        summary.write_text(
            "Deployment complete.\n\nDEPLOYMENT_STATUS: deployed\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = DeploymentCodexRunner(command_executor=executor, contract_attempts=1).run(run_dir)

    assert result.status == "deployment_deployed"
    assert (run_dir / "deployment" / "result.json").exists()
    assert (run_dir / "11-deployment-plan.json").exists()
    assert (run_dir / "13-deployment-summary.md").exists()


def test_deployment_contract_accepts_utf8_bom_result_json(tmp_path):
    run_dir = _create_run(tmp_path)
    request = load_execution_request(run_dir)
    _write_required_deployment_artifacts(run_dir)
    payload = {
        "status": "deployed",
        "target_environment": "azure-container-apps-dev",
        "topology_summary": "API and web services.",
        "deployment_targets": [],
        "public_urls": ["https://web.example.com"],
        "post_deploy_qa_targets": ["https://web.example.com"],
        "actions_performed": [],
        "blockers": [],
        "risks": [],
    }
    (run_dir / "deployment").mkdir(parents=True, exist_ok=True)
    (run_dir / "deployment" / "result.json").write_text(
        json.dumps(payload),
        encoding="utf-8-sig",
    )

    contract = read_deployment_contract(
        run_dir,
        Path(request.target_project_dir),
        "DEPLOYMENT_STATUS: deployed",
    )

    assert contract["contract_valid"]
    assert contract["status"] == "deployed"
    assert contract["result"]["public_urls"] == ["https://web.example.com"]


def test_deployment_contract_prefers_valid_fallback_over_invalid_run_level(tmp_path):
    run_dir = _create_run(tmp_path)
    request = load_execution_request(run_dir)
    target_dir = Path(request.target_project_dir)
    _write_required_deployment_artifacts(run_dir)
    (run_dir / "deployment").mkdir(parents=True, exist_ok=True)
    (run_dir / "deployment" / "result.json").write_text("{not json", encoding="utf-8")
    _write_required_deployment_artifacts(target_dir)
    fallback_payload = {
        "status": "deployed",
        "target_environment": "azure-container-apps-dev",
        "topology_summary": "Recovered fallback contract.",
        "deployment_targets": [],
        "public_urls": ["https://web.example.com"],
        "post_deploy_qa_targets": ["https://web.example.com"],
        "actions_performed": [],
        "blockers": [],
        "risks": [],
    }
    (target_dir / "deployment").mkdir(parents=True, exist_ok=True)
    (target_dir / "deployment" / "result.json").write_text(
        json.dumps(fallback_payload),
        encoding="utf-8",
    )
    (target_dir / "13-deployment-summary.md").write_text(
        "# Deployment\n\nStatus: deployed\n",
        encoding="utf-8",
    )

    contract = read_deployment_contract(
        run_dir,
        target_dir,
        "DEPLOYMENT_STATUS: deployed",
    )

    assert contract["contract_valid"]
    assert contract["result"]["topology_summary"] == "Recovered fallback contract."
    recovered = json.loads((run_dir / "deployment" / "result.json").read_text(encoding="utf-8"))
    assert recovered["status"] == "deployed"
    assert "Status: deployed" in (run_dir / "13-deployment-summary.md").read_text(encoding="utf-8")


def test_deployment_codex_prompt_does_not_prescribe_topology_or_commands(tmp_path):
    run_dir = _create_run(tmp_path)
    request = load_execution_request(run_dir)
    prompt = build_deployment_codex_prompt(
        request,
        run_dir,
        attempt=1,
        previous_summary="",
    )

    assert "The platform\nwill not infer topology" in prompt
    assert "Do not assume fixed service names" in prompt
    assert "Docker Compose" in prompt
    assert "Non-exhaustive deployment toolbox" in prompt
    assert "not as a limiting checklist" in prompt
    assert "Nth release / redeploy policy" in prompt
    assert "Reuse/update existing dev infrastructure by default" in prompt
    assert "Never create per-run resource names" in prompt
    assert "resource_changes" in prompt
    assert "Workspace ownership:" in prompt
    assert "Deployment-owned helper files" in prompt
    assert "project-local `.dockerignore` or `.gitignore`" in prompt
    assert "Do not write QA artifacts under `qa/`" in prompt
    assert "Verify cloud-readiness assumptions" in prompt
    assert "Local tests, local Docker smoke checks" in prompt
    assert "Classify the likely remediation owner" in prompt
    assert "Fullstack for application code" in prompt
    assert "Deployment for Azure resources" in prompt
    assert "remediation_requests" in prompt
    assert "az containerapp create" not in prompt
    assert "docker build -t" not in prompt


def test_deployment_codex_prompt_reads_release_scope_from_delivery_state(tmp_path):
    run_dir = _create_run(tmp_path)
    state_path = run_dir / DELIVERY_STATE_SNAPSHOT
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"completed_feature_ids": ["F1", "F2"]}),
        encoding="utf-8",
    )
    request = load_execution_request(run_dir)

    prompt = build_deployment_codex_prompt(
        request,
        run_dir,
        attempt=1,
        previous_summary="",
    )

    assert "Completed implementation features in this release batch: F1, F2" in prompt
    assert "Deployment release scope: F1, F2" in prompt


def test_deployment_codex_prompt_includes_exact_artifact_paths_and_repair_errors(tmp_path):
    run_dir = _create_run(tmp_path)
    request = load_execution_request(run_dir)

    prompt = build_deployment_codex_prompt(
        request,
        run_dir,
        attempt=2,
        previous_summary="DEPLOYMENT_STATUS: deployed",
        previous_contract_errors=[
            "Missing required deployment artifact: 13-deployment-summary.md."
        ],
    )

    assert str(run_dir / "deployment" / "result.json") in prompt
    assert str(run_dir / "11-deployment-plan.json") in prompt
    assert str(run_dir / "13-deployment-summary.md") in prompt
    assert str(Path(request.target_project_dir) / "deployment") in prompt
    assert "Missing required deployment artifact: 13-deployment-summary.md." in prompt


def _write_required_deployment_artifacts(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for path in [
        "11-deployment-plan.json",
        "12-deployment-request.json",
    ]:
        (root / path).write_text('{"status":"ready"}', encoding="utf-8")
    for path in [
        "11-deployment-plan.md",
        "12-deployment-request.md",
        "13-deployment-summary.md",
    ]:
        (root / path).write_text("# Deployment\n", encoding="utf-8")


def _create_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs" / "deployment-codex"
    target_dir = run_dir / "generated-project"
    target_dir.mkdir(parents=True)
    request_path = run_dir / "delivery/execution-request.json"
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "agent_id": "fullstack-agent",
                "agent_version": "0.1.0",
                "maturity_level": "L6 Codex Agent",
                "provider": "codex",
                "model": "gpt-5.3-codex",
                "target_project_dir": str(target_dir),
                "input_artifacts": ["05-implementation-brief.md"],
                "expected_outputs": ["README.md", "docker-compose.yml"],
                "instructions": ["Build the release batch."],
                "constraints": ["Keep names stable."],
                "feature_queue": [{"id": "F1", "title": "Create tasks", "delivery_order": 1}],
                "active_feature": None,
                "completed_feature_ids": ["F1"],
            }
        ),
        encoding="utf-8",
    )
    return run_dir
