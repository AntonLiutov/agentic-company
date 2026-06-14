"""Asynchronous, parallel dispatch for the external-board mirror.

A run must never block on GitHub. Mirror work is submitted here and applied on a
small pool of **daemon** worker threads draining a queue: different work items are
mirrored concurrently, while operations for the SAME work item are serialised (so
its card is created before its status moves and two threads never create
duplicate issues).

Daemon threads are used deliberately: they are killed at interpreter exit, so a
slow/in-flight GitHub call can never hang console shutdown (or a test run).
Submitting is instant and best-effort; ``flush`` lets a caller optionally wait
(bounded) for one item's mirror to settle and returns False on timeout.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from collections.abc import Callable
from typing import Any

LOGGER = logging.getLogger("agentic_company.mirror_dispatch")

DEFAULT_WORKERS = 8
_SENTINEL = object()


class MirrorDispatcher:
    """A fixed pool of daemon workers that serialise work per key."""

    def __init__(self, max_workers: int) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._guard = threading.Lock()
        self._locks: dict[Any, threading.Lock] = {}
        self._events: dict[Any, threading.Event] = {}
        self._threads: list[threading.Thread] = []
        for i in range(max_workers):
            thread = threading.Thread(target=self._worker, name=f"mirror-{i}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def submit(self, key: Any, fn: Callable[[], None]) -> None:
        done = threading.Event()
        with self._guard:
            self._events[key] = done  # latest pending op for this key
        self._queue.put((key, fn, done))

    def flush(self, key: Any, timeout: float) -> bool:
        with self._guard:
            done = self._events.get(key)
        if done is None:
            return True  # nothing pending for this key
        return done.wait(timeout)  # False on timeout -> caller proceeds anyway

    def _key_lock(self, key: Any) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _SENTINEL:
                    return
                key, fn, done = item
                try:
                    with self._key_lock(key):  # one op at a time per work item
                        fn()
                except Exception as exc:  # a board outage must not kill the worker
                    LOGGER.warning("Mirror task failed for %s: %s", key, exc)
                finally:
                    done.set()
            finally:
                self._queue.task_done()

    def shutdown(self, *, wait: bool = False) -> None:
        for _ in self._threads:
            self._queue.put(_SENTINEL)
        if wait:
            for thread in self._threads:
                thread.join(timeout=2)


_DISPATCHER: MirrorDispatcher | None = None
_INIT_LOCK = threading.Lock()


def _dispatcher() -> MirrorDispatcher:
    global _DISPATCHER
    if _DISPATCHER is None:
        with _INIT_LOCK:
            if _DISPATCHER is None:
                _DISPATCHER = MirrorDispatcher(_max_workers())
    return _DISPATCHER


def _max_workers() -> int:
    raw = os.getenv("AGENTIC_MIRROR_WORKERS", "").strip()
    if not raw:
        return DEFAULT_WORKERS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_WORKERS


def _disabled() -> bool:
    # Unit tests run hundreds of cases in one process and must not spin up the
    # background pool (or do any board I/O); they set AGENTIC_DISABLE_MIRROR.
    return os.getenv("AGENTIC_DISABLE_MIRROR", "").strip().lower() in ("1", "true", "yes")


def submit_mirror(key: Any, fn: Callable[[], None]) -> None:
    """Schedule ``fn`` on the mirror pool (instant, best-effort)."""
    if _disabled():
        return
    try:
        _dispatcher().submit(key, fn)
    except Exception as exc:  # dispatch must never break a run
        LOGGER.warning("Mirror dispatch failed for %s: %s", key, exc)


def flush_mirror(key: Any, timeout: float = 6.0) -> bool:
    """Wait (bounded) for a key's latest mirror to finish; False on timeout."""
    if _disabled():
        return True
    try:
        return _dispatcher().flush(key, timeout)
    except Exception:
        return False


def reset_dispatcher() -> None:
    """Tear down the dispatcher (tests only)."""
    global _DISPATCHER
    if _DISPATCHER is not None:
        _DISPATCHER.shutdown(wait=True)
        _DISPATCHER = None
