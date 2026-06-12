"""Structured runtime trace storage for delivery runs."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path
from typing import Any, Literal

from agentic_company.platform.security import redact_sensitive_output
from agentic_company.platform.status import WorkItemStatus, classify_work_item_status

RUN_TRACE_DIR = "delivery"
RUN_EVENTS_FILE = "run-events.jsonl"
TOOL_CALL_EVENTS_FILE = "tool-call-events.jsonl"
MODEL_CALL_EVENTS_FILE = "model-call-events.jsonl"

SECRET_REDACTION = "[REDACTED]"
SECRET_KEY_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "credential",
    "credentials",
    "encrypted_value",
    "gemini_api_key",
    "google_api_key",
    "openai_api_key",
    "password",
    "secret",
    "token",
}
SECRET_KEY_SUFFIXES = ("_api_key", "_secret", "_token", "_password")
ARTIFACT_ID_PREFIX = "art_"
RUNTIME_LOGGER = logging.getLogger("agentic_company.runtime")

TraceEventKind = Literal["run", "tool", "model"]


@dataclass(frozen=True, slots=True)
class RunEvent:
    """Business-safe runtime event for graph, agent, status, and checkpoint activity."""

    event_id: str
    project_id: int | None
    run_id: int | str
    work_item_id: str | None
    agent_id: str
    event_type: str
    status: str
    message: str
    artifact_ids: list[str] = field(default_factory=list)
    external_refs: list[dict[str, Any]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    """Structured trace for an AgentExecutor or coordinator tool invocation."""

    event_id: str
    run_id: int | str
    work_item_id: str | None
    agent_id: str
    tool_name: str
    tool_call_id: str
    input_summary: dict[str, Any] = field(default_factory=dict)
    output_summary: dict[str, Any] = field(default_factory=dict)
    artifact_ids: list[str] = field(default_factory=list)
    status: str = ""
    failure_mode: str | None = None
    duration_ms: int | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ModelCallEvent:
    """Structured trace for model-backed work attempts."""

    event_id: str
    run_id: int | str
    agent_id: str
    provider: str
    model: str
    purpose: str
    prompt_ref: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    status: str = ""
    duration_ms: int | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RunTrace:
    """Loaded structured trace for one run."""

    run_events: list[RunEvent]
    tool_call_events: list[ToolCallEvent]
    model_call_events: list[ModelCallEvent]

    def summary(self) -> dict[str, Any]:
        return trace_summary(
            self.run_events,
            self.tool_call_events,
            self.model_call_events,
        )


class TraceStore:
    """File-backed structured trace store under a run directory."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    @property
    def trace_dir(self) -> Path:
        return trace_dir(self.run_dir)

    def append_run_event(self, event: RunEvent) -> RunEvent:
        _append_jsonl_once(self.trace_dir / RUN_EVENTS_FILE, event.to_dict(), event.event_id)
        return event

    def append_tool_call_event(self, event: ToolCallEvent) -> ToolCallEvent:
        _append_jsonl_once(self.trace_dir / TOOL_CALL_EVENTS_FILE, event.to_dict(), event.event_id)
        return event

    def append_model_call_event(self, event: ModelCallEvent) -> ModelCallEvent:
        _append_jsonl_once(self.trace_dir / MODEL_CALL_EVENTS_FILE, event.to_dict(), event.event_id)
        return event

    def load(self) -> RunTrace:
        return RunTrace(
            run_events=load_run_events(self.run_dir),
            tool_call_events=load_tool_call_events(self.run_dir),
            model_call_events=load_model_call_events(self.run_dir),
        )


class TraceRecorder:
    """Small facade used by runtime hooks to write structured trace events."""

    def __init__(self, run_dir: Path) -> None:
        self.store = TraceStore(run_dir)

    def run_event(self, **kwargs: Any) -> RunEvent:
        return record_run_event(self.store.run_dir, **kwargs)

    def tool_call_event(self, **kwargs: Any) -> ToolCallEvent:
        return record_tool_call_event(self.store.run_dir, **kwargs)

    def model_call_event(self, **kwargs: Any) -> ModelCallEvent:
        return record_model_call_event(self.store.run_dir, **kwargs)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def trace_dir(run_dir: Path) -> Path:
    return run_dir / RUN_TRACE_DIR


