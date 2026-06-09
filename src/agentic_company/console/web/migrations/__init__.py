"""Alembic migration runner for the FastAPI console database."""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).resolve().parent


def database_url_from_env() -> str:
    url = os.getenv("AGENTIC_DATABASE_URL", "").strip() or os.getenv("DATABASE_URL", "").strip()
    if not url.lower().startswith(("postgres://", "postgresql://")):
        raise RuntimeError("AGENTIC_DATABASE_URL must be set to a PostgreSQL connection URL.")
    return url


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", _sqlalchemy_url(database_url).replace("%", "%%"))
    return config


def upgrade_database(database_url: str | None = None) -> None:
    """Upgrade the configured PostgreSQL database to the latest schema."""

    url = database_url or database_url_from_env()
    command.upgrade(alembic_config(url), "head")


def main() -> None:
    upgrade_database()


def _sqlalchemy_url(database_url: str) -> str:
    normalized = database_url.strip()
    if normalized.startswith("postgresql+psycopg://"):
        return normalized
    if normalized.startswith("postgresql://"):
        return "postgresql+psycopg://" + normalized.removeprefix("postgresql://")
    if normalized.startswith("postgres://"):
        return "postgresql+psycopg://" + normalized.removeprefix("postgres://")
    return normalized


__all__ = ["alembic_config", "database_url_from_env", "main", "upgrade_database"]
