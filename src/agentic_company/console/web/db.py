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

        for record in load_artifact_registry(run_dir):
            self.upsert_artifact_record(run_id, record)

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


def _artifact_record(row: sqlite3.Row) -> ArtifactRecord | None:
    payload = dict(row)
    payload["relative_path"] = payload.get("path", "")
    payload["owner_agent"] = payload.get("owner_agent") or payload.get("agent") or ""
    payload["external_refs"] = _json_column(payload.get("external_refs"), default=[])
    payload["metadata"] = _json_column(payload.get("metadata"), default={})
    return artifact_record_from_mapping(payload)


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