def record_run_event(
    run_dir: Path,
    *,
    run_id: int | str,
    agent_id: str,
    event_type: str,
    status: str = "",
    message: str = "",
    project_id: int | None = None,
    work_item_id: str | None = None,
    artifact_ids: list[str] | None = None,
    external_refs: list[dict[str, Any]] | None = None,
    data: dict[str, Any] | None = None,
    created_at: str | None = None,
    event_id: str | None = None,
) -> RunEvent:
    created = created_at or utc_now()
    safe_data = sanitize_trace_data(data or {})
    ids = sorted({*(artifact_ids or []), *extract_artifact_ids(safe_data)})
    refs = _safe_external_refs(external_refs or _external_refs_from_data(safe_data))
    event = RunEvent(
        event_id=event_id
        or build_event_id(
            "run",
            run_id,
            agent_id,
            event_type,
            status,
            work_item_id or "",
            _event_correlation_key(safe_data),
            message,
        ),
        project_id=project_id,
        run_id=run_id,
        work_item_id=work_item_id,
        agent_id=agent_id,
        event_type=event_type,
        status=status,
        message=message or event_type,
        artifact_ids=ids,
        external_refs=refs,
        data=safe_data,
        created_at=created,
    )
    is_new = not _jsonl_contains_event_id(trace_dir(run_dir) / RUN_EVENTS_FILE, event.event_id)
    stored = TraceStore(run_dir).append_run_event(event)
    _persist_run_event_to_db(stored)
    if is_new:
        _log_run_event(stored)
    return stored


def record_tool_call_event(
    run_dir: Path,
    *,
    run_id: int | str,
    agent_id: str,
    tool_name: str,
    tool_call_id: str,
    status: str,
    work_item_id: str | None = None,
    input_summary: dict[str, Any] | None = None,
    output_summary: dict[str, Any] | None = None,
    artifact_ids: list[str] | None = None,
    failure_mode: str | None = None,
    duration_ms: int | None = None,
    created_at: str | None = None,
    event_id: str | None = None,
) -> ToolCallEvent:
    created = created_at or utc_now()
    safe_input = sanitize_trace_data(input_summary or {})
    safe_output = sanitize_trace_data(output_summary or {})
    safe_failure_mode = None if _looks_successful(status) else failure_mode
    ids = sorted(
        {
            *(artifact_ids or []),
            *extract_artifact_ids(safe_input),
            *extract_artifact_ids(safe_output),
        }
    )
    event = ToolCallEvent(
        event_id=event_id
        or build_event_id(
            "tool",
            run_id,
            agent_id,
            tool_name,
            tool_call_id,
            status,
            work_item_id or "",
        ),
        run_id=run_id,
        work_item_id=work_item_id,
        agent_id=agent_id,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        input_summary=safe_input,
        output_summary=safe_output,
        artifact_ids=ids,
        status=status,
        failure_mode=safe_failure_mode,
        duration_ms=duration_ms,
        created_at=created,
    )
    is_new = not _jsonl_contains_event_id(
        trace_dir(run_dir) / TOOL_CALL_EVENTS_FILE, event.event_id
    )
    stored = TraceStore(run_dir).append_tool_call_event(event)
    _persist_tool_call_event_to_db(stored)
    if is_new:
        _log_tool_call_event(stored)
    return stored


def record_model_call_event(
    run_dir: Path,
    *,
    run_id: int | str,
    agent_id: str,
    provider: str,
    model: str,
    purpose: str,
    prompt_ref: str,
    status: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    estimated_cost_usd: float | None = None,
    duration_ms: int | None = None,
    created_at: str | None = None,
    event_id: str | None = None,
) -> ModelCallEvent:
    created = created_at or utc_now()
    event = ModelCallEvent(
        event_id=event_id
        or build_event_id(
            "model",
            run_id,
            agent_id,
            provider,
            model,
            purpose,
            prompt_ref,
            status,
        ),
        run_id=run_id,
        agent_id=agent_id,
        provider=provider,
        model=model,
        purpose=purpose,
        prompt_ref=prompt_ref,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=estimated_cost_usd,
        status=status,
        duration_ms=duration_ms,
        created_at=created,
    )
    is_new = not _jsonl_contains_event_id(
        trace_dir(run_dir) / MODEL_CALL_EVENTS_FILE, event.event_id
    )
    stored = TraceStore(run_dir).append_model_call_event(event)
    _persist_model_call_event_to_db(stored)
    if not is_new:
        return stored
    return stored


