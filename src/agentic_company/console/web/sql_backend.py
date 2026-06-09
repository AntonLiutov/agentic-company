"""Database backend helpers for the FastAPI console repository."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

_POSTGRES_POOLS: dict[str, Any] = {}
RETRYABLE_SQLSTATES = {"40P01", "40001", "55P03"}


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """Runtime database settings.

    PostgreSQL is the only supported product/runtime database.
    """

    url: str = ""

    @property
    def dialect(self) -> str:
        normalized = self.url.lower()
        if normalized.startswith(("postgres://", "postgresql://")):
            return "postgres"
        raise ValueError("AGENTIC_DATABASE_URL must be a PostgreSQL connection URL.")

    @property
    def is_postgres(self) -> bool:
        return self.dialect == "postgres"


@contextmanager
def connect_database(settings: DatabaseSettings) -> Iterator[Any]:
    if not settings.is_postgres:  # pragma: no cover - dialect property raises first
        raise ValueError("PostgreSQL is the only supported runtime database.")
    with _connect_postgres(settings.url) as conn:
        yield conn


@contextmanager
def _connect_postgres(url: str) -> Iterator[_PostgresConnection]:
    try:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
    except ImportError as exc:  # pragma: no cover - exercised only without app extra
        raise RuntimeError(
            "PostgreSQL DATABASE_URL requires installing the app extra with psycopg pool support."
        ) from exc

    pool = _postgres_pool(url, ConnectionPool, dict_row)
    with pool.connection() as conn:
        wrapper = _PostgresConnection(conn)
        try:
            yield wrapper
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _postgres_pool(url: str, pool_factory: Any, row_factory: Any) -> Any:
    pool = _POSTGRES_POOLS.get(url)
    if pool is not None:
        return pool
    min_size, max_size = postgres_pool_bounds()
    pool = pool_factory(
        conninfo=url,
        min_size=min_size,
        max_size=max_size,
        kwargs={"row_factory": row_factory},
        open=False,
    )
    pool.open()
    _POSTGRES_POOLS[url] = pool
    return pool


def postgres_pool_bounds() -> tuple[int, int]:
    min_size = _int_env("AGENTIC_POSTGRES_POOL_MIN", default=1, minimum=0)
    max_size = _int_env("AGENTIC_POSTGRES_POOL_MAX", default=10, minimum=1)
    if max_size < min_size:
        raise ValueError("AGENTIC_POSTGRES_POOL_MAX must be greater than or equal to min size")
    return min_size, max_size


def retry_database_operation(operation: Any) -> Any:
    """Run a short DB operation with retries for transient database concurrency errors."""

    attempts = _int_env("AGENTIC_DATABASE_RETRY_ATTEMPTS", default=5, minimum=1)
    delay_seconds = float(os.getenv("AGENTIC_DATABASE_RETRY_DELAY_SECONDS", "0.1") or "0.1")
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:
            if not is_retryable_database_error(exc) or attempt == attempts - 1:
                raise
            last_error = exc
            time.sleep(delay_seconds * (2**attempt))
    if last_error:
        raise last_error
    return operation()


def is_retryable_database_error(exc: BaseException) -> bool:
    sqlstate = str(getattr(exc, "sqlstate", "") or "")
    if sqlstate in RETRYABLE_SQLSTATES:
        return True
    cause = exc.__cause__
    if cause is not None and is_retryable_database_error(cause):
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(
        token in text
        for token in (
            "deadlock detected",
            "deadlockdetected",
            "serialization failure",
            "could not serialize access",
            "lock timeout",
            "locknotavailable",
        )
    )


def _int_env(name: str, *, default: int, minimum: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


class _StaticCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.rowcount = len(rows)
        self.lastrowid = 0

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _PostgresCursor:
    def __init__(self, connection: Any, cursor: Any) -> None:
        self._connection = connection
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    @property
    def lastrowid(self) -> int:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT LASTVAL() AS id")
            row = cursor.fetchone()
        return int(row["id"]) if row else 0

    def fetchone(self) -> dict[str, Any] | None:
        return self._cursor.fetchone()

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._cursor.fetchall())


class _PostgresConnection:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _PostgresCursor | _StaticCursor:
        cursor = self._connection.cursor()
        cursor.execute(_translate_sql(sql), tuple(params))
        return _PostgresCursor(self._connection, cursor)

    def executescript(self, script: str) -> None:
        raise RuntimeError("Schema changes must run through Alembic migrations.")


def _translate_sql(sql: str) -> str:
    return _replace_query_placeholders(sql)


def _replace_query_placeholders(sql: str) -> str:
    output: list[str] = []
    in_single = False
    in_double = False
    for index, char in enumerate(sql):
        prev = sql[index - 1] if index else ""
        if char == "'" and not in_double and prev != "\\":
            in_single = not in_single
        elif char == '"' and not in_single and prev != "\\":
            in_double = not in_double
        if char == "?" and not in_single and not in_double:
            output.append("%s")
        else:
            output.append(char)
    return "".join(output)


__all__ = [
    "DatabaseSettings",
    "connect_database",
    "is_retryable_database_error",
    "postgres_pool_bounds",
    "retry_database_operation",
]
