"""Transient runtime cache/pubsub helpers.

Postgres remains the source of truth. This module is intentionally limited to
ephemeral coordination: heartbeat/status cache, stop flags, and update signals.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

LOGGER = logging.getLogger(__name__)


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
    _redis_errors: tuple[type[Exception], ...] = ()

    def __post_init__(self) -> None:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - exercised only without app extra
            raise RuntimeError("Redis runtime cache requires installing the app extra.") from exc
        self._redis_errors = (redis.exceptions.RedisError,)
        self._client = redis.Redis.from_url(
            self.url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    def publish_run_event(self, run_id: str, message: str) -> None:
        try:
            self._client.publish(f"events:run:{run_id}", message)
        except self._redis_errors as exc:
            LOGGER.warning("Redis runtime event publish failed run_id=%s error=%s", run_id, exc)

    def set_stop_requested(self, run_id: str, *, ttl_seconds: int = 3600) -> None:
        try:
            self._client.set(f"run:{run_id}:stop_requested", "1", ex=ttl_seconds)
        except self._redis_errors as exc:
            LOGGER.warning("Redis runtime stop flag write failed run_id=%s error=%s", run_id, exc)

    def stop_requested(self, run_id: str) -> bool:
        try:
            return bool(self._client.exists(f"run:{run_id}:stop_requested"))
        except self._redis_errors as exc:
            LOGGER.warning("Redis runtime stop flag read failed run_id=%s error=%s", run_id, exc)
            return False


def redis_error_types() -> tuple[type[Exception], ...]:
    try:
        import redis
    except ImportError:
        return ()
    return (redis.exceptions.RedisError,)


def runtime_cache_from_env() -> RuntimeCache:
    url = os.getenv("AGENTIC_REDIS_URL", "").strip() or os.getenv("REDIS_URL", "").strip()
    if not url:
        return NoopRuntimeCache()
    return RedisRuntimeCache(url)


__all__ = [
    "NoopRuntimeCache",
    "RedisRuntimeCache",
    "RuntimeCache",
    "redis_error_types",
    "runtime_cache_from_env",
]
