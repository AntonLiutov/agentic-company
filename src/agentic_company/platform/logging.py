"""Shared logging setup for local console and runner commands."""

from __future__ import annotations

import logging
import os
import sys


def configure_logging(default_level: str = "INFO") -> None:
    """Configure process-wide logging without duplicating handlers on repeated app starts."""

    level_name = os.getenv("AGENTIC_COMPANY_LOG_LEVEL", default_level).upper()
    level = getattr(logging, level_name, logging.INFO)
    root_logger = logging.getLogger()

    if not any(
        getattr(handler, "_agentic_company_handler", False) for handler in root_logger.handlers
    ):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        handler._agentic_company_handler = True  # type: ignore[attr-defined]
        root_logger.addHandler(handler)

    root_logger.setLevel(level)
