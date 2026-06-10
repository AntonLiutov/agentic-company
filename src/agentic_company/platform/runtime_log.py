"""Standardized runtime logging contract.

Every runtime event logs through :func:`log_runtime_event`, which emits ONE
uniform terminal line carrying the four things an operator actually needs:

    run=<id> stage=<stage> agent=<agent> status=<canonical> event=<event> — <message>

This replaces ad-hoc, per-agent log formatting (and statuses concatenated into
free-form strings) with a single contract: the *stage* and *agent* are their own
fields, the *status* is the canonical board status (see :mod:`platform.status`),
and the granular event name lives in ``event``. Nothing else should hand-format
runtime log lines for the terminal.
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
    """Emit one standardized runtime log line.

    ``status`` may be any raw runtime string; it is folded to the canonical board
    status for display so the terminal shows a consistent vocabulary regardless
    of what an agent emitted.
    """

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
