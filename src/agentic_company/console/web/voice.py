"""Voice provider helpers for the product console."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

SPEECHMATICS_MANAGEMENT_URL = "https://mp.speechmatics.com/v1/api_keys?type=rt"
DEFAULT_SPEECHMATICS_RT_URL = "wss://eu2.rt.speechmatics.com/v2"

SPEECHMATICS_LANGUAGES: list[dict[str, str | bool]] = [
    {"code": "en", "name": "English", "popular": True, "recommended": True},
    {"code": "it", "name": "Italian", "popular": True, "recommended": True},
    {"code": "uk", "name": "Ukrainian", "popular": True, "recommended": True},
    {"code": "es", "name": "Spanish", "popular": True, "recommended": True},
    {"code": "de", "name": "German", "popular": True, "recommended": True},
    {"code": "fr", "name": "French", "popular": True, "recommended": True},
    {"code": "pt", "name": "Portuguese", "popular": True},
    {"code": "ru", "name": "Russian", "popular": True},
    {"code": "nl", "name": "Dutch", "popular": True},
    {"code": "pl", "name": "Polish", "popular": True},
    {"code": "ar", "name": "Arabic", "popular": True},
    {"code": "cmn", "name": "Mandarin Chinese", "popular": True},
    {"code": "yue", "name": "Cantonese Chinese", "popular": True},
    {"code": "ja", "name": "Japanese", "popular": True},
    {"code": "ko", "name": "Korean"},
    {"code": "hi", "name": "Hindi"},
    {"code": "tr", "name": "Turkish"},
    {"code": "id", "name": "Indonesian"},
]


class SpeechmaticsTokenError(RuntimeError):
    """Raised when a realtime token cannot be created."""


def speechmatics_api_key() -> str:
    return os.getenv("SPEECHMATICS_API_KEY", "").strip()


def speechmatics_rt_url() -> str:
    configured = os.getenv("SPEECHMATICS_RT_URL", DEFAULT_SPEECHMATICS_RT_URL).strip()
    return configured.rstrip("/")


def speechmatics_region() -> str:
    return os.getenv("SPEECHMATICS_REGION", "eu").strip() or "eu"


def speechmatics_configured() -> bool:
    return bool(speechmatics_api_key())


def dictation_languages() -> list[dict[str, str | bool]]:
    return SPEECHMATICS_LANGUAGES


def language_label(code: str) -> str:
    normalized = normalize_language_code(code)
    for language in SPEECHMATICS_LANGUAGES:
        if language["code"] == normalized:
            return f"{language['name']} ({language['code']})"
    return "English (en)"


def normalize_language_code(value: str) -> str:
    cleaned = str(value or "").strip()
    if "(" in cleaned and cleaned.endswith(")"):
        cleaned = cleaned.rsplit("(", 1)[-1].rstrip(")")
    cleaned = cleaned.lower()
    supported = {str(language["code"]) for language in SPEECHMATICS_LANGUAGES}
    return cleaned if cleaned in supported else "en"


def create_speechmatics_realtime_token(*, ttl_seconds: int = 60) -> str:
    api_key = speechmatics_api_key()
    if not api_key:
        raise SpeechmaticsTokenError("Speechmatics is not configured")
    payload = json.dumps({"ttl": ttl_seconds, "region": speechmatics_region()}).encode("utf-8")
    request = urllib.request.Request(
        SPEECHMATICS_MANAGEMENT_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
    except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise SpeechmaticsTokenError("Speechmatics token request failed") from exc
    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SpeechmaticsTokenError("Speechmatics token response was invalid") from exc
    token = str(data.get("key_value") or data.get("token") or "").strip()
    if not token:
        raise SpeechmaticsTokenError("Speechmatics token response was empty")
    return token
