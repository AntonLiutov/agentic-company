"""SQLite repository for the FastAPI product console."""

from __future__ import annotations

import json
import os
import sqlite3
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
from agentic_company.platform.artifact_registry import ArtifactRecord, artifact_record_from_mapping
from agentic_company.platform.run_trace import (
    ModelCallEvent,
    RunEvent,
    ToolCallEvent,
    load_model_call_events,
    load_run_events,
    load_tool_call_events,
    model_call_event_from_mapping,
    run_event_from_mapping,
    tool_call_event_from_mapping,
)
from agentic_company.platform.sprints import HEAD_PLANNING_ITEMS

SESSION_DAYS = 14


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


def default_db_path() -> Path:
    configured = os.getenv("AGENTIC_CONSOLE_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return repo_root() / "data" / "console.db"


class ConsoleRepository:
    """Small SQLite repository with explicit user isolation methods."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

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

                CREATE TABLE IF NOT EXISTS runtime_sync_state (
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    sync_kind TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, sync_kind)
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
                """
            )
            self._ensure_artifact_metadata_columns(conn)

    def _ensure_artifact_metadata_columns(self, conn: sqlite3.Connection) -> None:
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
        status: str,
        mode: str,
        reasoning: str,
    ) -> Run:
        now = utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO runs (
                    project_id, run_uid, run_dir, status, mode, reasoning,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    run_uid,
                    str(run_dir),
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

    def upsert_artifact_record(self, run_id: int, record: ArtifactRecord) -> None:
        with self.connect() as conn:
            self._upsert_artifact_record_conn(conn, run_id, record)

    def _upsert_artifact_record_conn(
        self,
        conn: sqlite3.Connection,
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

    def sync_artifact_registry_from_run_dir(self, run_id: int, run_dir: Path) -> None:
        from agentic_company.platform.artifact_registry import load_artifact_registry

        registry_path = run_dir / "delivery" / "artifact-registry.json"
        signature = _file_signature([registry_path])
        with self.connect() as conn:
            if self._sync_signature_matches(conn, run_id, "artifact_registry", signature):
                return
            for record in load_artifact_registry(run_dir):
                self._upsert_artifact_record_conn(conn, run_id, record)
            self._save_sync_signature(conn, run_id, "artifact_registry", signature)

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
            + " ORDER BY id"
        )
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_activity_event(row) for row in rows]

    def sync_work_items_from_run_dir(self, run_id: int, run_dir: Path) -> None:
        signature = _file_signature(
            [
                run_dir / "delivery" / "run-events.jsonl",
                run_dir / "delivery" / "tool-call-events.jsonl",
                run_dir
                / "upstream-planning"
                / "project-management"
                / "candidate-feature-queue.json",
                run_dir / "upstream-planning" / "project-management" / "release-plan.json",
                run_dir / "delivery" / "artifact-registry.json",
            ]
        )
        with self.connect() as conn:
            if self._sync_signature_matches(conn, run_id, "work_items", signature):
                return
            self._seed_planning_work_items_conn(conn, run_id)
            self._materialize_pm_work_items_conn(conn, run_id, run_dir)
            self._link_work_item_artifacts_conn(conn, run_id)
            for event in self._run_events_for_work_item_sync_conn(conn, run_id):
                self._apply_run_event_to_work_items_conn(conn, run_id, event)
            for event in self._tool_events_for_work_item_sync_conn(conn, run_id):
                self._apply_tool_event_to_work_items_conn(conn, run_id, event)
            self._save_sync_signature(conn, run_id, "work_items", signature)

    def _seed_planning_work_items_conn(self, conn: sqlite3.Connection, run_id: int) -> None:
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

    def _materialize_pm_work_items_conn(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        run_dir: Path,
    ) -> None:
        queue_path = (
            run_dir / "upstream-planning" / "project-management" / "candidate-feature-queue.json"
        )
        try:
            payload = json.loads(queue_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, list):
            return
        for index, item in enumerate(payload, start=1):
            if not isinstance(item, dict):
                continue
            work_item_id = _canonical_work_item_id(
                str(item.get("id") or item.get("feature_id") or "")
            )
            if not work_item_id:
                continue
            self._upsert_work_item_conn(
                conn,
                run_id=run_id,
                work_item_id=work_item_id,
                title=str(item.get("title") or item.get("name") or work_item_id),
                sprint_id=str(item.get("sprint_id") or ""),
                delivery_order=_int_value(item.get("delivery_order"), index),
                status=str(item.get("status") or "todo"),
                owner_agent=str(
                    item.get("suggested_owner_agent")
                    or item.get("owner_agent")
                    or "fullstack-agent"
                ),
                source_refs=_string_list(item.get("source_refs", [])),
                created_at=utc_now(),
            )

    def _upsert_work_item_conn(
        self,
        conn: sqlite3.Connection,
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

    def _link_work_item_artifacts_conn(self, conn: sqlite3.Connection, run_id: int) -> None:
        rows = conn.execute(
            """
            SELECT work_item_id, artifact_id
            FROM artifact_metadata
            WHERE run_id = ? AND COALESCE(work_item_id, '') != ''
            ORDER BY created_at, id
            """,
            (run_id,),
        ).fetchall()
        artifact_ids_by_item: dict[str, list[str]] = {}
        for row in rows:
            item_id = _canonical_work_item_id(str(row["work_item_id"] or ""))
            artifact_id = str(row["artifact_id"] or "")
            if item_id and artifact_id:
                artifact_ids_by_item.setdefault(item_id, []).append(artifact_id)
        for item_id, artifact_ids in artifact_ids_by_item.items():
            conn.execute(
                """
                UPDATE work_items
                SET artifact_ids = ?, updated_at = ?
                WHERE run_id = ? AND work_item_id = ?
                """,
                (json.dumps(sorted(set(artifact_ids))), utc_now(), run_id, item_id),
            )

    def _run_events_for_work_item_sync_conn(
        self,
        conn: sqlite3.Connection,
        run_id: int,
    ) -> list[RunEvent]:
        rows = conn.execute(
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY created_at, id",
            (run_id,),
        ).fetchall()
        return [_run_event(row) for row in rows]

    def _tool_events_for_work_item_sync_conn(
        self,
        conn: sqlite3.Connection,
        run_id: int,
    ) -> list[ToolCallEvent]:
        rows = conn.execute(
            "SELECT * FROM tool_call_events WHERE run_id = ? ORDER BY created_at, id",
            (run_id,),
        ).fetchall()
        return [_tool_call_event(row) for row in rows]

    def _apply_run_event_to_work_items_conn(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        event: RunEvent,
    ) -> None:
        if event.event_type.startswith("codex_command"):
            return
        item_id = _work_item_id_for_run_event(event)
        if not item_id:
            return
        status = _status_from_run_event(event)
        owner_agent = _owner_agent_for_run_event(item_id, event)
        message = _message_from_run_event(event)
        self._record_work_item_transition_conn(
            conn,
            run_id=run_id,
            event_id=f"run:{event.event_id}",
            work_item_id=item_id,
            event_type=event.event_type,
            status=status,
            owner_agent=owner_agent,
            assigned_agent=event.agent_id,
            agent_id=event.agent_id,
            tool_name="runtime_progress",
            tool_call_id="",
            message=message,
            artifact_ids=event.artifact_ids,
            created_at=event.created_at,
        )

    def _apply_tool_event_to_work_items_conn(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        event: ToolCallEvent,
    ) -> None:
        item_id = _work_item_id_for_tool_event(event)
        if not item_id:
            return
        status = _status_from_tool_event(event)
        owner_agent = _owner_agent_for_tool_event(item_id, event)
        message = _message_from_tool_event(event)
        self._record_work_item_transition_conn(
            conn,
            run_id=run_id,
            event_id=f"tool:{event.event_id}",
            work_item_id=item_id,
            event_type=event.tool_name,
            status=status,
            owner_agent=owner_agent,
            assigned_agent=event.agent_id,
            agent_id=event.agent_id,
            tool_name=event.tool_name,
            tool_call_id=event.tool_call_id,
            message=message,
            artifact_ids=event.artifact_ids,
            created_at=event.created_at,
        )

    def _record_work_item_transition_conn(
        self,
        conn: sqlite3.Connection,
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
        conn: sqlite3.Connection,
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
                json.dumps(event.artifact_ids, sort_keys=True),
                json.dumps(event.external_refs, sort_keys=True),
                json.dumps(event.data, sort_keys=True),
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
        conn: sqlite3.Connection,
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
                json.dumps(event.input_summary, sort_keys=True),
                json.dumps(event.output_summary, sort_keys=True),
                json.dumps(event.artifact_ids, sort_keys=True),
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
        conn: sqlite3.Connection,
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

    def sync_run_trace_from_run_dir(self, run_id: int, run_dir: Path) -> None:
        signature = _file_signature(
            [
                run_dir / "delivery" / "run-events.jsonl",
                run_dir / "delivery" / "tool-call-events.jsonl",
                run_dir / "delivery" / "model-call-events.jsonl",
            ]
        )
        with self.connect() as conn:
            if self._sync_signature_matches(conn, run_id, "run_trace", signature):
                return
            for event in load_run_events(run_dir):
                self._upsert_run_event_conn(conn, run_id, event)
            for event in load_tool_call_events(run_dir):
                self._upsert_tool_call_event_conn(conn, run_id, event)
            for event in load_model_call_events(run_dir):
                self._upsert_model_call_event_conn(conn, run_id, event)
            self._save_sync_signature(conn, run_id, "run_trace", signature)

    def _sync_signature_matches(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        sync_kind: str,
        signature: str,
    ) -> bool:
        row = conn.execute(
            """
            SELECT signature FROM runtime_sync_state
            WHERE run_id = ? AND sync_kind = ?
            """,
            (run_id, sync_kind),
        ).fetchone()
        return bool(row and str(row["signature"]) == signature)

    def _save_sync_signature(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        sync_kind: str,
        signature: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO runtime_sync_state (run_id, sync_kind, signature, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id, sync_kind) DO UPDATE SET
                signature = excluded.signature,
                updated_at = excluded.updated_at
            """,
            (run_id, sync_kind, signature, utc_now()),
        )

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
                    SET name = ?, status = 'demo_ready', updated_at = ?
                    WHERE id = ?
                    """,
                    (name, now, project_id),
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


def _file_signature(paths: list[Path]) -> str:
    entries: list[dict[str, object]] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            entries.append({"path": path.as_posix(), "missing": True})
            continue
        entries.append(
            {
                "path": path.as_posix(),
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
            }
        )
    return json.dumps(entries, sort_keys=True)


def _user(row: sqlite3.Row) -> User:
    return User(
        id=int(row["id"]),
        email=str(row["email"]),
        username=str(row["username"]),
        created_at=str(row["created_at"]),
    )


def _project(row: sqlite3.Row) -> Project:
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


def _run(row: sqlite3.Row) -> Run:
    return Run(
        id=int(row["id"]),
        project_id=int(row["project_id"]),
        run_uid=str(row["run_uid"]),
        run_dir=str(row["run_dir"]),
        status=str(row["status"]),
        mode=str(row["mode"]),
        reasoning=str(row["reasoning"]),
        generated_app_url=str(row["generated_app_url"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _provider(row: sqlite3.Row) -> ProviderCredential:
    return ProviderCredential(
        provider=str(row["provider"]),
        masked_value=str(row["masked_value"]),
        encrypted_value=str(row["encrypted_value"]),
        storage_mode=str(row["storage_mode"]),
        updated_at=str(row["updated_at"]),
    )


def _work_item(row: sqlite3.Row) -> WorkItem:
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


def _activity_event(row: sqlite3.Row) -> ActivityEvent:
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


def _artifact_record(row: sqlite3.Row) -> ArtifactRecord | None:
    payload = dict(row)
    payload["relative_path"] = payload.get("path", "")
    payload["owner_agent"] = payload.get("owner_agent") or payload.get("agent") or ""
    payload["external_refs"] = _json_column(payload.get("external_refs"), default=[])
    payload["metadata"] = _json_column(payload.get("metadata"), default={})
    return artifact_record_from_mapping(payload)


def _run_event(row: sqlite3.Row) -> RunEvent:
    payload = dict(row)
    payload["run_id"] = payload.get("runtime_run_id") or payload.get("run_id")
    payload["artifact_ids"] = _json_column(payload.get("artifact_ids"), default=[])
    payload["external_refs"] = _json_column(payload.get("external_refs"), default=[])
    payload["data"] = _json_column(payload.get("data"), default={})
    return run_event_from_mapping(payload)


def _tool_call_event(row: sqlite3.Row) -> ToolCallEvent:
    payload = dict(row)
    payload["run_id"] = payload.get("runtime_run_id") or payload.get("run_id")
    payload["input_summary"] = _json_column(payload.get("input_summary"), default={})
    payload["output_summary"] = _json_column(payload.get("output_summary"), default={})
    payload["artifact_ids"] = _json_column(payload.get("artifact_ids"), default=[])
    return tool_call_event_from_mapping(payload)


def _model_call_event(row: sqlite3.Row) -> ModelCallEvent:
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
    normalized = str(value or "").strip()
    aliases = {
        "BA": "PLAN-01",
        "BUSINESS-ANALYSIS": "PLAN-01",
        "BUSINESS_ANALYSIS": "PLAN-01",
        "REQUIREMENTS": "PLAN-01",
        "ARCH": "PLAN-02",
        "ARCHITECTURE": "PLAN-02",
        "PM": "PLAN-03",
        "PROJECT-MANAGEMENT": "PLAN-03",
        "PROJECT_MANAGEMENT": "PLAN-03",
        "TL": "PLAN-04",
        "TEAM-LEAD": "PLAN-04",
        "TEAM_LEAD": "PLAN-04",
    }
    return aliases.get(normalized.upper(), normalized)


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


def _work_item_id_for_run_event(event: RunEvent) -> str:
    explicit = _canonical_work_item_id(str(event.work_item_id or ""))
    if explicit:
        return explicit
    data = event.data if isinstance(event.data, dict) else {}
    for key in ("work_item_id", "target_work_item_id", "feature_id", "target"):
        value = _canonical_work_item_id(str(data.get(key) or ""))
        if value:
            return value
    token = f"{event.event_type} {data.get('node', '')} {data.get('stage', '')}".lower()
    if "business_analyst" in token or "business_analysis" in token:
        return "PLAN-01"
    if "architecture" in token or "architect" in token:
        return "PLAN-02"
    if "project_management" in token or "project_manager" in token:
        return "PLAN-03"
    if "team_lead" in token:
        return "PLAN-04"
    return ""


def _work_item_id_for_tool_event(event: ToolCallEvent) -> str:
    explicit = _canonical_work_item_id(str(event.work_item_id or ""))
    if explicit:
        return explicit
    if event.tool_name == "run_business_analyst":
        return "PLAN-01"
    if event.tool_name == "run_architect":
        return "PLAN-02"
    if event.tool_name == "run_project_manager":
        return "PLAN-03"
    if event.tool_name == "run_team_lead":
        return "PLAN-04"
    for source in (event.input_summary, event.output_summary):
        if not isinstance(source, dict):
            continue
        for key in ("work_item_id", "target_work_item_id", "feature_id", "target"):
            value = _canonical_work_item_id(str(source.get(key) or ""))
            if value:
                return value
    return ""


def _status_from_run_event(event: RunEvent) -> str:
    token = f"{event.event_type} {event.status}".lower()
    if "work_item_planned" in token:
        return "todo"
    if any(value in token for value in ("failed", "blocked")):
        return "blocked"
    if any(value in token for value in ("completed", "passed", "ready", "deployed")):
        return "done"
    if any(value in token for value in ("started", "running", "selected")):
        return "in_progress"
    return _normalize_work_item_status(event.status)


def _status_from_tool_event(event: ToolCallEvent) -> str:
    output = event.output_summary if isinstance(event.output_summary, dict) else {}
    dashboard_update = output.get("dashboard_update")
    dashboard = dashboard_update if isinstance(dashboard_update, dict) else {}
    dashboard_status = str(dashboard.get("status") or "")
    return _normalize_work_item_status(
        f"{dashboard_status} {event.status} {event.failure_mode or ''}"
    )


def _owner_agent_for_work_item(work_item_id: str, fallback_agent: str) -> str:
    owners = {
        "PLAN-01": "business-analyst-agent",
        "PLAN-02": "architect-agent",
        "PLAN-03": "project-manager-agent",
        "PLAN-04": "team-lead-agent",
    }
    return owners.get(work_item_id, fallback_agent or "fullstack-agent")


def _owner_agent_for_run_event(work_item_id: str, event: RunEvent) -> str:
    if work_item_id.startswith("PLAN-"):
        return _owner_agent_for_work_item(work_item_id, event.agent_id)
    data = event.data if isinstance(event.data, dict) else {}
    node = str(data.get("node") or "").strip().lower()
    if node in {"fullstack", "build", "builder"}:
        return "fullstack-agent"
    if node in {"qa", "quality", "quality_review"}:
        return "qa-agent"
    if node in {"deployment", "deploy", "publisher"}:
        return "deployment-agent"
    if node in {"handoff", "documentation", "release"}:
        return "documentation-handoff-agent"
    return event.agent_id or "fullstack-agent"


def _owner_agent_for_tool_event(work_item_id: str, event: ToolCallEvent) -> str:
    if work_item_id.startswith("PLAN-"):
        return _owner_agent_for_work_item(work_item_id, event.agent_id)
    return {
        "run_fullstack": "fullstack-agent",
        "run_qa": "qa-agent",
        "run_post_deploy_qa": "qa-agent",
        "run_deployment": "deployment-agent",
        "run_handoff": "documentation-handoff-agent",
        "complete_sprint": "team-lead-agent",
        "block_sprint": "team-lead-agent",
        "inspect_sprint_status": "team-lead-agent",
    }.get(event.tool_name, event.agent_id or "fullstack-agent")


def _message_from_run_event(event: RunEvent) -> str:
    if event.message and event.message != event.event_type:
        return event.message
    labels = {
        "work_item_planned": "Work item planned.",
        "business_analysis_started": "Business Analyst started working.",
        "business_analysis_completed": "Requirements brief is ready.",
        "architecture_started": "Solution Architect started working.",
        "architecture_completed": "Solution overview is ready.",
        "project_management_started": "Delivery Planner started working.",
        "project_management_completed": "Delivery plan is ready.",
        "team_lead_sprint_started": "Delivery Lead started sprint coordination.",
        "team_lead_sprint_completed": "Delivery Lead completed sprint coordination.",
        "team_lead_blocked_sprint": "Delivery Lead blocked the sprint.",
    }
    return labels.get(event.event_type, event.event_type.replace("_", " ").title())


def _message_from_tool_event(event: ToolCallEvent) -> str:
    output = event.output_summary if isinstance(event.output_summary, dict) else {}
    dashboard_update = output.get("dashboard_update")
    dashboard = dashboard_update if isinstance(dashboard_update, dict) else {}
    for value in (
        dashboard.get("comment"),
        dashboard.get("summary"),
        output.get("business_summary"),
        output.get("summary"),
        output.get("message"),
    ):
        if str(value or "").strip():
            return str(value).strip()
    return f"{event.tool_name.replace('_', ' ').title()} {event.status}".strip()


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value]
    return [str(value)]
