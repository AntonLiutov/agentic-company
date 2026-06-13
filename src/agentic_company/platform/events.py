"""Shared workflow event-log helpers."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from agentic_company.platform.run_trace import record_run_event
from agentic_company.platform.status import classify_work_item_status

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
    timestamp = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    raw_status = str(data.get("status") or "")
    canonical_status = classify_work_item_status(raw_status).value if raw_status.strip() else ""
    # The event carries the canonical board status; the granular workflow signal
    # (e.g. team_lead_sprint_handoff_ready) is preserved as detail, not status.
    event_data = dict(data)
    if raw_status and raw_status != canonical_status:
        event_data.setdefault("workflow_status", raw_status)
    try:
        record_run_event(
            run_dir,
            run_id=run_id,
            agent_id=agent_id,
            event_type=event,
            status=canonical_status,
            message=str(data.get("message") or data.get("summary") or event),
            work_item_id=str(data.get("work_item_id") or "") or None,
            data=event_data,
            created_at=timestamp,
        )
    except Exception:
        LOGGER.exception("structured_trace_write_failed run_id=%s event=%s", run_id, event)
