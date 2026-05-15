from pathlib import Path

from agentic_company.console.live_logs import (
    command_progress_entries,
    friendly_log_entries,
)


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
