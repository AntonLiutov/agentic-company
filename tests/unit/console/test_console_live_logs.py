from pathlib import Path

from agentic_company.console.live_logs import (
    command_progress_entries,
    friendly_log_entries,
)
from agentic_company.console.views.live_logs import _codex_event_paths, _codex_log_paths


def test_command_progress_entries_include_started_and_completed_steps(tmp_path: Path):
    log_path = tmp_path / "commands.log"
    log_path.write_text(
        """## Docker runtime E2E
$ uv run --with playwright python docker_runtime_e2e.py
cwd=C:\\project
started_at=2026-04-27T00:38:25
status=running
output:
building
exit_code=0
completed_at=2026-04-27T00:39:22

""",
        encoding="utf-8",
    )

    entries = command_progress_entries(log_path, agent="qa-agent", phase="QA")
    rendered = "\n".join(entry for _, entry in entries)

    assert "2026-04-27 00:38:25 - QA step started" in rendered
    assert "`qa-agent` | Docker runtime E2E" in rendered
    assert "2026-04-27 00:39:22 - QA step passed" in rendered


def test_friendly_log_entries_include_delivery_graph_events(tmp_path: Path):
    events: list[dict[str, object]] = [
        {
            "timestamp": "2026-04-27T10:00:00",
            "agent_id": "delivery-graph",
            "event": "delivery_graph_started",
            "data": {"node_order": ["fullstack", "qa"]},
        },
        {
            "timestamp": "2026-04-27T10:00:01",
            "agent_id": "delivery-graph",
            "event": "delivery_graph_node_started",
            "data": {"node": "fullstack", "status": "initialized"},
        },
        {
            "timestamp": "2026-04-27T10:00:02",
            "agent_id": "delivery-graph",
            "event": "delivery_graph_node_completed",
            "data": {"node": "fullstack", "status": "codex_completed"},
        },
        {
            "timestamp": "2026-04-27T10:00:03",
            "agent_id": "delivery-graph",
            "event": "delivery_graph_completed",
            "data": {"status": "deployment_ready"},
        },
    ]

    rendered = "\n".join(
        friendly_log_entries(
            events,
            [],
            qa_log=tmp_path / "missing-qa.log",
            deployment_log=tmp_path / "missing-deployment.log",
        )
    )

    assert "Delivery graph started" in rendered
    assert "Graph node started" in rendered
    assert "`delivery-graph` | node=fullstack status=initialized" in rendered
    assert "Graph node completed" in rendered
    assert "`delivery-graph` | node=fullstack status=codex_completed" in rendered
    assert "Delivery graph completed" in rendered


def test_friendly_log_entries_label_codex_feature_messages(tmp_path: Path):
    rendered = "\n".join(
        friendly_log_entries(
            [],
            [
                {
                    "recorded_at": "2026-05-02T00:41:55",
                    "feature_id": "F1",
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "I am implementing the create/list feature.",
                    },
                }
            ],
            qa_log=tmp_path / "missing-qa.log",
            deployment_log=tmp_path / "missing-deployment.log",
        )
    )

    assert "Codex (F1)" in rendered
    assert "create/list feature" in rendered


def test_friendly_log_entries_label_qa_codex_feature_messages(tmp_path: Path):
    rendered = "\n".join(
        friendly_log_entries(
            [],
            [
                {
                    "recorded_at": "2026-05-02T00:41:55",
                    "agent_id": "qa-codex-agent",
                    "feature_id": "F1",
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "I am reviewing the create/list feature.",
                    },
                }
            ],
            qa_log=tmp_path / "missing-qa.log",
            deployment_log=tmp_path / "missing-deployment.log",
        )
    )

    assert "QA Codex (F1)" in rendered
    assert "reviewing the create/list feature" in rendered


def test_qa_codex_attempt_logs_are_discovered_recursively(tmp_path: Path):
    run_dir = tmp_path / "run"
    attempt_dir = run_dir / "qa" / "codex" / "F1" / "attempt-1"
    attempt_dir.mkdir(parents=True)
    events_path = attempt_dir / "events.jsonl"
    log_path = attempt_dir / "execution.log"
    events_path.write_text("{}\n", encoding="utf-8")
    log_path.write_text("QA Codex execution is starting...\n", encoding="utf-8")

    assert events_path in _codex_event_paths(run_dir)
    assert log_path in _codex_log_paths(run_dir)


def test_handoff_codex_attempt_logs_are_discovered_recursively(tmp_path: Path):
    run_dir = tmp_path / "run"
    attempt_dir = run_dir / "handoff" / "codex" / "attempt-1"
    attempt_dir.mkdir(parents=True)
    events_path = attempt_dir / "events.jsonl"
    log_path = attempt_dir / "execution.log"
    events_path.write_text("{}\n", encoding="utf-8")
    log_path.write_text("Handoff Codex execution is starting...\n", encoding="utf-8")

    assert events_path in _codex_event_paths(run_dir)
    assert log_path in _codex_log_paths(run_dir)
