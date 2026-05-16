"""Agent-to-agent message contracts and run-local message storage."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

AgentMessageStatus = Literal["sent", "received", "handled", "failed"]


@dataclass(frozen=True, slots=True)
class AgentMessage:
    """Small message packet passed between platform agents."""

    from_agent: str
    to_agent: str
    intent: str
    content: str
    artifact_refs: list[str] = field(default_factory=list)
    message_id: str = field(default_factory=lambda: f"msg-{uuid4().hex}")
    correlation_id: str | None = None
    parent_message_id: str | None = None
    execution_id: str | None = None
    parent_execution_id: str | None = None
    status: AgentMessageStatus = "sent"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Serialize the message for JSONL storage and prompt packets."""

        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AgentMessage:
        """Deserialize a message from JSON-compatible data."""

        return cls(
            from_agent=str(payload["from_agent"]),
            to_agent=str(payload["to_agent"]),
            intent=str(payload["intent"]),
            content=str(payload["content"]),
            artifact_refs=[str(item) for item in payload.get("artifact_refs", [])],
            message_id=str(payload.get("message_id") or f"msg-{uuid4().hex}"),
            correlation_id=(
                str(payload["correlation_id"]) if payload.get("correlation_id") else None
            ),
            parent_message_id=(
                str(payload["parent_message_id"]) if payload.get("parent_message_id") else None
            ),
            execution_id=str(payload["execution_id"]) if payload.get("execution_id") else None,
            parent_execution_id=(
                str(payload["parent_execution_id"]) if payload.get("parent_execution_id") else None
            ),
            status=str(payload.get("status", "sent")),  # type: ignore[arg-type]
            created_at=str(payload.get("created_at") or datetime.now(UTC).isoformat()),
        )


class AgentMessageStore:
    """Append-only JSONL message store scoped to one run directory."""

    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / "messages" / "agent-messages.jsonl"

    def append(self, message: AgentMessage) -> AgentMessage:
        """Append a message to the run-local message log."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(message.to_dict(), sort_keys=True) + "\n")
        return message

    def get(self, message_id: str) -> AgentMessage | None:
        """Return one message by id from the run-local message log."""

        for message in self.read():
            if message.message_id == message_id:
                return message
        return None

    def read(
        self,
        *,
        to_agent: str | None = None,
        from_agent: str | None = None,
        intent: str | None = None,
        correlation_id: str | None = None,
        limit: int | None = None,
    ) -> list[AgentMessage]:
        """Read messages with optional sender, recipient, intent, and limit filters."""

        if not self.path.exists():
            return []

        messages: list[AgentMessage] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            message = AgentMessage.from_dict(json.loads(line))
            if to_agent is not None and message.to_agent != to_agent:
                continue
            if from_agent is not None and message.from_agent != from_agent:
                continue
            if intent is not None and message.intent != intent:
                continue
            if correlation_id is not None and message.correlation_id != correlation_id:
                continue
            messages.append(message)

        if limit is not None:
            return messages[-limit:]
        return messages


def render_incoming_messages_for_prompt(
    run_dir: str | Path,
    *,
    to_agent: str,
    limit: int = 3,
) -> str:
    """Render recent upstream messages for inclusion in a specialist prompt."""

    messages = AgentMessageStore(run_dir).read(to_agent=to_agent, limit=limit)
    if not messages:
        return "- No upstream messages were sent to this agent."

    sections: list[str] = []
    for message in messages:
        artifacts = "\n".join(f"  - {artifact}" for artifact in message.artifact_refs)
        sections.append(
            "\n".join(
                [
                    f"- Message id: {message.message_id}",
                    f"  From: {message.from_agent}",
                    f"  Intent: {message.intent}",
                    f"  Correlation: {message.correlation_id or '-'}",
                    f"  Execution: {message.execution_id or '-'}",
                    "  Content:",
                    _indent_message(message.content, prefix="    "),
                    "  Artifact refs:",
                    artifacts or "  - None",
                ]
            )
        )
    return "\n\n".join(sections)


def append_agent_response(
    run_dir: str | Path,
    *,
    from_agent: str,
    to_agent: str,
    status: str,
    content: str,
    artifact_refs: list[str],
    correlation_id: str | None = None,
    parent_message_id: str | None = None,
    execution_id: str | None = None,
    parent_execution_id: str | None = None,
) -> AgentMessage:
    """Append a generic downstream response message for an upstream tool call."""

    return AgentMessageStore(run_dir).append(
        AgentMessage(
            from_agent=from_agent,
            to_agent=to_agent,
            intent="agent_response",
            content=content or f"{from_agent} completed with status {status}.",
            artifact_refs=artifact_refs,
            correlation_id=correlation_id,
            parent_message_id=parent_message_id,
            execution_id=execution_id,
            parent_execution_id=parent_execution_id,
            status="sent",
        )
    )


def _indent_message(content: str, *, prefix: str) -> str:
    lines = content.splitlines() or [""]
    return "\n".join(f"{prefix}{line}" for line in lines)