def record_raw_log_event(
    *,
    run_id: int | str,
    agent_id: str,
    seq: int,
    message: str,
    work_item_id: str | None = None,
    sprint_id: str = "",
    tool_name: str = "",
    tool_call_id: str = "",
    level: str = "info",
    stream: str = "stdout",
    created_at: str | None = None,
) -> None:
    """Mirror developer/raw execution log lines into canonical DB storage."""

    db_run_id = _console_run_db_id(run_id)
    if db_run_id is None:
        return
    try:
        from agentic_company.console.web.db import ConsoleRepository
        from agentic_company.console.web.sql_backend import retry_database_operation

        def operation() -> None:
            repo = ConsoleRepository()
            repo.init_schema()
            repo.append_raw_log_event(
                db_run_id,
                agent_id=agent_id,
                work_item_id=work_item_id or "",
                sprint_id=sprint_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                seq=seq,
                level=level,
                stream=stream,
                message=redact_sensitive_output(message.rstrip()),
                created_at=created_at or utc_now(),
            )

        retry_database_operation(operation)
    except Exception:
        RUNTIME_LOGGER.debug("Skipping DB raw log persistence", exc_info=True)


def load_run_events(run_dir: Path) -> list[RunEvent]:
    db_events = _load_run_events_from_db(run_dir)
    if db_events is not None:
        return db_events
    return [
        run_event_from_mapping(item) for item in _read_jsonl(trace_dir(run_dir) / RUN_EVENTS_FILE)
    ]


def load_tool_call_events(run_dir: Path) -> list[ToolCallEvent]:
    db_events = _load_tool_call_events_from_db(run_dir)
    if db_events is not None:
        return db_events
    return [
        tool_call_event_from_mapping(item)
        for item in _read_jsonl(trace_dir(run_dir) / TOOL_CALL_EVENTS_FILE)
    ]


def load_model_call_events(run_dir: Path) -> list[ModelCallEvent]:
    db_events = _load_model_call_events_from_db(run_dir)
    if db_events is not None:
        return db_events
    return [
        model_call_event_from_mapping(item)
        for item in _read_jsonl(trace_dir(run_dir) / MODEL_CALL_EVENTS_FILE)
    ]


def _persist_run_event_to_db(event: RunEvent) -> None:
    run_id = _console_run_db_id(event.run_id)
    if run_id is None:
        return
    try:
        from agentic_company.console.web.db import ConsoleRepository
        from agentic_company.console.web.sql_backend import retry_database_operation

        def operation() -> None:
            repo = ConsoleRepository()
            repo.init_schema()
            with repo.connect() as conn:
                repo._upsert_run_event_conn(conn, run_id, event)

        retry_database_operation(operation)
        _record_card_log_from_run_event(event)
    except Exception:
        RUNTIME_LOGGER.debug("Skipping DB run trace persistence", exc_info=True)


def _persist_tool_call_event_to_db(event: ToolCallEvent) -> None:
    run_id = _console_run_db_id(event.run_id)
    if run_id is None:
        return
    try:
        from agentic_company.console.web.db import ConsoleRepository
        from agentic_company.console.web.sql_backend import retry_database_operation

        def operation() -> None:
            repo = ConsoleRepository()
            repo.init_schema()
            repo.upsert_tool_call_event(run_id, event)

        retry_database_operation(operation)
    except Exception:
        RUNTIME_LOGGER.debug("Skipping DB tool trace persistence", exc_info=True)


