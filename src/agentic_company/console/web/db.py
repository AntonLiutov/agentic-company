"""Repository for the FastAPI product console."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentic_company.console.support import repo_root
from agentic_company.console.web.auth import (
    encrypt_secret,
    hash_password,
    hash_token,
    mask_secret,
    new_session_token,
    verify_password,
)
from agentic_company.console.web.sql_backend import DatabaseSettings, connect_database
from agentic_company.platform.artifact_registry import ArtifactRecord, artifact_record_from_mapping
from agentic_company.platform.run_trace import (
    ModelCallEvent,
    RunEvent,
    ToolCallEvent,
    model_call_event_from_mapping,
    run_event_from_mapping,
    sanitize_trace_data,
    tool_call_event_from_mapping,
)
from agentic_company.platform.work_item_contracts import HEAD_PLANNING_ITEMS

SESSION_DAYS = 14
CONSOLE_SCHEMA_VERSION = "2026-06-06.1"


@dataclass(frozen=True, slots=True)
class User:
    id: int
    email: str
    username: str
    created_at: str


@dataclass(frozen=True, slots=True)
class Project:
    id: int
    owner_user_id: int | None
    name: str
    mode: str
    complexity: str
    status: str
    visibility: str
    created_at: str
    updated_at: str
    request_text: str = ""
    latest_run_id: int | None = None
    latest_run_status: str = ""
    generated_app_url: str = ""


@dataclass(frozen=True, slots=True)
class Run:
    id: int
    project_id: int
    run_uid: str
    run_dir: str
    target_project_dir: str
    status: str
    mode: str
    reasoning: str
    generated_app_url: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ProviderCredential:
    provider: str
    masked_value: str
    encrypted_value: str
    storage_mode: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class WorkItem:
    id: int
    run_id: int
    work_item_id: str
    title: str
    sprint_id: str
    delivery_order: int
    status: str
    lane: str
    owner_agent: str
    assigned_agent: str
    active: bool
    source_refs: list[str]
    artifact_ids: list[str]
    blocker: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    id: int
    run_id: int
    work_item_id: str
    owner_agent: str
    agent_id: str
    tool_name: str
    message: str
    status: str
    artifact_ids: list[str]
    visibility: str
    created_at: str


@dataclass(frozen=True, slots=True)
class RawLogEvent:
    id: int
    run_id: int
    work_item_id: str
    sprint_id: str
    agent_id: str
    tool_name: str
    tool_call_id: str
    seq: int
    level: str
    stream: str
    message: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ConsoleProcessState:
    id: int
    run_id: int
    process_name: str
    status: str
    thread_name: str
    env_keys: list[str]
    stop_requested_at: str
    error: str
    created_at: str
    updated_at: str


def default_db_path() -> Path:
    configured = os.getenv("AGENTIC_CONSOLE_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return repo_root() / "data" / "console.db"


def default_database_url() -> str:
    return os.getenv("AGENTIC_DATABASE_URL", "").strip() or os.getenv("DATABASE_URL", "").strip()


class ConsoleRepository:
    """Product console repository with explicit user isolation methods."""

    def __init__(self, db_path: Path | None = None, *, database_url: str = "") -> None:
        self.database_url = database_url.strip() or ("" if db_path else default_database_url())
        self.db_path = db_path or default_db_path()
        self.settings = DatabaseSettings(url=self.database_url, sqlite_path=self.db_path)
        if not self.settings.is_postgres:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[Any]:
        with connect_database(self.settings) as conn:
            yield conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    request_text TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'simple_prototype',
                    complexity TEXT NOT NULL DEFAULT 'simple',
                    status TEXT NOT NULL DEFAULT 'draft',
                    visibility TEXT NOT NULL DEFAULT 'private',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    run_uid TEXT NOT NULL UNIQUE,
                    run_dir TEXT NOT NULL,
                    target_project_dir TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'starting',
                    mode TEXT NOT NULL DEFAULT 'simple_prototype',
                    reasoning TEXT NOT NULL DEFAULT 'medium',
                    generated_app_url TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS provider_credentials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    masked_value TEXT NOT NULL,
                    encrypted_value TEXT NOT NULL DEFAULT '',
                    storage_mode TEXT NOT NULL DEFAULT 'masked_only',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, provider)
                );

                CREATE TABLE IF NOT EXISTS artifact_metadata (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    artifact_id TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL,
                    project_id INTEGER,
                    work_item_id TEXT,
                    label TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    owner_agent TEXT NOT NULL DEFAULT '',
                    artifact_type TEXT NOT NULL DEFAULT '',
                    visibility TEXT NOT NULL DEFAULT 'business',
                    storage_uri TEXT NOT NULL DEFAULT '',
                    source_tool TEXT NOT NULL DEFAULT '',
                    source_model TEXT NOT NULL DEFAULT '',
                    external_refs TEXT NOT NULL DEFAULT '[]',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, path)
                );

                CREATE TABLE IF NOT EXISTS artifact_contents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    artifact_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    content_kind TEXT NOT NULL DEFAULT 'text',
                    content_text TEXT NOT NULL DEFAULT '',
                    content_json TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(run_id, artifact_id)
                );

                CREATE TABLE IF NOT EXISTS execution_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    execution_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    request_payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(run_id, execution_id)
                );

                CREATE TABLE IF NOT EXISTS agent_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    message_id TEXT NOT NULL,
                    from_agent TEXT NOT NULL,
                    to_agent TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    artifact_refs TEXT NOT NULL DEFAULT '[]',
                    correlation_id TEXT,
                    parent_message_id TEXT,
                    execution_id TEXT,
                    parent_execution_id TEXT,
                    status TEXT NOT NULL DEFAULT 'sent',
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, message_id)
                );

                CREATE TABLE IF NOT EXISTS delivery_state_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    snapshot_id TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    state_payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, snapshot_id)
                );

                CREATE TABLE IF NOT EXISTS run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    runtime_run_id TEXT NOT NULL DEFAULT '',
                    event_id TEXT NOT NULL,
                    project_id INTEGER,
                    work_item_id TEXT,
                    agent_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    artifact_ids TEXT NOT NULL DEFAULT '[]',
                    external_refs TEXT NOT NULL DEFAULT '[]',
                    data TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, event_id)
                );

                CREATE TABLE IF NOT EXISTS tool_call_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    runtime_run_id TEXT NOT NULL DEFAULT '',
                    event_id TEXT NOT NULL,
                    work_item_id TEXT,
                    agent_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    input_summary TEXT NOT NULL DEFAULT '{}',
                    output_summary TEXT NOT NULL DEFAULT '{}',
                    artifact_ids TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT '',
                    failure_mode TEXT,
                    duration_ms INTEGER,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, event_id)
                );

                CREATE TABLE IF NOT EXISTS model_call_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    runtime_run_id TEXT NOT NULL DEFAULT '',
                    event_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    prompt_ref TEXT NOT NULL DEFAULT '',
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    estimated_cost_usd REAL,
                    status TEXT NOT NULL DEFAULT '',
                    duration_ms INTEGER,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, event_id)
                );

                CREATE TABLE IF NOT EXISTS work_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    work_item_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    sprint_id TEXT NOT NULL DEFAULT '',
                    delivery_order INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'todo',
                    lane TEXT NOT NULL DEFAULT 'todo',
                    owner_agent TEXT NOT NULL DEFAULT '',
                    assigned_agent TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 0,
                    source_refs TEXT NOT NULL DEFAULT '[]',
                    artifact_ids TEXT NOT NULL DEFAULT '[]',
                    blocker TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(run_id, work_item_id)
                );

                CREATE TABLE IF NOT EXISTS sprints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    sprint_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    delivery_order INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'planned',
                    is_final INTEGER NOT NULL DEFAULT 0,
                    source_refs TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(run_id, sprint_id)
                );

                CREATE TABLE IF NOT EXISTS work_item_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    event_id TEXT NOT NULL,
                    work_item_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_status TEXT NOT NULL DEFAULT '',
                    to_status TEXT NOT NULL DEFAULT '',
                    from_owner TEXT NOT NULL DEFAULT '',
                    to_owner TEXT NOT NULL DEFAULT '',
                    agent_id TEXT NOT NULL DEFAULT '',
                    tool_name TEXT NOT NULL DEFAULT '',
                    tool_call_id TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    visibility TEXT NOT NULL DEFAULT 'user',
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, event_id)
                );

                CREATE TABLE IF NOT EXISTS activity_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    event_id TEXT NOT NULL,
                    work_item_id TEXT NOT NULL,
                    owner_agent TEXT NOT NULL DEFAULT '',
                    agent_id TEXT NOT NULL DEFAULT '',
                    tool_name TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    artifact_ids TEXT NOT NULL DEFAULT '[]',
                    visibility TEXT NOT NULL DEFAULT 'user',
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, event_id)
                );

                CREATE TABLE IF NOT EXISTS raw_log_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    work_item_id TEXT,
                    sprint_id TEXT NOT NULL DEFAULT '',
                    agent_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL DEFAULT '',
                    tool_call_id TEXT NOT NULL DEFAULT '',
                    seq INTEGER NOT NULL,
                    level TEXT NOT NULL DEFAULT '',
                    stream TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, agent_id, tool_call_id, seq)
                );

                CREATE TABLE IF NOT EXISTS console_processes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    process_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT '',
                    thread_name TEXT NOT NULL DEFAULT '',
                    env_keys TEXT NOT NULL DEFAULT '[]',
                    stop_requested_at TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(run_id, process_name)
                );

                CREATE INDEX IF NOT EXISTS idx_runs_project_created
                    ON runs(project_id, created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_projects_owner_updated
                    ON projects(owner_user_id, visibility, updated_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_work_items_run_sprint_order
                    ON work_items(run_id, sprint_id, delivery_order, work_item_id);
                CREATE INDEX IF NOT EXISTS idx_work_items_run_status
                    ON work_items(run_id, status, active);
                CREATE INDEX IF NOT EXISTS idx_sprints_run_order
                    ON sprints(run_id, delivery_order, sprint_id);
                CREATE INDEX IF NOT EXISTS idx_work_item_events_run_item_created
                    ON work_item_events(run_id, work_item_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_activity_events_run_item_created
                    ON activity_events(run_id, work_item_id, visibility, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_artifact_metadata_run_item_visibility
                    ON artifact_metadata(run_id, work_item_id, visibility, created_at);
                CREATE INDEX IF NOT EXISTS idx_tool_call_events_run_item_created
                    ON tool_call_events(run_id, work_item_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_run_events_run_item_created
                    ON run_events(run_id, work_item_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_model_call_events_run_agent_created
                    ON model_call_events(run_id, agent_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_raw_log_events_run_item_seq
                    ON raw_log_events(run_id, work_item_id, id);
                CREATE INDEX IF NOT EXISTS idx_raw_log_events_run_agent_seq
                    ON raw_log_events(run_id, agent_id, id);
                CREATE INDEX IF NOT EXISTS idx_agent_messages_run_to_created
                    ON agent_messages(run_id, to_agent, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_agent_messages_run_correlation
                    ON agent_messages(run_id, correlation_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_console_processes_run_name
                    ON console_processes(run_id, process_name);
                """
            )
            self._ensure_artifact_metadata_columns(conn)
            self._ensure_run_columns(conn)
            self._record_schema_metadata(conn)

    def _record_schema_metadata(self, conn: Any) -> None:
        now = utc_now()
        conn.execute(
            """
            INSERT INTO schema_metadata (key, value, updated_at)
            VALUES ('schema_version', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (CONSOLE_SCHEMA_VERSION, now),
        )

    def schema_version(self) -> str:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
            ).fetchone()
        return str(row["value"]) if row else ""

    def _ensure_artifact_metadata_columns(self, conn: Any) -> None:
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(artifact_metadata)").fetchall()
        }
        columns = {
            "artifact_id": "TEXT NOT NULL DEFAULT ''",
            "project_id": "INTEGER",
            "work_item_id": "TEXT",
            "owner_agent": "TEXT NOT NULL DEFAULT ''",
            "artifact_type": "TEXT NOT NULL DEFAULT ''",
            "storage_uri": "TEXT NOT NULL DEFAULT ''",
            "source_tool": "TEXT NOT NULL DEFAULT ''",
            "source_model": "TEXT NOT NULL DEFAULT ''",
            "external_refs": "TEXT NOT NULL DEFAULT '[]'",
            "metadata": "TEXT NOT NULL DEFAULT '{}'",
        }
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE artifact_metadata ADD COLUMN {name} {definition}")

    def _ensure_run_columns(self, conn: Any) -> None:
        existing = {str(row["name"]) for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        columns = {
            "target_project_dir": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {definition}")

    def create_user(self, *, email: str, username: str, password: str) -> User:
        now = utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO users (email, username, password_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    email.strip().lower(),
                    username.strip(),
                    hash_password(password),
                    now,
                ),
            )
            user_id = int(cursor.lastrowid)
        user = self.get_user_by_id(user_id)
        if user is None:  # pragma: no cover - defensive
            raise RuntimeError("Created user could not be loaded")
        return user

    def authenticate_user(self, identifier: str, password: str) -> User | None:
        lookup = identifier.strip().lower()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM users
                WHERE lower(email) = ? OR lower(username) = ?
                """,
                (lookup, lookup),
            ).fetchone()
        if row is None or not verify_password(password, str(row["password_hash"])):
            return None
        return _user(row)

    def get_user_by_id(self, user_id: int) -> User | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user(row) if row else None

    def create_session(self, user_id: int) -> str:
        token = new_session_token()
        now = datetime.now(UTC)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (user_id, token_hash, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    user_id,
                    hash_token(token),
                    now.isoformat(),
                    (now + timedelta(days=SESSION_DAYS)).isoformat(),
                ),
            )
        return token

    def user_for_session(self, token: str | None) -> User | None:
        if not token:
            return None
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT users.*
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?
                """,
                (hash_token(token), now),
            ).fetchone()
        return _user(row) if row else None

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))

    def list_projects_for_user(self, user_id: int) -> list[Project]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*,
                       r.id AS latest_run_id,
                       r.status AS latest_run_status,
                       r.generated_app_url AS generated_app_url
                FROM projects p
                LEFT JOIN runs r ON r.id = (
                    SELECT id FROM runs
                    WHERE project_id = p.id
                    ORDER BY created_at DESC
                    LIMIT 1
                )
                WHERE p.owner_user_id = ? AND p.visibility IN ('private', 'public_demo')
                ORDER BY p.updated_at DESC, p.id DESC
                """,
                (user_id,),
            ).fetchall()
        return [_project(row) for row in rows]

    def list_recent_projects_for_user(self, user_id: int, *, limit: int = 5) -> list[Project]:
        return self.list_projects_for_user(user_id)[:limit]

    def list_public_demo_projects(self, *, limit: int | None = None) -> list[Project]:
        sql = """
            SELECT p.*,
                   r.id AS latest_run_id,
                   r.status AS latest_run_status,
                   r.generated_app_url AS generated_app_url
            FROM projects p
            LEFT JOIN runs r ON r.id = (
                SELECT id FROM runs
                WHERE project_id = p.id
                ORDER BY created_at DESC
                LIMIT 1
            )
            WHERE p.visibility = 'public_demo'
            ORDER BY p.updated_at DESC, p.id DESC
        """
        params: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_project(row) for row in rows]

    def public_demo_project(self) -> Project | None:
        projects = self.list_public_demo_projects(limit=1)
        return projects[0] if projects else None

    def get_project_for_user(self, project_id: int, user_id: int) -> Project | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT p.*,
                       r.id AS latest_run_id,
                       r.status AS latest_run_status,
                       r.generated_app_url AS generated_app_url
                FROM projects p
                LEFT JOIN runs r ON r.id = (
                    SELECT id FROM runs
                    WHERE project_id = p.id
                    ORDER BY created_at DESC
                    LIMIT 1
                )
                WHERE p.id = ?
                  AND (
                    (p.owner_user_id = ? AND p.visibility = 'private')
                    OR p.visibility = 'public_demo'
                  )
                """,
                (project_id, user_id),
            ).fetchone()
        return _project(row) if row else None

    def project_request_text(self, project_id: int, user_id: int) -> str:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT request_text
                FROM projects
                WHERE id = ? AND owner_user_id = ? AND visibility = 'private'
                """,
                (project_id, user_id),
            ).fetchone()
        return str(row["request_text"]) if row else ""

    def create_project(
        self,
        *,
        owner_user_id: int,
        name: str,
        request_text: str,
        mode: str,
        complexity: str,
        status: str = "starting",
    ) -> Project:
        now = utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO projects (
                    owner_user_id, name, request_text, mode, complexity,
                    status, visibility, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'private', ?, ?)
                """,
                (
                    owner_user_id,
                    name.strip(),
                    request_text.strip(),
                    mode,
                    complexity,
                    status,
                    now,
                    now,
                ),
            )
            project_id = int(cursor.lastrowid)
        project = self.get_project_for_user(project_id, owner_user_id)
        if project is None:  # pragma: no cover - defensive
            raise RuntimeError("Created project could not be loaded")
        return project

    def update_project_status(self, project_id: int, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now(), project_id),
            )

    def set_project_visibility(self, project_id: int, user_id: int, visibility: str) -> bool:
        if visibility not in {"private", "public_demo"}:
            raise ValueError(f"Unsupported project visibility: {visibility}")
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE projects
                SET visibility = ?, updated_at = ?
                WHERE id = ? AND owner_user_id = ?
                """,
                (visibility, utc_now(), project_id, user_id),
            )
        return cursor.rowcount > 0

    def delete_private_project(self, project_id: int, user_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM projects
                WHERE id = ? AND owner_user_id = ? AND visibility = 'private'
                """,
                (project_id, user_id),
            )
        return cursor.rowcount > 0

    def create_run(
        self,
        *,
        project_id: int,
        run_uid: str,
        run_dir: Path,
        target_project_dir: Path | str | None = None,
        status: str,
        mode: str,
        reasoning: str,
    ) -> Run:
        now = utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO runs (
                    project_id, run_uid, run_dir, target_project_dir, status, mode, reasoning,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    run_uid,
                    str(run_dir),
                    str(target_project_dir or (run_dir / "generated-project")),
                    status,
                    mode,
                    reasoning,
                    now,
                    now,
                ),
            )
            run_id = int(cursor.lastrowid)
            self._seed_planning_work_items_conn(conn, run_id)
        run = self.get_run(run_id)
        if run is None:  # pragma: no cover - defensive
            raise RuntimeError("Created run could not be loaded")
        return run

    def get_run(self, run_id: int) -> Run | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _run(row) if row else None

    def get_run_for_user(self, run_id: int, user_id: int) -> Run | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT r.*
                FROM runs r
                JOIN projects p ON p.id = r.project_id
                WHERE r.id = ?
                  AND (
                    (p.owner_user_id = ? AND p.visibility = 'private')
                    OR p.visibility = 'public_demo'
                  )
                """,
                (run_id, user_id),
            ).fetchone()
        return _run(row) if row else None

    def latest_run_for_project(self, project_id: int, user_id: int) -> Run | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT r.*
                FROM runs r
                JOIN projects p ON p.id = r.project_id
                WHERE p.id = ?
                  AND (
                    (p.owner_user_id = ? AND p.visibility = 'private')
                    OR p.visibility = 'public_demo'
                  )
                ORDER BY r.created_at DESC
                LIMIT 1
                """,
                (project_id, user_id),
            ).fetchone()
        return _run(row) if row else None

    def runs_for_project(self, project_id: int, user_id: int) -> list[Run]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*
                FROM runs r
                JOIN projects p ON p.id = r.project_id
                WHERE p.id = ?
                  AND (
                    (p.owner_user_id = ? AND p.visibility = 'private')
                    OR p.visibility = 'public_demo'
                  )
                ORDER BY r.created_at DESC
                """,
                (project_id, user_id),
            ).fetchall()
        return [_run(row) for row in rows]

    def update_run_status(
        self,
        run_id: int,
        status: str,
        *,
        generated_app_url: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = ?,
                    generated_app_url = COALESCE(NULLIF(?, ''), generated_app_url),
                    updated_at = ?
                WHERE id = ?
                """,
                (status, generated_app_url, utc_now(), run_id),
            )

    def update_run_target_project_dir(self, run_id: int, target_project_dir: Path | str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET target_project_dir = ?, updated_at = ?
                WHERE id = ?
                """,
                (str(target_project_dir), utc_now(), run_id),
            )

    def upsert_console_process_state(
        self,
        run_id: int,
        *,
        process_name: str,
        status: str = "",
        thread_name: str = "",
        env_keys: list[str] | None = None,
        stop_requested_at: str = "",
        error: str = "",
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT env_keys, stop_requested_at
                FROM console_processes
                WHERE run_id = ? AND process_name = ?
                """,
                (run_id, process_name),
            ).fetchone()
            env_payload = json.dumps(env_keys if env_keys is not None else [], sort_keys=True)
            if existing is not None and env_keys is None:
                env_payload = str(existing["env_keys"] or "[]")
            stop_value = stop_requested_at
            if existing is not None and not stop_value:
                stop_value = str(existing["stop_requested_at"] or "")
            conn.execute(
                """
                INSERT INTO console_processes (
                    run_id, process_name, status, thread_name, env_keys,
                    stop_requested_at, error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, process_name) DO UPDATE SET
                    status = excluded.status,
                    thread_name = excluded.thread_name,
                    env_keys = excluded.env_keys,
                    stop_requested_at = excluded.stop_requested_at,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    process_name,
                    status,
                    thread_name,
                    env_payload,
                    stop_value,
                    error,
                    now,
                    now,
                ),
            )

    def request_console_process_stop(self, run_id: int, *, process_name: str) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO console_processes (
                    run_id, process_name, status, stop_requested_at,
                    created_at, updated_at
                )
                VALUES (?, ?, 'stop_requested', ?, ?, ?)
                ON CONFLICT(run_id, process_name) DO UPDATE SET
                    status = excluded.status,
                    stop_requested_at = excluded.stop_requested_at,
                    updated_at = excluded.updated_at
                """,
                (run_id, process_name, now, now, now),
            )

    def get_console_process_state(
        self,
        run_id: int,
        process_name: str,
    ) -> ConsoleProcessState | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM console_processes
                WHERE run_id = ? AND process_name = ?
                """,
                (run_id, process_name),
            ).fetchone()
        return _console_process_state(row) if row else None

    def get_run_by_uid(self, run_uid: str) -> Run | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_uid = ?", (run_uid,)).fetchone()
        return _run(row) if row else None

    def upsert_artifact_record(self, run_id: int, record: ArtifactRecord) -> None:
        with self.connect() as conn:
            self._upsert_artifact_record_conn(conn, run_id, record)

    def _upsert_artifact_record_conn(
        self,
        conn: Any,
        run_id: int,
        record: ArtifactRecord,
    ) -> None:
        conn.execute(
            """
            INSERT INTO artifact_metadata (
                run_id, artifact_id, path, project_id, work_item_id, label, agent,
                owner_agent, artifact_type, visibility, storage_uri, source_tool,
                source_model, external_refs, metadata, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, path) DO UPDATE SET
                artifact_id = excluded.artifact_id,
                project_id = excluded.project_id,
                work_item_id = excluded.work_item_id,
                label = excluded.label,
                agent = excluded.agent,
                owner_agent = excluded.owner_agent,
                artifact_type = excluded.artifact_type,
                visibility = excluded.visibility,
                storage_uri = excluded.storage_uri,
                source_tool = excluded.source_tool,
                source_model = excluded.source_model,
                external_refs = excluded.external_refs,
                metadata = excluded.metadata,
                created_at = excluded.created_at
            """,
            (
                run_id,
                record.artifact_id,
                record.relative_path,
                record.project_id,
                record.work_item_id,
                record.label,
                record.owner_agent,
                record.owner_agent,
                record.artifact_type,
                record.visibility,
                record.storage_uri,
                record.source_tool,
                record.source_model,
                json.dumps(record.external_refs, sort_keys=True),
                json.dumps(record.metadata, sort_keys=True),
                record.created_at,
            ),
        )

    def upsert_artifact_content(
        self,
        run_id: int,
        *,
        artifact_id: str,
        path: str,
        content_kind: str,
        content_text: str = "",
        content_json: object | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO artifact_contents (
                    run_id, artifact_id, path, content_kind, content_text,
                    content_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, artifact_id) DO UPDATE SET
                    path = excluded.path,
                    content_kind = excluded.content_kind,
                    content_text = excluded.content_text,
                    content_json = excluded.content_json,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    artifact_id,
                    path,
                    content_kind,
                    content_text,
                    json.dumps(content_json, sort_keys=True) if content_json is not None else "",
                    now,
                    now,
                ),
            )

    def get_artifact_content(self, run_id: int, artifact_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM artifact_contents
                WHERE run_id = ? AND artifact_id = ?
                """,
                (run_id, artifact_id),
            ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["content_json"] = _json_column(payload.get("content_json"), default=None)
        return payload

    def upsert_execution_request(
        self,
        run_id: int,
        *,
        execution_id: str,
        agent_id: str,
        request_payload: dict[str, Any],
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO execution_requests (
                    run_id, execution_id, agent_id, request_payload, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, execution_id) DO UPDATE SET
                    agent_id = excluded.agent_id,
                    request_payload = excluded.request_payload,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    execution_id,
                    agent_id,
                    json.dumps(request_payload, sort_keys=True),
                    now,
                    now,
                ),
            )

    def latest_execution_request(self, run_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT request_payload FROM execution_requests
                WHERE run_id = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        payload = _json_column(row["request_payload"], default={})
        return payload if isinstance(payload, dict) else {}

    def upsert_agent_message(self, run_id: int, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_messages (
                    run_id, message_id, from_agent, to_agent, intent, content,
                    artifact_refs, correlation_id, parent_message_id, execution_id,
                    parent_execution_id, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, message_id) DO UPDATE SET
                    from_agent = excluded.from_agent,
                    to_agent = excluded.to_agent,
                    intent = excluded.intent,
                    content = excluded.content,
                    artifact_refs = excluded.artifact_refs,
                    correlation_id = excluded.correlation_id,
                    parent_message_id = excluded.parent_message_id,
                    execution_id = excluded.execution_id,
                    parent_execution_id = excluded.parent_execution_id,
                    status = excluded.status,
                    created_at = excluded.created_at
                """,
                (
                    run_id,
                    str(payload["message_id"]),
                    str(payload["from_agent"]),
                    str(payload["to_agent"]),
                    str(payload["intent"]),
                    str(payload.get("content") or ""),
                    json.dumps(payload.get("artifact_refs") or [], sort_keys=True),
                    payload.get("correlation_id"),
                    payload.get("parent_message_id"),
                    payload.get("execution_id"),
                    payload.get("parent_execution_id"),
                    str(payload.get("status") or "sent"),
                    str(payload.get("created_at") or utc_now()),
                ),
            )

    def list_agent_messages(
        self,
        run_id: int,
        *,
        to_agent: str | None = None,
        from_agent: str | None = None,
        intent: str | None = None,
        correlation_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        if to_agent is not None:
            clauses.append("to_agent = ?")
            params.append(to_agent)
        if from_agent is not None:
            clauses.append("from_agent = ?")
            params.append(from_agent)
        if intent is not None:
            clauses.append("intent = ?")
            params.append(intent)
        if correlation_id is not None:
            clauses.append("correlation_id = ?")
            params.append(correlation_id)
        sql = (
            "SELECT * FROM agent_messages WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at, id"
        )
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        payloads = [_agent_message_payload(row) for row in rows]
        if limit is not None:
            return payloads[-limit:]
        return payloads

    def upsert_delivery_state_snapshot(
        self,
        run_id: int,
        *,
        snapshot_id: str,
        stage: str,
        status: str,
        state_payload: dict[str, Any],
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO delivery_state_snapshots (
                    run_id, snapshot_id, stage, status, state_payload, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, snapshot_id) DO UPDATE SET
                    stage = excluded.stage,
                    status = excluded.status,
                    state_payload = excluded.state_payload,
                    created_at = excluded.created_at
                """,
                (
                    run_id,
                    snapshot_id,
                    stage,
                    status,
                    json.dumps(state_payload, sort_keys=True),
                    utc_now(),
                ),
            )

    def latest_delivery_state_snapshot(self, run_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT state_payload
                FROM delivery_state_snapshots
                WHERE run_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        payload = _json_column(row["state_payload"], default={})
        return payload if isinstance(payload, dict) else {}

    def list_artifact_records(
        self,
        run_id: int,
        *,
        visibility: str | set[str] | None = None,
    ) -> list[ArtifactRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifact_metadata WHERE run_id = ? ORDER BY created_at, path",
                (run_id,),
            ).fetchall()
        records = [_artifact_record(row) for row in rows]
        records = [record for record in records if record is not None]
        if visibility:
            visibilities = {visibility} if isinstance(visibility, str) else visibility
            records = [record for record in records if record.visibility in visibilities]
        return records

    def get_artifact_record(self, run_id: int, artifact_id: str) -> ArtifactRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM artifact_metadata
                WHERE run_id = ? AND artifact_id = ?
                """,
                (run_id, artifact_id),
            ).fetchone()
        return _artifact_record(row) if row else None

    def list_work_items(self, run_id: int) -> list[WorkItem]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM work_items
                WHERE run_id = ?
                ORDER BY
                    CASE WHEN lower(sprint_id) = 'planning' THEN 0 ELSE 1 END,
                    sprint_id,
                    delivery_order,
                    work_item_id
                """,
                (run_id,),
            ).fetchall()
        return [_work_item(row) for row in rows]

    def upsert_sprint(
        self,
        run_id: int,
        *,
        sprint_id: str,
        title: str,
        delivery_order: int,
        status: str,
        is_final: bool,
        source_refs: list[str] | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sprints (
                    run_id, sprint_id, title, delivery_order, status, is_final,
                    source_refs, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, sprint_id) DO UPDATE SET
                    title = excluded.title,
                    delivery_order = excluded.delivery_order,
                    status = CASE
                        WHEN sprints.status IN ('running', 'done', 'blocked') THEN sprints.status
                        ELSE excluded.status
                    END,
                    is_final = excluded.is_final,
                    source_refs = excluded.source_refs,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    sprint_id,
                    title,
                    delivery_order,
                    status,
                    1 if is_final else 0,
                    json.dumps(source_refs or [], sort_keys=True),
                    now,
                    now,
                ),
            )

    def sprint_is_final(self, run_id: int, sprint_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT is_final FROM sprints
                WHERE run_id = ? AND sprint_id = ?
                """,
                (run_id, sprint_id),
            ).fetchone()
        return bool(row and row["is_final"])

    def list_activity_events(
        self,
        run_id: int,
        *,
        work_item_id: str = "",
        visibility: str = "user",
    ) -> list[ActivityEvent]:
        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        if work_item_id:
            clauses.append("work_item_id = ?")
            params.append(_canonical_work_item_id(work_item_id))
        if visibility:
            clauses.append("visibility = ?")
            params.append(visibility)
        sql = (
            "SELECT * FROM activity_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at, id"
        )
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_activity_event(row) for row in rows]

    def append_raw_log_event(
        self,
        run_id: int,
        *,
        agent_id: str,
        seq: int,
        message: str,
        work_item_id: str = "",
        sprint_id: str = "",
        tool_name: str = "",
        tool_call_id: str = "",
        level: str = "",
        stream: str = "",
        created_at: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO raw_log_events (
                    run_id, work_item_id, sprint_id, agent_id, tool_name,
                    tool_call_id, seq, level, stream, message, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, agent_id, tool_call_id, seq) DO UPDATE SET
                    work_item_id = excluded.work_item_id,
                    sprint_id = excluded.sprint_id,
                    tool_name = excluded.tool_name,
                    level = excluded.level,
                    stream = excluded.stream,
                    message = excluded.message,
                    created_at = excluded.created_at
                """,
                (
                    run_id,
                    _canonical_work_item_id(work_item_id) or None,
                    sprint_id,
                    agent_id,
                    tool_name,
                    tool_call_id,
                    seq,
                    level,
                    stream,
                    message,
                    created_at or utc_now(),
                ),
            )

    def list_raw_log_events(
        self,
        run_id: int,
        *,
        work_item_id: str = "",
        agent_id: str = "",
        limit: int | None = None,
    ) -> list[RawLogEvent]:
        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        if work_item_id:
            clauses.append("work_item_id = ?")
            params.append(_canonical_work_item_id(work_item_id))
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        sql = (
            "SELECT * FROM raw_log_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY id"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_raw_log_event(row) for row in rows]

    def _seed_planning_work_items_conn(self, conn: Any, run_id: int) -> None:
        for item in HEAD_PLANNING_ITEMS:
            self._upsert_work_item_conn(
                conn,
                run_id=run_id,
                work_item_id=str(item["id"]),
                title=str(item["title"]),
                sprint_id=str(item["sprint_id"]),
                delivery_order=int(item["delivery_order"]),
                status="todo",
                owner_agent=str(item["suggested_owner_agent"]),
                source_refs=[],
                created_at=utc_now(),
            )

    def _upsert_work_item_conn(
        self,
        conn: Any,
        *,
        run_id: int,
        work_item_id: str,
        title: str,
        sprint_id: str,
        delivery_order: int,
        status: str,
        owner_agent: str,
        source_refs: list[str],
        created_at: str,
    ) -> None:
        canonical_id = _canonical_work_item_id(work_item_id)
        if not canonical_id:
            return
        normalized_status = _normalize_work_item_status(status)
        conn.execute(
            """
            INSERT INTO work_items (
                run_id, work_item_id, title, sprint_id, delivery_order, status,
                lane, owner_agent, source_refs, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, work_item_id) DO UPDATE SET
                title = COALESCE(NULLIF(excluded.title, ''), work_items.title),
                sprint_id = COALESCE(NULLIF(excluded.sprint_id, ''), work_items.sprint_id),
                delivery_order = CASE
                    WHEN excluded.delivery_order > 0 THEN excluded.delivery_order
                    ELSE work_items.delivery_order
                END,
                owner_agent = COALESCE(NULLIF(excluded.owner_agent, ''), work_items.owner_agent),
                source_refs = excluded.source_refs,
                updated_at = excluded.updated_at
            """,
            (
                run_id,
                canonical_id,
                title,
                sprint_id,
                delivery_order,
                normalized_status,
                _lane_for_work_item_status(normalized_status),
                owner_agent,
                json.dumps(source_refs, sort_keys=True),
                created_at,
                created_at,
            ),
        )

    def _record_work_item_transition_conn(
        self,
        conn: Any,
        *,
        run_id: int,
        event_id: str,
        work_item_id: str,
        event_type: str,
        status: str,
        owner_agent: str,
        assigned_agent: str,
        agent_id: str,
        tool_name: str,
        tool_call_id: str,
        message: str,
        artifact_ids: list[str],
        created_at: str,
    ) -> None:
        item_id = _canonical_work_item_id(work_item_id)
        if not item_id:
            return
        row = conn.execute(
            """
            SELECT status, owner_agent, title, sprint_id, delivery_order
            FROM work_items
            WHERE run_id = ? AND work_item_id = ?
            """,
            (run_id, item_id),
        ).fetchone()
        from_status = str(row["status"]) if row else ""
        from_owner = str(row["owner_agent"]) if row else ""
        to_status = _normalize_work_item_status(status)
        to_owner = owner_agent or from_owner
        if row is None:
            self._upsert_work_item_conn(
                conn,
                run_id=run_id,
                work_item_id=item_id,
                title=item_id,
                sprint_id="planning" if item_id.startswith("PLAN-") else "",
                delivery_order=0,
                status=to_status,
                owner_agent=to_owner,
                source_refs=[],
                created_at=created_at or utc_now(),
            )
        blocker = message if to_status == "blocked" else ""
        conn.execute(
            """
            UPDATE work_items
            SET status = ?,
                lane = ?,
                owner_agent = COALESCE(NULLIF(?, ''), owner_agent),
                assigned_agent = COALESCE(NULLIF(?, ''), assigned_agent),
                active = ?,
                blocker = ?,
                artifact_ids = CASE
                    WHEN ? != '[]' THEN ?
                    ELSE artifact_ids
                END,
                updated_at = ?
            WHERE run_id = ? AND work_item_id = ?
            """,
            (
                to_status,
                _lane_for_work_item_status(to_status),
                to_owner,
                assigned_agent,
                1 if to_status in {"in_progress", "review"} else 0,
                blocker,
                json.dumps(sorted(set(artifact_ids))),
                json.dumps(sorted(set(artifact_ids))),
                created_at or utc_now(),
                run_id,
                item_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO work_item_events (
                run_id, event_id, work_item_id, event_type, from_status, to_status,
                from_owner, to_owner, agent_id, tool_name, tool_call_id, message,
                visibility, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'user', ?)
            ON CONFLICT(run_id, event_id) DO NOTHING
            """,
            (
                run_id,
                event_id,
                item_id,
                event_type,
                from_status,
                to_status,
                from_owner,
                to_owner,
                agent_id,
                tool_name,
                tool_call_id,
                message,
                created_at or utc_now(),
            ),
        )
        if message:
            conn.execute(
                """
                INSERT INTO activity_events (
                    run_id, event_id, work_item_id, owner_agent, agent_id, tool_name,
                    message, status, artifact_ids, visibility, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'user', ?)
                ON CONFLICT(run_id, event_id) DO NOTHING
                """,
                (
                    run_id,
                    event_id,
                    item_id,
                    to_owner,
                    agent_id,
                    tool_name,
                    message,
                    to_status,
                    json.dumps(sorted(set(artifact_ids))),
                    created_at or utc_now(),
                ),
            )

    def upsert_run_event(self, run_id: int, event: RunEvent) -> None:
        with self.connect() as conn:
            self._upsert_run_event_conn(conn, run_id, event)

    def _upsert_run_event_conn(
        self,
        conn: Any,
        run_id: int,
        event: RunEvent,
    ) -> None:
        conn.execute(
            """
            INSERT INTO run_events (
                run_id, runtime_run_id, event_id, project_id, work_item_id, agent_id,
                event_type, status, message, artifact_ids, external_refs, data, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, event_id) DO UPDATE SET
                runtime_run_id = excluded.runtime_run_id,
                project_id = excluded.project_id,
                work_item_id = excluded.work_item_id,
                agent_id = excluded.agent_id,
                event_type = excluded.event_type,
                status = excluded.status,
                message = excluded.message,
                artifact_ids = excluded.artifact_ids,
                external_refs = excluded.external_refs,
                data = excluded.data,
                created_at = excluded.created_at
            """,
            (
                run_id,
                str(event.run_id),
                event.event_id,
                event.project_id,
                event.work_item_id,
                event.agent_id,
                event.event_type,
                event.status,
                event.message,
                json.dumps(sanitize_trace_data(event.artifact_ids), sort_keys=True),
                json.dumps(sanitize_trace_data(event.external_refs), sort_keys=True),
                json.dumps(sanitize_trace_data(event.data), sort_keys=True),
                event.created_at,
            ),
        )

    def list_run_events(
        self,
        run_id: int,
        *,
        event_type: str | None = None,
        agent_id: str | None = None,
    ) -> list[RunEvent]:
        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        sql = "SELECT * FROM run_events WHERE " + " AND ".join(clauses) + " ORDER BY created_at, id"
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_run_event(row) for row in rows]

    def upsert_tool_call_event(self, run_id: int, event: ToolCallEvent) -> None:
        with self.connect() as conn:
            self._upsert_tool_call_event_conn(conn, run_id, event)

    def _upsert_tool_call_event_conn(
        self,
        conn: Any,
        run_id: int,
        event: ToolCallEvent,
    ) -> None:
        conn.execute(
            """
            INSERT INTO tool_call_events (
                run_id, runtime_run_id, event_id, work_item_id, agent_id, tool_name,
                tool_call_id, input_summary, output_summary, artifact_ids, status,
                failure_mode, duration_ms, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, event_id) DO UPDATE SET
                runtime_run_id = excluded.runtime_run_id,
                work_item_id = excluded.work_item_id,
                agent_id = excluded.agent_id,
                tool_name = excluded.tool_name,
                tool_call_id = excluded.tool_call_id,
                input_summary = excluded.input_summary,
                output_summary = excluded.output_summary,
                artifact_ids = excluded.artifact_ids,
                status = excluded.status,
                failure_mode = excluded.failure_mode,
                duration_ms = excluded.duration_ms,
                created_at = excluded.created_at
            """,
            (
                run_id,
                str(event.run_id),
                event.event_id,
                event.work_item_id,
                event.agent_id,
                event.tool_name,
                event.tool_call_id,
                json.dumps(sanitize_trace_data(event.input_summary), sort_keys=True),
                json.dumps(sanitize_trace_data(event.output_summary), sort_keys=True),
                json.dumps(sanitize_trace_data(event.artifact_ids), sort_keys=True),
                event.status,
                event.failure_mode,
                event.duration_ms,
                event.created_at,
            ),
        )

    def list_tool_call_events(
        self,
        run_id: int,
        *,
        tool_name: str | None = None,
        agent_id: str | None = None,
    ) -> list[ToolCallEvent]:
        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        if tool_name:
            clauses.append("tool_name = ?")
            params.append(tool_name)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        sql = (
            "SELECT * FROM tool_call_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at, id"
        )
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_tool_call_event(row) for row in rows]

    def upsert_model_call_event(self, run_id: int, event: ModelCallEvent) -> None:
        with self.connect() as conn:
            self._upsert_model_call_event_conn(conn, run_id, event)

    def _upsert_model_call_event_conn(
        self,
        conn: Any,
        run_id: int,
        event: ModelCallEvent,
    ) -> None:
        conn.execute(
            """
            INSERT INTO model_call_events (
                run_id, runtime_run_id, event_id, agent_id, provider, model, purpose,
                prompt_ref, input_tokens, output_tokens, estimated_cost_usd, status,
                duration_ms, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, event_id) DO UPDATE SET
                runtime_run_id = excluded.runtime_run_id,
                agent_id = excluded.agent_id,
                provider = excluded.provider,
                model = excluded.model,
                purpose = excluded.purpose,
                prompt_ref = excluded.prompt_ref,
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                estimated_cost_usd = excluded.estimated_cost_usd,
                status = excluded.status,
                duration_ms = excluded.duration_ms,
                created_at = excluded.created_at
            """,
            (
                run_id,
                str(event.run_id),
                event.event_id,
                event.agent_id,
                event.provider,
                event.model,
                event.purpose,
                event.prompt_ref,
                event.input_tokens,
                event.output_tokens,
                event.estimated_cost_usd,
                event.status,
                event.duration_ms,
                event.created_at,
            ),
        )

    def list_model_call_events(
        self,
        run_id: int,
        *,
        agent_id: str | None = None,
    ) -> list[ModelCallEvent]:
        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        sql = (
            "SELECT * FROM model_call_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at, id"
        )
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_model_call_event(row) for row in rows]

    def save_provider_secret(self, user_id: int, provider: str, secret: str) -> ProviderCredential:
        encrypted = encrypt_secret(secret)
        storage_mode = "encrypted" if encrypted else "local_demo"
        stored_value = encrypted or secret
        masked = mask_secret(secret)
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_credentials (
                    user_id, provider, masked_value, encrypted_value,
                    storage_mode, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, provider)
                DO UPDATE SET
                    masked_value = excluded.masked_value,
                    encrypted_value = excluded.encrypted_value,
                    storage_mode = excluded.storage_mode,
                    updated_at = excluded.updated_at
                """,
                (user_id, provider, masked, stored_value, storage_mode, now, now),
            )
        credential = self.get_provider_secret(user_id, provider)
        if credential is None:  # pragma: no cover - defensive
            raise RuntimeError("Saved provider credential could not be loaded")
        return credential

    def get_provider_secret(self, user_id: int, provider: str) -> ProviderCredential | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT provider, masked_value, encrypted_value, storage_mode, updated_at
                FROM provider_credentials
                WHERE user_id = ? AND provider = ?
                """,
                (user_id, provider),
            ).fetchone()
        return _provider(row) if row else None

    def delete_provider_secret(self, user_id: int, provider: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM provider_credentials WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            )

    def seed_public_demo_from_env(self) -> None:
        run_dir = os.getenv("PUBLIC_DEMO_RUN_DIR", "").strip()
        if not run_dir:
            return
        run_path = Path(run_dir)
        if not run_path.exists():
            return
        name = os.getenv("PUBLIC_DEMO_PROJECT_NAME", "").strip() or "Agentic Company Demo"
        request_text = _read_seed_request_text(run_path)
        now = utc_now()
        with self.connect() as conn:
            project_row = conn.execute(
                """
                SELECT p.id
                FROM projects p
                JOIN runs r ON r.project_id = p.id
                WHERE p.visibility = 'public_demo' AND r.run_dir = ?
                LIMIT 1
                """,
                (str(run_path),),
            ).fetchone()
            if project_row:
                project_id = int(project_row["id"])
                conn.execute(
                    """
                    UPDATE projects
                    SET name = ?, request_text = ?, status = 'demo_ready', updated_at = ?
                    WHERE id = ?
                    """,
                    (name, request_text, now, project_id),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO projects (
                        owner_user_id, name, request_text, mode, complexity,
                        status, visibility, created_at, updated_at
                    )
                    VALUES (NULL, ?, '', 'public_demo', 'medium',
                            'demo_ready', 'public_demo', ?, ?)
                    """,
                    (name, now, now),
                )
                project_id = int(cursor.lastrowid)
                conn.execute(
                    "UPDATE projects SET request_text = ? WHERE id = ?",
                    (request_text, project_id),
                )
            existing = conn.execute(
                "SELECT id FROM runs WHERE project_id = ? AND run_dir = ?",
                (project_id, str(run_path)),
            ).fetchone()
            if not existing:
                conn.execute(
                    """
                    INSERT INTO runs (
                        project_id, run_uid, run_dir, status, mode, reasoning,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, 'demo_ready', 'public_demo', 'medium', ?, ?)
                    """,
                    (project_id, run_path.name, str(run_path), now, now),
                )


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read_seed_request_text(run_path: Path) -> str:
    try:
        return (run_path / "00-requirements.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _user(row: Any) -> User:
    return User(
        id=int(row["id"]),
        email=str(row["email"]),
        username=str(row["username"]),
        created_at=str(row["created_at"]),
    )


def _project(row: Any) -> Project:
    return Project(
        id=int(row["id"]),
        owner_user_id=int(row["owner_user_id"]) if row["owner_user_id"] is not None else None,
        name=str(row["name"]),
        mode=str(row["mode"]),
        complexity=str(row["complexity"]),
        status=str(row["status"]),
        visibility=str(row["visibility"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        request_text=str(row["request_text"] or "") if "request_text" in row.keys() else "",
        latest_run_id=int(row["latest_run_id"]) if row["latest_run_id"] is not None else None,
        latest_run_status=str(row["latest_run_status"] or ""),
        generated_app_url=str(row["generated_app_url"] or ""),
    )


def _run(row: Any) -> Run:
    return Run(
        id=int(row["id"]),
        project_id=int(row["project_id"]),
        run_uid=str(row["run_uid"]),
        run_dir=str(row["run_dir"]),
        target_project_dir=str(row["target_project_dir"] or ""),
        status=str(row["status"]),
        mode=str(row["mode"]),
        reasoning=str(row["reasoning"]),
        generated_app_url=str(row["generated_app_url"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _provider(row: Any) -> ProviderCredential:
    return ProviderCredential(
        provider=str(row["provider"]),
        masked_value=str(row["masked_value"]),
        encrypted_value=str(row["encrypted_value"]),
        storage_mode=str(row["storage_mode"]),
        updated_at=str(row["updated_at"]),
    )


def _work_item(row: Any) -> WorkItem:
    return WorkItem(
        id=int(row["id"]),
        run_id=int(row["run_id"]),
        work_item_id=str(row["work_item_id"]),
        title=str(row["title"]),
        sprint_id=str(row["sprint_id"]),
        delivery_order=int(row["delivery_order"]),
        status=str(row["status"]),
        lane=str(row["lane"]),
        owner_agent=str(row["owner_agent"]),
        assigned_agent=str(row["assigned_agent"]),
        active=bool(row["active"]),
        source_refs=_json_column(row["source_refs"], default=[]),
        artifact_ids=_json_column(row["artifact_ids"], default=[]),
        blocker=str(row["blocker"] or ""),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _activity_event(row: Any) -> ActivityEvent:
    return ActivityEvent(
        id=int(row["id"]),
        run_id=int(row["run_id"]),
        work_item_id=str(row["work_item_id"]),
        owner_agent=str(row["owner_agent"]),
        agent_id=str(row["agent_id"]),
        tool_name=str(row["tool_name"]),
        message=str(row["message"]),
        status=str(row["status"]),
        artifact_ids=_json_column(row["artifact_ids"], default=[]),
        visibility=str(row["visibility"]),
        created_at=str(row["created_at"]),
    )


def _raw_log_event(row: Any) -> RawLogEvent:
    return RawLogEvent(
        id=int(row["id"]),
        run_id=int(row["run_id"]),
        work_item_id=str(row["work_item_id"] or ""),
        sprint_id=str(row["sprint_id"] or ""),
        agent_id=str(row["agent_id"]),
        tool_name=str(row["tool_name"] or ""),
        tool_call_id=str(row["tool_call_id"] or ""),
        seq=int(row["seq"]),
        level=str(row["level"] or ""),
        stream=str(row["stream"] or ""),
        message=str(row["message"]),
        created_at=str(row["created_at"]),
    )


def _console_process_state(row: Any) -> ConsoleProcessState:
    return ConsoleProcessState(
        id=int(row["id"]),
        run_id=int(row["run_id"]),
        process_name=str(row["process_name"]),
        status=str(row["status"] or ""),
        thread_name=str(row["thread_name"] or ""),
        env_keys=_json_column(row["env_keys"], default=[]),
        stop_requested_at=str(row["stop_requested_at"] or ""),
        error=str(row["error"] or ""),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _artifact_record(row: Any) -> ArtifactRecord | None:
    payload = dict(row)
    payload["relative_path"] = payload.get("path", "")
    payload["owner_agent"] = payload.get("owner_agent") or payload.get("agent") or ""
    payload["external_refs"] = _json_column(payload.get("external_refs"), default=[])
    payload["metadata"] = _json_column(payload.get("metadata"), default={})
    return artifact_record_from_mapping(payload)


def _agent_message_payload(row: Any) -> dict[str, Any]:
    return {
        "from_agent": str(row["from_agent"]),
        "to_agent": str(row["to_agent"]),
        "intent": str(row["intent"]),
        "content": str(row["content"] or ""),
        "artifact_refs": _json_column(row["artifact_refs"], default=[]),
        "message_id": str(row["message_id"]),
        "correlation_id": row["correlation_id"],
        "parent_message_id": row["parent_message_id"],
        "execution_id": row["execution_id"],
        "parent_execution_id": row["parent_execution_id"],
        "status": str(row["status"] or "sent"),
        "created_at": str(row["created_at"]),
    }


def _run_event(row: Any) -> RunEvent:
    payload = dict(row)
    payload["run_id"] = payload.get("runtime_run_id") or payload.get("run_id")
    payload["artifact_ids"] = _json_column(payload.get("artifact_ids"), default=[])
    payload["external_refs"] = _json_column(payload.get("external_refs"), default=[])
    payload["data"] = _json_column(payload.get("data"), default={})
    return run_event_from_mapping(payload)


def _tool_call_event(row: Any) -> ToolCallEvent:
    payload = dict(row)
    payload["run_id"] = payload.get("runtime_run_id") or payload.get("run_id")
    payload["input_summary"] = _json_column(payload.get("input_summary"), default={})
    payload["output_summary"] = _json_column(payload.get("output_summary"), default={})
    payload["artifact_ids"] = _json_column(payload.get("artifact_ids"), default=[])
    return tool_call_event_from_mapping(payload)


def _model_call_event(row: Any) -> ModelCallEvent:
    payload = dict(row)
    payload["run_id"] = payload.get("runtime_run_id") or payload.get("run_id")
    return model_call_event_from_mapping(payload)


def _json_column(value: Any, *, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return default
    return parsed


def row_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "__dataclass_fields__"):
        return {field: getattr(row, field) for field in row.__dataclass_fields__}
    return dict(row)


def _canonical_work_item_id(value: str) -> str:
    return str(value or "").strip()


def _normalize_work_item_status(status: str) -> str:
    token = str(status or "").strip().lower()
    if token in {"", "pending", "planned", "backlog"}:
        return "todo"
    if "needs_repair" in token or "failed" in token or "blocked" in token:
        return "blocked"
    if "qa" in token or "review" in token or "implemented" in token or "inspect" in token:
        if "passed" in token or "completed" in token or "ready" in token:
            return "done"
        return "review"
    if any(value in token for value in ("completed", "passed", "ready", "done", "deployed")):
        return "done"
    if any(value in token for value in ("started", "running", "active", "progress")):
        return "in_progress"
    return token if token in {"todo", "in_progress", "review", "done", "blocked"} else "in_progress"


def _lane_for_work_item_status(status: str) -> str:
    normalized = _normalize_work_item_status(status)
    if normalized == "review":
        return "qa"
    if normalized in {"todo", "in_progress", "done", "blocked"}:
        return normalized
    return "todo"
