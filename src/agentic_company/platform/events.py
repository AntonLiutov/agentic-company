"""Shared workflow event-log helpers."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from agentic_company.platform.run_trace import record_run_event

LOGGER = logging.getLogger(__name__)


def write_event(
    run_dir: Path,
    run_id: str,
    agent_id: str,
    event: str,
    data: dict[str, object],
) -> None:
    if run_dir.name == "events.jsonl":
        run_dir = run_dir.parent
    payload = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "run_id": run_id,
        "agent_id": agent_id,
        "event": event,
        "data": data,
    }
    try:
        record_run_event(
            run_dir,
            run_id=run_id,
            agent_id=agent_id,
            event_type=event,
            status=str(data.get("status") or ""),
            message=str(data.get("message") or data.get("summary") or event),
            work_item_id=str(data.get("work_item_id") or "") or None,
            data=data,
            created_at=str(payload["timestamp"]),
        )
    except Exception:
        LOGGER.exception("structured_trace_write_failed run_id=%s event=%s", run_id, event)
    LOGGER.info(
        "event_written run_id=%s agent=%s event=%s data_keys=%s",
        run_id,
        agent_id,
        event,
        sorted(data),
    )
