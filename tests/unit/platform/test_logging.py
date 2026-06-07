import logging

from agentic_company.platform.logging import configure_logging


def test_configure_logging_adds_single_handler_and_quiets_vendor_logs(monkeypatch):
    root_logger = logging.getLogger()
    existing_handlers = list(root_logger.handlers)
    existing_level = root_logger.level
    vendor_loggers = ["httpx", "httpcore", "google_genai", "google_genai.models"]
    existing_vendor_levels = {name: logging.getLogger(name).level for name in vendor_loggers}
    root_logger.handlers = []
    monkeypatch.delenv("AGENTIC_COMPANY_VENDOR_LOGS", raising=False)
    monkeypatch.setenv("AGENTIC_COMPANY_LOG_LEVEL", "debug")

    try:
        configure_logging()
        configure_logging()

        agentic_handlers = [
            handler
            for handler in root_logger.handlers
            if getattr(handler, "_agentic_company_handler", False)
        ]
        assert len(agentic_handlers) == 1
        assert root_logger.level == logging.DEBUG
        assert all(logging.getLogger(name).level == logging.WARNING for name in vendor_loggers)
    finally:
        root_logger.handlers = existing_handlers
        root_logger.setLevel(existing_level)
        for name, level in existing_vendor_levels.items():
            logging.getLogger(name).setLevel(level)


def test_configure_logging_respects_vendor_logs_flag_and_default_level(monkeypatch):
    root_logger = logging.getLogger()
    existing_handlers = list(root_logger.handlers)
    existing_level = root_logger.level
    httpx_logger = logging.getLogger("httpx")
    existing_httpx_level = httpx_logger.level
    root_logger.handlers = []
    httpx_logger.setLevel(logging.NOTSET)
    monkeypatch.setenv("AGENTIC_COMPANY_VENDOR_LOGS", "yes")
    monkeypatch.setenv("AGENTIC_COMPANY_LOG_LEVEL", "not-a-level")

    try:
        configure_logging(default_level="ERROR")

        assert root_logger.level == logging.INFO
        assert httpx_logger.level == logging.NOTSET
    finally:
        root_logger.handlers = existing_handlers
        root_logger.setLevel(existing_level)
        httpx_logger.setLevel(existing_httpx_level)
