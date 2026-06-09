from agentic_company.console.web.db import ConsoleRepository
from agentic_company.platform.messages import (
    AgentMessage,
    AgentMessageStore,
    append_agent_response,
    render_incoming_messages_for_prompt,
)


def test_agent_message_round_trips_as_prompt_packet():
    message = AgentMessage(
        message_id="msg-1",
        from_agent="team-lead-agent",
        to_agent="qa-agent",
        intent="request_qa",
        content="Validate feature F2.",
        artifact_refs=["team-lead/sprint-01-plan.json"],
        correlation_id="feature-F2",
        created_at="2026-05-09T00:00:00+00:00",
    )

    payload = message.to_dict()
    restored = AgentMessage.from_dict(payload)

    assert restored == message
    assert payload["artifact_refs"] == ["team-lead/sprint-01-plan.json"]


def test_agent_message_store_filters_run_messages_from_db(tmp_path, monkeypatch):
    store, _repo, _run = _message_store(tmp_path, monkeypatch)
    store.append(
        AgentMessage(
            message_id="msg-1",
            from_agent="team-lead-agent",
            to_agent="fullstack-agent",
            intent="delegate_feature",
            content="Implement F1.",
            created_at="2026-05-09T00:00:00+00:00",
        )
    )
    store.append(
        AgentMessage(
            message_id="msg-2",
            from_agent="team-lead-agent",
            to_agent="qa-agent",
            intent="request_qa",
            content="Validate F1.",
            created_at="2026-05-09T00:01:00+00:00",
        )
    )

    qa_messages = store.read(to_agent="qa-agent")
    latest = store.read(limit=1)

    assert store.get("msg-1") is not None
    assert store.get("missing") is None
    assert [message.message_id for message in qa_messages] == ["msg-2"]
    assert [message.message_id for message in latest] == ["msg-2"]
    assert store.read(to_agent="deployment-agent") == []


def test_agent_message_store_filters_by_correlation_id(tmp_path, monkeypatch):
    store, _repo, _run = _message_store(tmp_path, monkeypatch)
    store.append(
        AgentMessage(
            message_id="msg-1",
            from_agent="team-lead-agent",
            to_agent="fullstack-agent",
            intent="delegate_feature",
            content="Implement F1.",
            correlation_id="F1",
        )
    )
    store.append(
        AgentMessage(
            message_id="msg-2",
            from_agent="team-lead-agent",
            to_agent="fullstack-agent",
            intent="delegate_feature",
            content="Implement F2.",
            correlation_id="F2",
        )
    )

    messages = store.read(to_agent="fullstack-agent", correlation_id="F2")

    assert [message.message_id for message in messages] == ["msg-2"]


def test_agent_messages_render_prompt_context_and_response(tmp_path, monkeypatch):
    store, _repo, run = _message_store(tmp_path, monkeypatch)
    run_dir = run.run_dir
    store.append(
        AgentMessage(
            message_id="msg-in",
            from_agent="team-lead-agent",
            to_agent="qa-agent",
            intent="request_qa",
            content="Validate deployed behavior.",
            artifact_refs=["08-qa-report-F1.md"],
            correlation_id="post-deploy",
        )
    )

    rendered = render_incoming_messages_for_prompt(run_dir, to_agent="qa-agent")
    response = append_agent_response(
        run_dir,
        from_agent="qa-agent",
        to_agent="team-lead-agent",
        status="qa_passed",
        content="QA passed.",
        artifact_refs=["qa/results-post-deploy.json"],
        correlation_id="post-deploy",
        parent_message_id="msg-in",
    )

    assert "Validate deployed behavior." in rendered
    assert "08-qa-report-F1.md" in rendered
    assert response.intent == "agent_response"
    assert response.parent_message_id == "msg-in"


def test_agent_message_store_mirrors_run_messages_to_db(tmp_path, monkeypatch):
    store, repo, run = _message_store(tmp_path, monkeypatch)

    store.append(
        AgentMessage(
            message_id="msg-db",
            from_agent="team-lead-agent",
            to_agent="qa-agent",
            intent="request_qa",
            content="Validate from DB.",
            correlation_id="US-1",
            created_at="2026-05-09T00:00:00+00:00",
        )
    )

    db_payloads = repo.list_agent_messages(run.id, to_agent="qa-agent", correlation_id="US-1")
    messages = store.read(to_agent="qa-agent", correlation_id="US-1")

    assert [payload["message_id"] for payload in db_payloads] == ["msg-db"]
    assert [message.message_id for message in messages] == ["msg-db"]


def test_agent_message_store_requires_registered_db_run(tmp_path, monkeypatch):
    store = AgentMessageStore(tmp_path / "missing-run")

    try:
        store.read()
    except RuntimeError as exc:
        assert "No DB run is registered" in str(exc)
    else:
        raise AssertionError("Expected unregistered message store reads to fail.")


def _message_store(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="messages@example.test",
        username="messages-user",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Messages",
        request_text="Messages",
        mode="internal_tool",
        complexity="simple",
        status="running",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-messages",
        run_dir=run_dir,
        status="running",
        mode="internal_tool",
        reasoning="medium",
    )
    return AgentMessageStore(run_dir), repo, run
