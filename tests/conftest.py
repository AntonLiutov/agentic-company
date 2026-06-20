from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest


@pytest.fixture(scope="session")
def postgres_test_database_url() -> str:
    """Return a schema-isolated PostgreSQL URL for the test session."""

    import os

    import psycopg

    base_url = os.getenv(
        "AGENTIC_TEST_DATABASE_URL",
        "postgresql://agentic:agentic_dev_password@127.0.0.1:54329/agentic_company",
    ).strip()
    schema = f"adl_test_{os.getpid()}"
    # connect_timeout so a down DB fails fast (Windows otherwise stalls on SYN
    # retransmits and the whole session hangs with no output). Skip — not hang —
    # with a clear "start the services" message.
    try:
        with psycopg.connect(base_url, autocommit=True, connect_timeout=5) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.execute(f'CREATE SCHEMA "{schema}"')
    except psycopg.OperationalError as exc:
        pytest.skip(
            f"PostgreSQL not reachable ({base_url.rsplit('@', 1)[-1]}): {exc}. "
            "Start it: docker compose -f docker-compose.dev.yml up -d postgres redis"
        )
    separator = "&" if "?" in base_url else "?"
    schema_url = f"{base_url}{separator}options={quote(f'-csearch_path={schema}', safe='')}"
    yield schema_url
    with psycopg.connect(base_url, autocommit=True, connect_timeout=5) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


@pytest.fixture(autouse=True)
def isolate_unit_database_env(
    monkeypatch: pytest.MonkeyPatch,
    postgres_test_database_url: str,
) -> None:
    """Keep unit tests on an isolated PostgreSQL schema by default."""

    monkeypatch.setenv("AGENTIC_DATABASE_URL", postgres_test_database_url)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APP_SECRET_KEY", "test-app-secret-key-0123456789ab")
    # Unit tests must not spin up the async board-mirror pool (hundreds of cases
    # in one process) or do any real board I/O; the mirror is exercised directly
    # in its own tests and validated live.
    monkeypatch.setenv("AGENTIC_DISABLE_MIRROR", "1")

    import psycopg

    with psycopg.connect(postgres_test_database_url, autocommit=True, connect_timeout=5) as conn:
        rows = conn.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = current_schema()
              AND tablename NOT IN ('alembic_version', 'schema_metadata')
            """
        ).fetchall()
        tables = [str(row[0]) for row in rows]
        if tables:
            quoted = ", ".join(f'"{table}"' for table in tables)
            conn.execute(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE")


@pytest.fixture
def sample_web_app_requirements() -> str:
    return """# Web App MVP Requirements

Project name: Simple LLM Chat

Goal:
Create a local Streamlit app where a user can chat with an LLM.

Target user:
A solo builder testing simple assistant ideas locally.

Core features:
- User can enter a message
- App sends the message to an LLM

Required configuration:
- OPENAI_API_KEY

Preferred stack:
- Python
- Streamlit

Acceptance criteria:
- App starts locally with Streamlit
"""


@pytest.fixture
def write_sample_requirements(sample_web_app_requirements: str):
    def write(path: Path) -> Path:
        path.write_text(sample_web_app_requirements, encoding="utf-8")
        return path

    return write
