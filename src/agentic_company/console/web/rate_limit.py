"""In-memory sliding-window rate limiting for sensitive console endpoints."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """Fixed-capacity sliding-window limiter keyed by an arbitrary string.

    Sized for a single-process console: each key may make up to ``max_attempts``
    calls per ``window_seconds``. Used to throttle brute-force login attempts.
    """

    def __init__(self, *, max_attempts: int, window_seconds: float) -> None:
        self._max = max_attempts
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Record an attempt for ``key`` and return whether it is within the limit."""

        moment = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            cutoff = moment - self._window
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self._max:
                return False
            hits.append(moment)
            return True

    def reset(self, key: str) -> None:
        """Clear recorded attempts for ``key`` (e.g. after a successful login)."""

        with self._lock:
            self._hits.pop(key, None)


__all__ = ["RateLimiter"]
