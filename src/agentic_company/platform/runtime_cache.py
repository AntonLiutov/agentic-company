"""Transient runtime cache/pubsub helpers.

Postgres remains the source of truth. This module is intentionally limited to
ephemeral coordination: locks, heartbeat/status cache, and update signals.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol


class RuntimeCache(Protocol):
    def publish_run_event(self, run_id: str, message: str) -> None:
        """Notify listeners that canonical run data changed."""

    def set_stop_requested(self, run_id: str, *, ttl_seconds: int = 3600) -> None:
        """Set a short-lived stop flag."""

    def stop_requested(self, run_id: str) -> bool:
        """Return whether a stop flag is currently present."""


@dataclass(slots=True)
class NoopRuntimeCache:
    """Local/default cache implementation when Redis is not configured."""

    def publish_run_event(self, run_id: str, message: str) -> None:
        return None

    def set_stop_requested(self, run_id: str, *, ttl_seconds: int = 3600) -> None:
        return None

    def stop_requested(self, run_id: str) -> bool:
        return False


@dataclass(slots=True)
class RedisRuntimeCache:
    url: str
    _client: Any = None

    def __post_init__(self) -> None:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - exercised only without app extra
            raise RuntimeError("Redis runtime cache requires installing the app extra.") from exc
        self._client = redis.Redis.from_url(self.url, decode_responses=True)

    def publish_run_event(self, run_id: str, message: str) -> None:
        self._client.publish(f"events:run:{run_id}", message)

    def set_stop_requested(self, run_id: str, *, ttl_seconds: int = 3600) -> None:
        self._client.setex(f"run:{run_id}:stop_requested", ttl_seconds, "1")

    def stop_requested(self, run_id: str) -> bool:
        return bool(self._client.exists(f"run:{run_id}:stop_requested"))


def runtime_cache_from_env() -> RuntimeCache:
    url = os.getenv("AGENTIC_REDIS_URL", "").strip() or os.getenv("REDIS_URL", "").strip()
    if not url:
        return NoopRuntimeCache()
    return RedisRuntimeCache(url)


__all__ = [
    "NoopRuntimeCache",
    "RedisRuntimeCache",
    "RuntimeCache",
    "runtime_cache_from_env",
]