def _persist_model_call_event_to_db(event: ModelCallEvent) -> None:
    run_id = _console_run_db_id(event.run_id)
    if run_id is None:
        return
    try:
        from agentic_company.console.web.db import ConsoleRepository
        from agentic_company.console.web.sql_backend import retry_database_operation

        def operation() -> None:
            repo = ConsoleRepository()
            repo.init_schema()
            repo.upsert_model_call_event(run_id, event)

        retry_database_operation(operation)
    except Exception:
        RUNTIME_LOGGER.debug("Skipping DB model trace persistence", exc_info=True)


def _console_run_db_id(runtime_run_id: int | str) -> int | None:
    try:
        from agentic_company.console.web.db import ConsoleRepository

        repo = ConsoleRepository()
        repo.init_schema()
        if isinstance(runtime_run_id, int):
            run = repo.get_run(runtime_run_id)
            return run.id if run else None
        token = str(runtime_run_id or "").strip()
        if not token:
            return None
        run = repo.get_run_by_uid(token)
        if run:
            return run.id
        if token.isdigit():
            by_id = repo.get_run(int(token))
            return by_id.id if by_id else None
    except Exception:
        RUNTIME_LOGGER.debug("Skipping DB trace lookup", exc_info=True)
    return None


def _console_run_db_id_for_run_dir(run_dir: Path) -> int | None:
    try:
        from agentic_company.console.web.db import ConsoleRepository

        repo = ConsoleRepository()
        repo.init_schema()
        with repo.connect() as conn:
            row = conn.execute(
                "SELECT id FROM runs WHERE run_dir = ? OR run_uid = ?",
                (str(run_dir), run_dir.name),
            ).fetchone()
        return int(row["id"]) if row else None
    except Exception:
        RUNTIME_LOGGER.debug("Skipping DB trace run_dir lookup", exc_info=True)
    return None


def _load_run_events_from_db(run_dir: Path) -> list[RunEvent] | None:
    db_run_id = _console_run_db_id_for_run_dir(run_dir)
    if db_run_id is None:
        return None
    try:
        from agentic_company.console.web.db import ConsoleRepository

        repo = ConsoleRepository()
        repo.init_schema()
        return repo.list_run_events(db_run_id)
    except Exception:
        RUNTIME_LOGGER.debug("Skipping DB run trace load", exc_info=True)
        return None


def _load_tool_call_events_from_db(run_dir: Path) -> list[ToolCallEvent] | None:
    db_run_id = _console_run_db_id_for_run_dir(run_dir)
    if db_run_id is None:
        return None
    try:
        from agentic_company.console.web.db import ConsoleRepository

        repo = ConsoleRepository()
        repo.init_schema()
        return repo.list_tool_call_events(db_run_id)
    except Exception:
        RUNTIME_LOGGER.debug("Skipping DB tool trace load", exc_info=True)
        return None


def _load_model_call_events_from_db(run_dir: Path) -> list[ModelCallEvent] | None:
    db_run_id = _console_run_db_id_for_run_dir(run_dir)
    if db_run_id is None:
        return None
    try:
        from agentic_company.console.web.db import ConsoleRepository

        repo = ConsoleRepository()
        repo.init_schema()
        return repo.list_model_call_events(db_run_id)
    except Exception:
        RUNTIME_LOGGER.debug("Skipping DB model trace load", exc_info=True)
        return None


def _record_card_log_from_run_event(event: RunEvent) -> None:
    if not _is_card_log_run_event(event):
        return
    try:
        from agentic_company.platform.runtime_db import record_activity_event
        from agentic_company.platform.tool_contracts import ActivityEventRecord

        record_activity_event(
            ActivityEventRecord(
                run_id=str(event.run_id),
                event_id=event.event_id,
                work_item_id=str(event.work_item_id or ""),
                owner_agent=event.agent_id,
                agent_id=event.agent_id,
                tool_name=event.event_type,
                status=event.status,
                message=event.message,
                artifact_ids=tuple(sanitize_trace_data(event.artifact_ids)),
            )
        )
    except Exception:
        RUNTIME_LOGGER.debug("Skipping DB card activity persistence", exc_info=True)


def _is_card_log_run_event(event: RunEvent) -> bool:
    return (
        event.event_type == "codex_agent_message"
        and bool(str(event.work_item_id or "").strip())
        and bool(str(event.message or "").strip())
    )


