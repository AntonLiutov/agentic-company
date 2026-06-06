"""DB-backed runtime state service for work items, activity, and artifacts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentic_company.platform.artifact_registry import (
    ArtifactRecord,
    normalize_artifact_path,
    register_artifact,
)
from agentic_company.platform.tool_contracts import (
    ArtifactRegistrationRequest,
    ToolExecutionRecord,
    WorkItemExecutionPacket,
)
from agentic_company.platform.work_item_contracts import (
    HEAD_PLANNING_ITEMS,
    pm_sprints_from_run_dir,
    pm_work_items_from_run_dir,
)


@dataclass(frozen=True, slots=True)
class RuntimeWorkItem:
    """Canonical DB work-item snapshot used by specialist execution packets."""

    run_id: str
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SprintCompletionState:
    """Canonical DB sprint execution state."""

    run_id: str
    sprint_id: str
    status: str
    total_items: int
    pending_items: int
    blocked_items: int
    done_items: int
    is_final: bool
    next_work_item_id: str

    @property
    def has_items(self) -> bool:
        return self.total_items > 0

    @property
    def is_complete(self) -> bool:
        return self.has_items and self.pending_items == 0 and self.blocked_items == 0

    @property
    def is_blocked(self) -> bool:
        return self.status == "blocked" or self.blocked_items > 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def materialize_planning_items(run_id: str) -> None:
    repo, db_run_id = _repo_and_run(run_id)
    with repo.connect() as conn:
        for item in HEAD_PLANNING_ITEMS:
            _upsert_work_item_conn(
                conn,
                run_id=db_run_id,
                work_item_id=str(item["id"]),
                title=str(item["title"]),
                sprint_id=str(item["sprint_id"]),
                delivery_order=int(item["delivery_order"]),
                status="todo",
                owner_agent=str(item["suggested_owner_agent"]),
                source_refs=[str(value) for value in item.get("source_refs", [])],
            )


def materialize_pm_work_items(run_id: str, pm_artifacts: str | Path | None = None) -> None:
    repo, db_run_id = _repo_and_run(run_id)
    run_dir = Path(pm_artifacts) if pm_artifacts else Path(_run_row(repo, run_id)["run_dir"])
    sprints = pm_sprints_from_run_dir(run_dir)
    if sprints and not any(bool(sprint.get("is_final")) for sprint in sprints):
        final_sprint = max(sprints, key=lambda sprint: int(sprint.get("delivery_order") or 0))
        final_sprint["is_final"] = True
    for sprint in sprints:
        repo.upsert_sprint(
            db_run_id,
            sprint_id=str(sprint["sprint_id"]),
            title=str(sprint["title"]),
            delivery_order=int(sprint["delivery_order"]),
            status=str(sprint["status"]),
            is_final=bool(sprint["is_final"]),
            source_refs=[str(value) for value in sprint.get("source_refs", [])],
        )
    for item in pm_work_items_from_run_dir(run_dir):
        with repo.connect() as conn:
            _upsert_work_item_conn(
                conn,
                run_id=db_run_id,
                work_item_id=str(item["id"]),
                title=str(item["title"]),
                sprint_id=str(item["sprint_id"]),
                delivery_order=int(item["delivery_order"]),
                status=str(item.get("status") or "todo"),
                owner_agent=str(item["suggested_owner_agent"]),
                source_refs=[str(value) for value in item.get("source_refs", [])],
            )


def next_work_item(run_id: str, sprint_id: str) -> RuntimeWorkItem | None:
    repo, db_run_id = _repo_and_run(run_id)
    with repo.connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM work_items
            WHERE run_id = ?
              AND sprint_id = ?
              AND status IN ('todo', 'blocked')
            ORDER BY delivery_order, work_item_id
            LIMIT 1
            """,
            (db_run_id, sprint_id),
        ).fetchone()
    return _work_item_from_row(row, runtime_run_id=run_id) if row else None


