from pathlib import Path

from agentic_company.platform.executions import (
    execution_artifact_dir,
    extract_codex_thread_id,
)


def test_execution_artifact_dir_uses_short_human_readable_name():
    artifact_dir = execution_artifact_dir(
        root=Path("codex") / "F1",
        execution_id="exec-console-20260510-135612-fullstack-agent-f1-delegate-feature-fe97a1f0",
    )

    assert artifact_dir == Path("codex") / "F1" / "exec-fe97a1f0"


def test_execution_artifact_dir_preserves_attempt_folder():
    artifact_dir = execution_artifact_dir(
        root=Path("qa") / "codex" / "F1",
        execution_id="exec-console-20260510-135612-qa-agent-f1-feature-qa-12345678",
        attempt=2,
    )

    assert artifact_dir == Path("qa") / "codex" / "F1" / "exec-12345678" / "attempt-2"


def test_extract_codex_thread_id_tolerates_windows_encoded_event_text(tmp_path: Path):
    events_path = tmp_path / "events.jsonl"
    events_path.write_bytes(b'{"thread_id": "thread-1", "note": "Sprint \\u2013 ok"}\n')
    assert extract_codex_thread_id(events_path) == "thread-1"

    events_path.write_bytes(b'{"thread_id": "thread-2", "note": "Sprint \x96 ok"}\n')
    assert extract_codex_thread_id(events_path) == "thread-2"