def run_event_from_mapping(payload: dict[str, Any]) -> RunEvent:
    return RunEvent(
        event_id=str(payload.get("event_id") or ""),
        project_id=_optional_int(payload.get("project_id")),
        run_id=payload.get("run_id") or "",
        work_item_id=_optional_str(payload.get("work_item_id")),
        agent_id=str(payload.get("agent_id") or ""),
        event_type=str(payload.get("event_type") or ""),
        status=str(payload.get("status") or ""),
        message=str(payload.get("message") or ""),
        artifact_ids=_string_list(payload.get("artifact_ids")),
        external_refs=_dict_list(payload.get("external_refs")),
        data=_dict(payload.get("data")),
        created_at=str(payload.get("created_at") or ""),
    )


def tool_call_event_from_mapping(payload: dict[str, Any]) -> ToolCallEvent:
    return ToolCallEvent(
        event_id=str(payload.get("event_id") or ""),
        run_id=payload.get("run_id") or "",
        work_item_id=_optional_str(payload.get("work_item_id")),
        agent_id=str(payload.get("agent_id") or ""),
        tool_name=str(payload.get("tool_name") or ""),
        tool_call_id=str(payload.get("tool_call_id") or ""),
        input_summary=_dict(payload.get("input_summary")),
        output_summary=_dict(payload.get("output_summary")),
        artifact_ids=_string_list(payload.get("artifact_ids")),
        status=str(payload.get("status") or ""),
        failure_mode=_optional_str(payload.get("failure_mode")),
        duration_ms=_optional_int(payload.get("duration_ms")),
        created_at=str(payload.get("created_at") or ""),
    )


def model_call_event_from_mapping(payload: dict[str, Any]) -> ModelCallEvent:
    return ModelCallEvent(
        event_id=str(payload.get("event_id") or ""),
        run_id=payload.get("run_id") or "",
        agent_id=str(payload.get("agent_id") or ""),
        provider=str(payload.get("provider") or ""),
        model=str(payload.get("model") or ""),
        purpose=str(payload.get("purpose") or ""),
        prompt_ref=str(payload.get("prompt_ref") or ""),
        input_tokens=_optional_int(payload.get("input_tokens")),
        output_tokens=_optional_int(payload.get("output_tokens")),
        estimated_cost_usd=_optional_float(payload.get("estimated_cost_usd")),
        status=str(payload.get("status") or ""),
        duration_ms=_optional_int(payload.get("duration_ms")),
        created_at=str(payload.get("created_at") or ""),
    )


def trace_summary(
    run_events: list[RunEvent],
    tool_call_events: list[ToolCallEvent],
    model_call_events: list[ModelCallEvent],
) -> dict[str, Any]:
    artifact_ids = sorted(
        {
            artifact_id
            for event in [*run_events, *tool_call_events]
            for artifact_id in event.artifact_ids
        }
    )
    failures = [
        {
            "event_id": event.event_id,
            "agent_id": event.agent_id,
            "event_type": event.event_type,
            "status": event.status,
            "message": event.message,
        }
        for event in run_events
        if _looks_failed(event.status) or _looks_failed(event.event_type)
    ]
    failures.extend(
        {
            "event_id": event.event_id,
            "agent_id": event.agent_id,
            "tool_name": event.tool_name,
            "status": event.status,
            "failure_mode": event.failure_mode,
        }
        for event in tool_call_events
        if _looks_failed(event.status) or event.failure_mode
    )
    return {
        "duration_ms": _trace_duration_ms(run_events, tool_call_events, model_call_events),
        "agents": _count_by(
            [event.agent_id for event in [*run_events, *tool_call_events, *model_call_events]]
        ),
        "tools": _count_by([event.tool_name for event in tool_call_events]),
        "failures": failures,
        "artifact_ids": artifact_ids,
    }


