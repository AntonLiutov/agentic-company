"""Security helpers shared across agents and console views."""

from __future__ import annotations

import re


def redact_sensitive_output(output: str) -> str:
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-***REDACTED***", output)
    redacted = re.sub(
        r"(?im)^(\s*(?:OPENAI_API_KEY|CODEX_API_KEY|GOOGLE_API_KEY|GEMINI_API_KEY|API_KEY|TOKEN|SECRET|PASSWORD)\s*[:=]\s*).+$",
        r"\1***REDACTED***",
        redacted,
    )
    return redacted
