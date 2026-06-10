"""Standardized runtime logging.

:func:`log_runtime_event` emits one uniform terminal line per runtime event:
``run=<id> stage=<stage> agent=<agent> status=<canonical> event=<event> - <message>``.
"""

from __future__ import annotations

import logging

from agentic_company.platform.status import classify_work_item_status

RUNTIME_LOGGER = logging.getLogger("agentic_company.runtime")


def log_runtime_event(
    *,
    run_id: str,
    agent: str,
    event: str,
    status: str = "",
    message: str = "",
    stage: str = "",
    level: int = logging.INFO,
) -> None:
    """Emit one standardized runtime log line; ``status`` is folded to canonical."""

    canonical = classify_work_item_status(status).value if status.strip() else "-"
    RUNTIME_LOGGER.log(
        level,
        "run=%s stage=%s agent=%s status=%s event=%s%s",
        run_id or "-",
        stage or "-",
        agent or "-",
        canonical,
        event or "-",
        f" - {message}" if message else "",
    )


__all__ = ["RUNTIME_LOGGER", "log_runtime_event"]