def sanitize_trace_data(value: Any) -> Any:
    """Return a JSON-safe value with secret-like fields redacted."""

    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if _is_secret_key(key):
                safe[key] = SECRET_REDACTION
            else:
                safe[key] = sanitize_trace_data(raw_value)
        return safe
    if isinstance(value, (list, tuple, set)):
        return [sanitize_trace_data(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def extract_artifact_ids(value: Any) -> list[str]:
    ids: list[str] = []
    _extract_artifact_ids(value, ids)
    return sorted(set(ids))


def build_event_id(kind: TraceEventKind, *parts: Any) -> str:
    content = json.dumps(sanitize_trace_data(list(parts)), sort_keys=True, default=str)
    return f"{kind}_{sha1(content.encode('utf-8')).hexdigest()[:20]}"


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(sanitize_trace_data(payload), sort_keys=True) + "\n")


def _append_jsonl_once(path: Path, payload: dict[str, Any], event_id: str) -> bool:
    if _jsonl_contains_event_id(path, event_id):
        return False
    _append_jsonl(path, payload)
    return True


def _jsonl_contains_event_id(path: Path, event_id: str) -> bool:
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and payload.get("event_id") == event_id:
                    return True
    except OSError:
        return False
    return False


def _event_correlation_key(data: dict[str, Any]) -> str:
    for key in (
        "tool_call_id",
        "codex_execution_id",
        "execution_id",
        "attempt",
        "work_item_id",
        "artifact",
        "status",
    ):
        value = data.get(key)
        if value not in (None, ""):
            return f"{key}:{value}"
    return json.dumps(sanitize_trace_data(data), sort_keys=True, default=str)


def _log_run_event(event: RunEvent) -> None:
    if not _is_operator_run_event(event):
        return
    message = _truncate_operator_text(event.message or event.event_type)
    # Prefer the granular workflow signal for the human label; the event.status
    # itself is the canonical board status.
    status_label = event.data.get("workflow_status") if isinstance(event.data, dict) else None
    RUNTIME_LOGGER.info(
        "RUN %s [%s] %s: %s -> %s - %s",
        event.run_id,
        event.work_item_id or "-",
        _operator_agent_label(event.agent_id),
        _operator_event_label(event.event_type),
        _operator_status_label(str(status_label) if status_label else event.status),
        message,
    )


def _log_tool_call_event(event: ToolCallEvent) -> None:
    update = _dict(event.output_summary.get("dashboard_update"))
    summary = _first_text(
        update.get("comment"),
        update.get("summary"),
        event.output_summary.get("business_summary"),
        event.output_summary.get("summary"),
        event.output_summary.get("message"),
    )
    duration = _duration_label(event.duration_ms)
    status = event.failure_mode or event.status or "-"
    RUNTIME_LOGGER.info(
        "RUN %s [%s] %s: %s -> %s%s - %s",
        event.run_id,
        event.work_item_id or "-",
        _operator_agent_label(event.agent_id),
        _operator_event_label(event.tool_name),
        _operator_status_label(status),
        f" in {duration}" if duration != "-" else "",
        _truncate_operator_text(summary),
    )


def _is_operator_run_event(event: RunEvent) -> bool:
    event_type = event.event_type.lower()
    return any(
        token in event_type
        for token in (
            "delivery_graph",
            "worker_started",
            "worker_completed",
            "tool_completed",
            "blocked",
            "artifact_written",
        )
    )


def _operator_agent_label(agent_id: str) -> str:
    labels = {
        "head-agent": "Coordinator",
        "team-lead-agent": "Delivery Lead",
        "business-analyst-agent": "Business Analyst",
        "architect-agent": "Solution Architect",
        "project-manager-agent": "Delivery Planner",
        "fullstack-agent": "Builder",
        "qa-agent": "Quality Reviewer",
        "qa-codex-agent": "Quality Reviewer",
        "deployment-agent": "Publisher",
        "deployment-codex-agent": "Publisher",
        "handoff-agent": "Release Reporter",
        "handoff-codex-agent": "Release Reporter",
    }
    return labels.get(agent_id, agent_id or "runtime")


def _operator_event_label(value: str) -> str:
    labels = {
        "delivery_graph_started": "delivery started",
        "delivery_graph_completed": "delivery completed",
        "delivery_graph_node_started": "stage started",
        "delivery_graph_node_completed": "stage completed",
        "head_worker_started": "worker started",
        "head_worker_completed": "worker completed",
        "head_tool_completed": "tool completed",
        "team_lead_worker_started": "worker started",
        "team_lead_worker_completed": "worker completed",
        "team_lead_tool_completed": "tool completed",
        "team_lead_blocked_sprint": "sprint blocked",
        "artifact_written": "artifact written",
        "run_business_analyst": "run business analysis",
        "run_architect": "run architecture",
        "run_project_manager": "run delivery planning",
        "run_team_lead": "run delivery lead",
        "run_fullstack": "build feature",
        "run_qa": "quality review",
        "run_handoff": "handoff report",
        "complete_sprint": "complete sprint",
        "block_sprint": "block sprint",
        "inspect_delivery_status": "inspect delivery",
        "inspect_sprint_status": "inspect sprint",
        "codex_exec": "codex execution",
    }
    normalized = str(value or "").strip()
    return labels.get(normalized, normalized.replace("_", " ") or "event")


def _operator_status_label(value: str | None) -> str:
    status = str(value or "").strip()
    if not status:
        return "-"
    labels = {
        "business_analysis_completed": "completed",
        "architecture_completed": "completed",
        "project_management_completed": "completed",
        "codex_completed": "completed",
        "qa_passed": "passed",
        "needs_repair": "needs repair",
        "handoff_ready": "ready",
        "team_lead_sprint_handoff_ready": "sprint ready",
        "team_lead_sprint_blocked": "blocked",
        "fullstack_feature_implemented": "implemented",
        "qa_feature_passed_next_feature_ready": "QA passed",
        "qa_feature_failed_repair_ready": "needs repair",
        "feature_queue_qa_completed_deployment_ready": "deployment ready",
        "ready_for_next_sprint": "ready for next sprint",
        "ready_to_complete": "ready to complete",
        "failed": "failed",
        "blocked": "blocked",
        "running": "running",
        "done": "done",
        "passed": "passed",
        "ready": "ready",
    }
    return labels.get(status, status.replace("_", " "))


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _truncate_operator_text(value: str, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _duration_label(duration_ms: int | None) -> str:
    if duration_ms is None:
        return "-"
    seconds = max(0, int(duration_ms / 1000))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remainder = minutes % 60
    return f"{hours}h {remainder}m" if remainder else f"{hours}h"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _is_secret_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return normalized in SECRET_KEY_NAMES or normalized.endswith(SECRET_KEY_SUFFIXES)


def _extract_artifact_ids(value: Any, ids: list[str]) -> None:
    if isinstance(value, dict):
        artifact_id = value.get("artifact_id")
        if isinstance(artifact_id, str) and artifact_id.startswith(ARTIFACT_ID_PREFIX):
            ids.append(artifact_id)
        for item in value.values():
            _extract_artifact_ids(item, ids)
    elif isinstance(value, list | tuple | set):
        for item in value:
            _extract_artifact_ids(item, ids)
    elif isinstance(value, str) and value.startswith(ARTIFACT_ID_PREFIX):
        ids.append(value)


def _external_refs_from_data(data: dict[str, Any]) -> list[dict[str, Any]]:
    refs = data.get("external_refs")
    return _dict_list(refs)


def _safe_external_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe_refs: list[dict[str, Any]] = []
    for ref in refs:
        safe = sanitize_trace_data(ref)
        if isinstance(safe, dict):
            safe_refs.append(safe)
    return safe_refs


def _trace_duration_ms(
    run_events: list[RunEvent],
    tool_call_events: list[ToolCallEvent],
    model_call_events: list[ModelCallEvent],
) -> int:
    durations = [
        duration
        for duration in [
            *[_optional_int(event.data.get("duration_ms")) for event in run_events],
            *[event.duration_ms for event in tool_call_events],
            *[event.duration_ms for event in model_call_events],
        ]
        if duration is not None and duration >= 0
    ]
    return sum(durations)


def _count_by(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = value or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _looks_failed(value: str) -> bool:
    return classify_work_item_status(value) is WorkItemStatus.BLOCKED


def _looks_successful(value: str) -> bool:
    return classify_work_item_status(value) is WorkItemStatus.DONE


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value] if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _optional_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
