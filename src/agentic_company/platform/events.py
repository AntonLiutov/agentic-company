"""Shared workflow event-log helpers."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def write_event(
    event_log: Path,
    run_id: str,
    agent_id: str,
    event: str,
    data: dict[str, object],
) -> None:
    payload = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "run_id": run_id,
        "agent_id": agent_id,
        "event": event,
        "data": data,
    }
    with event_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    LOGGER.info(
        "event_written run_id=%s agent=%s event=%s data_keys=%s",
        run_id,
        agent_id,
        event,
        sorted(data),
    )
