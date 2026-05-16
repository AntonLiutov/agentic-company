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


def test_agent_message_store_filters_run_local_messages(tmp_path):
    store = AgentMessageStore(tmp_path)
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


def test_agent_message_store_filters_by_correlation_id(tmp_path):
    store = AgentMessageStore(tmp_path)
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


def test_agent_messages_render_prompt_context_and_response(tmp_path):
    store = AgentMessageStore(tmp_path)
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

    rendered = render_incoming_messages_for_prompt(tmp_path, to_agent="qa-agent")
    response = append_agent_response(
        tmp_path,
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
