"""Database backend helpers for the FastAPI console repository."""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_POSTGRES_POOLS: dict[str, Any] = {}


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """Runtime database settings.

    SQLite remains the local zero-dependency default. PostgreSQL is selected by
    passing a postgres URL through AGENTIC_DATABASE_URL or DATABASE_URL.
    """

    url: str = ""
    sqlite_path: Path | None = None

    @property
    def dialect(self) -> str:
        normalized = self.url.lower()
        if normalized.startswith(("postgres://", "postgresql://")):
            return "postgres"
        return "sqlite"

    @property
    def is_postgres(self) -> bool:
        return self.dialect == "postgres"


@contextmanager
def connect_database(settings: DatabaseSettings) -> Iterator[Any]:
    if settings.is_postgres:
        yield from _connect_postgres(settings.url)
        return
    if settings.sqlite_path is None:
        raise ValueError("SQLite database settings require sqlite_path")
    settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.sqlite_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
        pragma_table = _pragma_table_info(sql)
        if pragma_table:
            return _StaticCursor(_postgres_table_info(self._connection, pragma_table))
        cursor = self._connection.cursor()
        cursor.execute(_translate_sql(sql), tuple(params))
        return _PostgresCursor(self._connection, cursor)

    def executescript(self, script: str) -> None:
        for statement in _split_sql_script(script):
            self.execute(statement)


def _pragma_table_info(sql: str) -> str:
    match = re.fullmatch(r"\s*PRAGMA\s+table_info\(([\w_]+)\)\s*", sql, flags=re.I)
    return match.group(1) if match else ""


def _postgres_table_info(connection: Any, table: str) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name AS name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        return list(cursor.fetchall())


def _split_sql_script(script: str) -> Iterable[str]:
    for statement in script.split(";"):
        stripped = statement.strip()
        if stripped:
            yield stripped


def _translate_sql(sql: str) -> str:
    translated = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
        sql,
        flags=re.I,
    )
    return _replace_sqlite_placeholders(translated)


def _replace_sqlite_placeholders(sql: str) -> str:
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
    "postgres_pool_bounds",
]
