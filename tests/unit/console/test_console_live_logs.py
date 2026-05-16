from pathlib import Path

from agentic_company.console.live_logs import (
    command_progress_entries,
    friendly_log_entries,
)
from agentic_company.console.views.live_logs import (
    _codex_event_paths,
    _codex_log_paths,
    _read_codex_events,
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


def test_friendly_log_entries_include_team_lead_decisions(tmp_path: Path):
    rendered = "\n".join(
        friendly_log_entries(
            [
                {
                    "timestamp": "2026-05-09T10:00:00",
                    "agent_id": "team-lead-agent",
                    "event": "team_lead_decision",
                    "data": {
                        "decision": {
                            "tool": "run_qa",
                            "target": "F1",
                            "reason": "Validate the implemented feature.",
                        }
                    },
                }
            ],
            [],
            qa_log=tmp_path / "missing-qa.log",
            deployment_log=tmp_path / "missing-deployment.log",
        )
    )

    assert "Team Lead decision" in rendered
    assert (
        "`team-lead-agent` | tool=run_qa target=F1 reason=Validate the implemented feature."
        in rendered
    )


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


def test_friendly_log_entries_label_team_lead_codex_review_messages(tmp_path: Path):
    rendered = "\n".join(
        friendly_log_entries(
            [],
            [
                {
                    "recorded_at": "2026-05-02T00:41:55",
                    "agent_id": "team-lead-codex-review",
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "The handoff report is business-ready.",
                    },
                }
            ],
            qa_log=tmp_path / "missing-qa.log",
            deployment_log=tmp_path / "missing-deployment.log",
        )
    )

    assert "Team Lead Codex Review" in rendered
    assert "business-ready" in rendered


def test_friendly_log_entries_label_head_codex_review_messages(tmp_path: Path):
    rendered = "\n".join(
        friendly_log_entries(
            [],
            [
                {
                    "recorded_at": "2026-05-02T00:41:55",
                    "agent_id": "head-codex-review",
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "Architecture is ready for PM.",
                    },
                }
            ],
            qa_log=tmp_path / "missing-qa.log",
            deployment_log=tmp_path / "missing-deployment.log",
        )
    )

    assert "Head Codex Review" in rendered
    assert "ready for PM" in rendered


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


def test_fullstack_codex_execution_logs_are_discovered_recursively(tmp_path: Path):
    run_dir = tmp_path / "run"
    execution_dir = run_dir / "codex" / "F1" / "exec-run-fullstack-f1"
    execution_dir.mkdir(parents=True)
    events_path = execution_dir / "events.jsonl"
    log_path = execution_dir / "execution.log"
    events_path.write_text("{}\n", encoding="utf-8")
    log_path.write_text("Fullstack Codex execution is starting...\n", encoding="utf-8")

    assert events_path in _codex_event_paths(run_dir)
    assert log_path in _codex_log_paths(run_dir)


def test_upstream_planning_codex_logs_are_labeled_from_execution_id(tmp_path: Path):
    run_dir = tmp_path / "run"
    execution_dir = (
        run_dir
        / "upstream-planning"
        / "architect"
        / "codex"
        / "exec-run-architect-agent-architecture"
    )
    execution_dir.mkdir(parents=True)
    events_path = execution_dir / "events.jsonl"
    events_path.write_text(
        '{"type": "item.completed", '
        '"item": {"type": "agent_message", "text": "Architecture ready."}}\n',
        encoding="utf-8",
    )

    events = _read_codex_events(run_dir)

    assert events[0]["agent_id"] == "architect-agent"


def test_upstream_planning_codex_logs_are_labeled_from_event_execution_id(tmp_path: Path):
    run_dir = tmp_path / "run"
    execution_dir = run_dir / "upstream-planning" / "architect" / "codex" / "exec-123"
    execution_dir.mkdir(parents=True)
    events_path = execution_dir / "events.jsonl"
    events_path.write_text(
        '{"codex_execution_id": "codex-run-architect-agent", '
        '"type": "item.completed", '
        '"item": {"type": "agent_message", "text": "Architecture ready."}}\n',
        encoding="utf-8",
    )

    events = _read_codex_events(run_dir)

    assert events[0]["agent_id"] == "architect-agent"


def test_upstream_planning_codex_logs_are_labeled_from_execution_log(tmp_path: Path):
    run_dir = tmp_path / "run"
    execution_dir = run_dir / "upstream-planning" / "architect" / "codex" / "exec-123"
    execution_dir.mkdir(parents=True)
    events_path = execution_dir / "events.jsonl"
    log_path = execution_dir / "execution.log"
    events_path.write_text(
        '{"type": "item.completed", '
        '"item": {"type": "agent_message", "text": "Architecture ready."}}\n',
        encoding="utf-8",
    )
    log_path.write_text("agent_id=architect-agent\n", encoding="utf-8")

    events = _read_codex_events(run_dir)

    assert events[0]["agent_id"] == "architect-agent"


def test_upstream_planning_project_manager_codex_logs_are_not_labeled_as_ba(
    tmp_path: Path,
):
    run_dir = tmp_path / "run"
    execution_dir = run_dir / "upstream-planning" / "project-manager" / "codex" / "exec-123"
    execution_dir.mkdir(parents=True)
    events_path = execution_dir / "events.jsonl"
    events_path.write_text(
        '{"type": "item.completed", '
        '"item": {"type": "agent_message", "text": "Sprint plan ready."}}\n',
        encoding="utf-8",
    )

    events = _read_codex_events(run_dir)
    rendered = "\n".join(
        friendly_log_entries(
            [],
            events,
            qa_log=tmp_path / "missing-qa.log",
            deployment_log=tmp_path / "missing-deployment.log",
        )
    )

    assert events[0]["agent_id"] == "project-manager-agent"
    assert "Project Manager Codex" in rendered
    assert "Business Analyst Codex" not in rendered


def test_legacy_upstream_planning_codex_logs_are_still_discovered(tmp_path: Path):
    run_dir = tmp_path / "run"
    execution_dir = run_dir / "upstream-planning" / "codex" / "exec-legacy"
    execution_dir.mkdir(parents=True)
    events_path = execution_dir / "events.jsonl"
    log_path = execution_dir / "execution.log"
    events_path.write_text("{}\n", encoding="utf-8")
    log_path.write_text("legacy upstream log\n", encoding="utf-8")

    assert events_path in _codex_event_paths(run_dir)
    assert log_path in _codex_log_paths(run_dir)


def test_team_lead_codex_review_logs_are_discovered_recursively(tmp_path: Path):
    run_dir = tmp_path / "run"
    review_dir = run_dir / "team-lead" / "codex-review" / "exec-review"
    review_dir.mkdir(parents=True)
    events_path = review_dir / "events.jsonl"
    log_path = review_dir / "execution.log"
    events_path.write_text("{}\n", encoding="utf-8")
    log_path.write_text("Team Lead Codex review is starting...\n", encoding="utf-8")

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