def list_sprint_work_items(run_id: str, sprint_id: str) -> list[RuntimeWorkItem]:
    repo, db_run_id = _repo_and_run(run_id)
    with repo.connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM work_items
            WHERE run_id = ? AND sprint_id = ?
            ORDER BY delivery_order, work_item_id
            """,
            (db_run_id, sprint_id),
        ).fetchall()
    return [_work_item_from_row(row, runtime_run_id=run_id) for row in rows]


def sprint_ids(run_id: str) -> list[str]:
    repo, db_run_id = _repo_and_run(run_id)
    with repo.connect() as conn:
        rows = conn.execute(
            """
            SELECT sprint_id
            FROM sprints
            WHERE run_id = ?
            ORDER BY delivery_order, sprint_id
            """,
            (db_run_id,),
        ).fetchall()
    return [str(row["sprint_id"]) for row in rows]


def next_pending_work_item(run_id: str, sprint_id: str) -> RuntimeWorkItem | None:
    return next_work_item(run_id, sprint_id)


def sprint_completion_state(run_id: str, sprint_id: str) -> SprintCompletionState:
    repo, db_run_id = _repo_and_run(run_id)
    with repo.connect() as conn:
        sprint_row = conn.execute(
            """
            SELECT status, is_final FROM sprints
            WHERE run_id = ? AND sprint_id = ?
            """,
            (db_run_id, sprint_id),
        ).fetchone()
        item_rows = conn.execute(
            """
            SELECT work_item_id, status FROM work_items
            WHERE run_id = ? AND sprint_id = ?
            ORDER BY delivery_order, work_item_id
            """,
            (db_run_id, sprint_id),
        ).fetchall()
    statuses = [str(row["status"]) for row in item_rows]
    next_item = next((row for row in item_rows if str(row["status"]) in {"todo", "blocked"}), None)
    return SprintCompletionState(
        run_id=run_id,
        sprint_id=sprint_id,
        status=str(sprint_row["status"] if sprint_row else ""),
        total_items=len(item_rows),
        pending_items=sum(1 for status in statuses if status in {"todo", "in_progress", "review"}),
        blocked_items=sum(1 for status in statuses if status == "blocked"),
        done_items=sum(1 for status in statuses if status == "done"),
        is_final=bool(sprint_row and sprint_row["is_final"]),
        next_work_item_id=str(next_item["work_item_id"] if next_item else ""),
    )


def mark_sprint_started(run_id: str, sprint_id: str) -> None:
    _update_sprint_status(run_id, sprint_id, "running")


def mark_sprint_done(run_id: str, sprint_id: str) -> None:
    _update_sprint_status(run_id, sprint_id, "done")


def mark_sprint_blocked(run_id: str, sprint_id: str, blocker: str = "") -> None:
    _update_sprint_status(run_id, sprint_id, "blocked")
    if blocker:
        record = ToolExecutionRecord(
            run_id=run_id,
            work_item_id="PLAN-04",
            sprint_id="planning",
            owner_agent="team-lead-agent",
            tool_name="block_sprint",
            tool_call_id=f"{run_id}:team-lead-agent:block_sprint:sprint-status",
            attempt_id="sprint",
            status="blocked",
            activity_message=blocker,
        )
        record_work_item_transition(record)


def next_sprint_to_run(run_id: str) -> str | None:
    repo, db_run_id = _repo_and_run(run_id)
    with repo.connect() as conn:
        rows = conn.execute(
            """
            SELECT sprint_id, status
            FROM sprints
            WHERE run_id = ?
            ORDER BY delivery_order, sprint_id
            """,
            (db_run_id,),
        ).fetchall()
    for row in rows:
        state = sprint_completion_state(run_id, str(row["sprint_id"]))
        if state.has_items and not state.is_complete and not state.is_blocked:
            return state.sprint_id
    return None


def sprint_is_final(run_id: str, sprint_id: str) -> bool:
    repo, db_run_id = _repo_and_run(run_id)
    return repo.sprint_is_final(db_run_id, sprint_id)


def get_work_item(run_id: str, work_item_id: str) -> RuntimeWorkItem:
    repo, db_run_id = _repo_and_run(run_id)
    with repo.connect() as conn:
        row = conn.execute(
            "SELECT * FROM work_items WHERE run_id = ? AND work_item_id = ?",
            (db_run_id, work_item_id),
        ).fetchone()
    if row is None:
        raise ValueError(f"Unknown work_item_id for run {run_id}: {work_item_id}")
    return _work_item_from_row(row, runtime_run_id=run_id)


def completed_work_item_ids(run_id: str, sprint_id: str = "") -> list[str]:
    repo, db_run_id = _repo_and_run(run_id)
    params: list[Any] = [db_run_id]
    clause = "run_id = ? AND status = 'done'"
    if sprint_id:
        clause += " AND sprint_id = ?"
        params.append(sprint_id)
    with repo.connect() as conn:
        rows = conn.execute(
            f"SELECT work_item_id FROM work_items WHERE {clause} ORDER BY delivery_order, id",
            tuple(params),
        ).fetchall()
    return [str(row["work_item_id"]) for row in rows]


def count_tool_call_events(
    run_id: str,
    *,
    agent_id: str = "",
    tool_name: str = "",
    work_item_id: str = "",
) -> int:
    repo, db_run_id = _repo_and_run(run_id)
    clauses = ["run_id = ?"]
    params: list[Any] = [db_run_id]
    if agent_id:
        clauses.append("agent_id = ?")
        params.append(agent_id)
    if tool_name:
        clauses.append("tool_name = ?")
        params.append(tool_name)
    if work_item_id:
        clauses.append("work_item_id = ?")
        params.append(work_item_id)
    with repo.connect() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS count FROM tool_call_events WHERE {' AND '.join(clauses)}",
            tuple(params),
        ).fetchone()
    return int(row["count"] if row else 0)


def packet_for_work_item(
    *,
    run_id: str,
    work_item_id: str,
    tool_name: str,
    tool_call_id: str,
    attempt_id: str,
    status: str = "in_progress",
    owner_agent: str = "",
) -> WorkItemExecutionPacket:
    item = get_work_item(run_id, work_item_id)
    packet = WorkItemExecutionPacket(
        run_id=run_id,
        work_item_id=item.work_item_id,
        sprint_id=item.sprint_id,
        owner_agent=owner_agent or item.owner_agent,
        assigned_agent=item.assigned_agent,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        attempt_id=attempt_id,
        status=status,
        params={"work_item": item.to_dict()},
    )
    packet.validate()
    return packet


def record_work_item_transition(record: ToolExecutionRecord) -> None:
    record.validate()
    repo, db_run_id = _repo_and_run(record.run_id)
    now = _now()
    event_id = f"work:{record.tool_call_id}:{record.attempt_id}:{uuid4().hex}"
    with repo.connect() as conn:
        row = conn.execute(
            "SELECT status, owner_agent FROM work_items WHERE run_id = ? AND work_item_id = ?",
            (db_run_id, record.work_item_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown work_item_id for run {record.run_id}: {record.work_item_id}")
        from_status = str(row["status"])
        from_owner = str(row["owner_agent"])
        requested_status = _normalize_status(record.status)
        effective_status = _effective_transition_status(
            current_status=from_status,
            requested_status=requested_status,
            tool_name=record.tool_name,
        )
        lane = _lane_for_status(effective_status)
        effective_owner = (
            from_owner
            if from_status == "done" and effective_status == "done"
            else record.owner_agent
        )
        conn.execute(
            """
            UPDATE work_items
            SET status = ?,
                lane = ?,
                owner_agent = ?,
                assigned_agent = ?,
                active = ?,
                blocker = ?,
                updated_at = ?
            WHERE run_id = ? AND work_item_id = ?
            """,
            (
                effective_status,
                lane,
                effective_owner,
                effective_owner,
                1 if lane in {"in_progress", "qa"} else 0,
                record.activity_message if lane == "blocked" else "",
                now,
                db_run_id,
                record.work_item_id,
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
            """,
            (
                db_run_id,
                event_id,
                record.work_item_id,
                record.tool_name,
                from_status,
                effective_status,
                from_owner,
                effective_owner,
                effective_owner,
                record.tool_name,
                record.tool_call_id,
                record.activity_message,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO activity_events (
                run_id, event_id, work_item_id, owner_agent, agent_id, tool_name,
                message, status, artifact_ids, visibility, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'user', ?)
            """,
            (
                db_run_id,
                event_id,
                record.work_item_id,
                effective_owner,
                effective_owner,
                record.tool_name,
                record.activity_message,
                effective_status,
                json.dumps(list(record.artifact_ids), sort_keys=True),
                now,
            ),
        )


def record_run_lifecycle(
    run_id: str,
    status: str,
    *,
    generated_app_url: str = "",
    target_project_dir: str = "",
) -> None:
    """Persist run-level lifecycle fields in the canonical console DB row."""

    repo, db_run_id = _repo_and_run(run_id)
    repo.update_run_status(db_run_id, status, generated_app_url=generated_app_url)
    if target_project_dir:
        repo.update_run_target_project_dir(db_run_id, target_project_dir)


def run_target_project_dir(run_id: str) -> str:
    repo, _ = _repo_and_run(run_id)
    row = _run_row(repo, run_id)
    return str(row["target_project_dir"] or "")


def record_execution_request(run_id: str, payload: dict[str, Any]) -> None:
    """Persist a specialist execution request packet in the canonical DB."""

    repo, db_run_id = _repo_and_run(run_id)
    execution_id = str(payload.get("execution_id") or payload.get("agent_id") or "current")
    repo.upsert_execution_request(
        db_run_id,
        execution_id=execution_id,
        agent_id=str(payload.get("agent_id") or ""),
        request_payload=payload,
    )


def latest_execution_request(run_id: str) -> dict[str, Any]:
    repo, db_run_id = _repo_and_run(run_id)
    payload = repo.latest_execution_request(db_run_id)
    if not payload:
        raise ValueError(f"Missing DB execution request contract for run {run_id}")
    return payload


def update_execution_request(run_id: str, updates: dict[str, Any]) -> None:
    payload = latest_execution_request(run_id)
    payload.update({key: value for key, value in updates.items() if value is not None})
    record_execution_request(run_id, payload)


def record_delivery_state_snapshot(state: dict[str, Any]) -> None:
    """Persist a graph state snapshot in the canonical DB."""

    repo, db_run_id = _repo_and_run(str(state["run_id"]))
    snapshot_id = f"state:{state.get('stage', '')}:{state.get('status', '')}:{uuid4().hex}"
    repo.upsert_delivery_state_snapshot(
        db_run_id,
        snapshot_id=snapshot_id,
        stage=str(state.get("stage") or ""),
        status=str(state.get("status") or ""),
        state_payload=dict(state),
    )


def record_activity_event(record: ToolExecutionRecord) -> None:
    record_work_item_transition(record)


def record_artifact_link(
    run_dir: Path,
    request: ArtifactRegistrationRequest,
) -> ArtifactRecord:
    request.validate()
    record = register_artifact(
        run_dir,
        artifact_id=request.artifact_id,
        relative_path=request.relative_path,
        run_id=request.run_id,
        work_item_id=request.work_item_id or None,
        owner_agent=request.owner_agent,
        artifact_type=request.artifact_type,
        visibility=request.visibility,
        label=request.label,
        source_tool=request.source_tool,
    )
    repo, db_run_id = _repo_and_run(request.run_id)
    with repo.connect() as conn:
        repo._upsert_artifact_record_conn(conn, db_run_id, record)
        if record.work_item_id:
            row = conn.execute(
                "SELECT artifact_ids FROM work_items WHERE run_id = ? AND work_item_id = ?",
                (db_run_id, record.work_item_id),
            ).fetchone()
            if row is None:
                raise ValueError(
                    "Artifact registration references unknown work_item_id: "
                    f"{record.work_item_id}"
                )
            artifact_ids = _json_list(row["artifact_ids"])
            if record.artifact_id not in artifact_ids:
                artifact_ids.append(record.artifact_id)
            conn.execute(
                """
                UPDATE work_items
                SET artifact_ids = ?, updated_at = ?
                WHERE run_id = ? AND work_item_id = ?
                """,
                (
                    json.dumps(artifact_ids, sort_keys=True),
                    _now(),
                    db_run_id,
                    record.work_item_id,
                ),
            )
    _record_artifact_content(repo, db_run_id, run_dir, record)
    return record


def artifact_links_for_paths(run_id: str, paths: list[str]) -> tuple[Any, ...]:
    """Return DB-backed artifact links for explicit run-local paths."""

    repo, db_run_id = _repo_and_run(run_id)
    normalized = [normalize_artifact_path(path) for path in paths if str(path or "").strip()]
    if not normalized:
        return ()
    # Build through the public repository method to keep row conversion centralized.
    by_path = {record.relative_path: record for record in repo.list_artifact_records(db_run_id)}
    missing = [path for path in normalized if path not in by_path]
    if missing:
        raise ValueError(
            "Artifact refs must be registered in DB before use: " + ", ".join(sorted(missing))
        )
    return tuple(by_path[path].to_tool_ref() for path in normalized)


def artifact_paths_by_owner(run_id: str, owner_agent: str) -> list[str]:
    """List registered artifact paths for one owner agent from DB metadata."""

    repo, db_run_id = _repo_and_run(run_id)
    return [
        record.relative_path
        for record in repo.list_artifact_records(db_run_id)
        if record.owner_agent == owner_agent
    ]


def artifact_paths_by_type(run_id: str, artifact_types: set[str]) -> list[str]:
    """List registered artifact paths for explicit artifact types from DB metadata."""

    repo, db_run_id = _repo_and_run(run_id)
    return [
        record.relative_path
        for record in repo.list_artifact_records(db_run_id)
        if record.artifact_type in artifact_types
    ]


def _repo_and_run(run_id: str):
    from agentic_company.console.web.db import ConsoleRepository

    repo = ConsoleRepository()
    repo.init_schema()
    row = _run_row(repo, run_id)
    return repo, int(row["id"])


def _update_sprint_status(run_id: str, sprint_id: str, status: str) -> None:
    repo, db_run_id = _repo_and_run(run_id)
    with repo.connect() as conn:
        row = conn.execute(
            "SELECT sprint_id FROM sprints WHERE run_id = ? AND sprint_id = ?",
            (db_run_id, sprint_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown sprint_id for run {run_id}: {sprint_id}")
        conn.execute(
            """
            UPDATE sprints
            SET status = ?, updated_at = ?
            WHERE run_id = ? AND sprint_id = ?
            """,
            (status, _now(), db_run_id, sprint_id),
        )


def _record_artifact_content(
    repo: Any,
    db_run_id: int,
    run_dir: Path,
    record: ArtifactRecord,
) -> None:
    path = run_dir / record.relative_path
    if not path.exists() or not path.is_file():
        return
    content_kind = _artifact_content_kind(record.relative_path)
    try:
        if content_kind == "json":
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            repo.upsert_artifact_content(
                db_run_id,
                artifact_id=record.artifact_id,
                path=record.relative_path,
                content_kind=content_kind,
                content_json=payload,
            )
            return
        repo.upsert_artifact_content(
            db_run_id,
            artifact_id=record.artifact_id,
            path=record.relative_path,
            content_kind=content_kind,
            content_text=_read_text(path) if content_kind != "binary_ref" else "",
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return


def _artifact_content_kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in {".md", ".txt", ".html", ".mmd", ".csv", ".log"}:
        return suffix.lstrip(".") or "text"
    return "binary_ref"


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _run_row(repo: Any, run_id: str) -> Any:
    with repo.connect() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE run_uid = ? OR CAST(id AS TEXT) = ?",
            (str(run_id), str(run_id)),
        ).fetchone()
    if row is None:
        raise ValueError(f"Unknown runtime run_id: {run_id}")
    return row


def _upsert_work_item_conn(
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
) -> None:
    now = _now()
    normalized = _normalize_status(status)
    conn.execute(
        """
        INSERT INTO work_items (
            run_id, work_item_id, title, sprint_id, delivery_order, status,
            lane, owner_agent, assigned_agent, active, source_refs,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
        ON CONFLICT(run_id, work_item_id) DO UPDATE SET
            title = excluded.title,
            sprint_id = excluded.sprint_id,
            delivery_order = excluded.delivery_order,
            owner_agent = excluded.owner_agent,
            assigned_agent = excluded.assigned_agent,
            source_refs = excluded.source_refs,
            updated_at = excluded.updated_at
        """,
        (
            run_id,
            work_item_id,
            title,
            sprint_id,
            delivery_order,
            normalized,
            _lane_for_status(normalized),
            owner_agent,
            owner_agent,
            json.dumps(source_refs, sort_keys=True),
            now,
            now,
        ),
    )


def _work_item_from_row(row: Any, *, runtime_run_id: str) -> RuntimeWorkItem:
    return RuntimeWorkItem(
        run_id=runtime_run_id,
        work_item_id=str(row["work_item_id"]),
        title=str(row["title"]),
        sprint_id=str(row["sprint_id"]),
        delivery_order=int(row["delivery_order"]),
        status=str(row["status"]),
        lane=str(row["lane"]),
        owner_agent=str(row["owner_agent"]),
        assigned_agent=str(row["assigned_agent"]),
        active=bool(row["active"]),
        source_refs=_json_list(row["source_refs"]),
        artifact_ids=_json_list(row["artifact_ids"]),
        blocker=str(row["blocker"] or ""),
    )


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def _normalize_status(status: str) -> str:
    token = status.strip().lower()
    if token in {"", "pending", "planned", "backlog"}:
        return "todo"
    if any(value in token for value in ("failed", "blocked", "repair")):
        return "blocked"
    if any(value in token for value in ("completed", "passed", "ready", "done", "deployed")):
        return "done"
    if any(value in token for value in ("qa", "review", "inspect", "implemented")):
        return "review"
    if any(value in token for value in ("started", "running", "progress")):
        return "in_progress"
    return token if token in {"todo", "in_progress", "review", "done", "blocked"} else "in_progress"


def _lane_for_status(status: str) -> str:
    normalized = _normalize_status(status)
    return "qa" if normalized == "review" else normalized


def _effective_transition_status(
    *,
    current_status: str,
    requested_status: str,
    tool_name: str,
) -> str:
    """Prevent read-only inspections from downgrading completed cards."""

    current = _normalize_status(current_status)
    requested = _normalize_status(requested_status)
    if current == "done" and tool_name == "inspect_sprint_status":
        return current
    return requested


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
