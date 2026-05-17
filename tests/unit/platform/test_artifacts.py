import json
from pathlib import Path

from agentic_company.platform.artifacts import (
    build_execution_request_payload,
    load_execution_request,
    read_json_artifact,
    read_text_artifact,
    update_execution_request_context,
)


def test_update_execution_request_context_clears_stale_codex_resume_thread(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    request_path = run_dir / "delivery/execution-request.json"
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(
        json.dumps(
            {
                "run_id": "run",
                "agent_id": "fullstack-agent",
                "agent_version": "0.1.0",
                "maturity_level": "L6 Codex Agent",
                "provider": "codex",
                "model": "gpt-5.3-codex",
                "target_project_dir": str(run_dir / "generated-project"),
                "input_artifacts": [],
                "expected_outputs": [],
                "instructions": [],
                "constraints": [],
                "codex_resume_thread_id": "thread-from-previous-agent",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    update_execution_request_context(run_dir)

    request = load_execution_request(run_dir)
    assert request.codex_resume_thread_id == ""


def test_build_execution_request_payload_omits_legacy_topology_label(tmp_path: Path):
    payload = build_execution_request_payload(
        {
            "run_id": "run",
            "run_dir": str(tmp_path / "run"),
        },
        agent_id="deployment-agent",
        model="gpt-5.3-codex",
        input_artifacts=[],
        expected_outputs=[],
        instructions=[],
        constraints=[],
    )

    assert set(payload) == {
        "active_feature",
        "agent_id",
        "agent_version",
        "codex_resume_thread_id",
        "completed_feature_ids",
        "constraints",
        "execution_id",
        "execution_intent",
        "expected_outputs",
        "feature_queue",
        "input_artifacts",
        "instructions",
        "maturity_level",
        "model",
        "parent_message_id",
        "provider",
        "run_id",
        "target_project_dir",
    }


def test_read_json_artifact_tolerates_and_normalizes_utf8_bom(tmp_path: Path):
    path = tmp_path / "artifact.json"
    path.write_text('{"ok": true}', encoding="utf-8-sig")

    payload = read_json_artifact(path, normalize_bom=True)

    assert payload == {"ok": True}
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_read_text_artifact_tolerates_windows_encoded_text(tmp_path: Path):
    path = tmp_path / "summary.md"
    path.write_bytes(b"HANDOFF_STATUS: ready\n\nSprint \x96 done\n")

    text = read_text_artifact(path)

    assert "HANDOFF_STATUS: ready" in text
    assert "Sprint - done" not in text
    assert "Sprint \u2013 done" in text
