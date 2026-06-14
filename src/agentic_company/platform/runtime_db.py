"""DB-backed runtime state service for work items, activity, and artifacts."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentic_company.platform.artifact_registry import (
    ArtifactRecord,
    normalize_artifact_path,
    register_artifact,
    resolve_run_artifact_path,
)
from agentic_company.platform.run_finalizer import TERMINAL_RUN_STATUSES, RunStatus
from agentic_company.platform.status import (
    InvalidStatusTransition,
    WorkItemStatus,
    classify_work_item_status,
    transition,
)
from agentic_company.platform.tool_contracts import (
    ActivityEventRecord,
    ArtifactRegistrationRequest,
    ToolExecutionRecord,
    WorkItemExecutionPacket,
)
from agentic_company.platform.work_item_contracts import (
    HEAD_PLANNING_ITEMS,
    pm_sprints_from_run_dir,
    pm_work_items_from_run_dir,
)

LOGGER = logging.getLogger(__name__)


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


@dataclass(frozen=True, slots=True)
class WorkItemClaimResult:
    """Result of attempting to claim a sprint work item for execution."""

    claimed: bool
    work_item_id: str
    blocking_work_item_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RunReconcileSnapshot:
    """Frozen DB world snapshot for one run reconciliation pass."""

    run_id: str
    db_run_id: int
    status: str
    updated_at: str
    control_intent: str
    control_intent_reason: str
    sprint_count: int
    empty_sprints: int
    incomplete_sprints: int
    blocked_sprints: int
    open_delivery_items: int
    active_items: int
    blocked_items: int


@dataclass(frozen=True, slots=True)
class RunReconcileResult:
    """Outcome of applying one reconciliation decision."""

    action: str
    applied: bool
    reason: str
    status: str


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
    _mirror_seed_work_items(repo, db_run_id, run_id)  # all planning items -> board at once


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
    _mirror_seed_work_items(repo, db_run_id, run_id)  # all feature items -> board at once


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


def blocked_work_items(run_id: str, sprint_id: str = "") -> list[RuntimeWorkItem]:
    """Return canonical blocked work items for current DB-backed blocker reporting."""

    repo, db_run_id = _repo_and_run(run_id)
    params: list[Any] = [db_run_id]
    clause = "run_id = ? AND status = 'blocked'"
    if sprint_id:
        clause += " AND sprint_id = ?"
        params.append(sprint_id)
    with repo.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM work_items
            WHERE {clause}
            ORDER BY sprint_id, delivery_order, work_item_id
            """,
            tuple(params),
        ).fetchall()
    return [_work_item_from_row(row, runtime_run_id=run_id) for row in rows]


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

    def operation() -> tuple[Any, int]:
        repo, db_run_id = _repo_and_run(record.run_id)
        now = _now()
        with repo.connect() as conn:
            _record_work_item_transition_conn(conn, db_run_id, record, now)
        return repo, db_run_id

    _with_db_retry(operation)
    # DB is committed and is the source of truth; mirroring the new state onto an
    # external board (if any) runs async off the critical path and never blocks.
    _submit_item_mirror(record.run_id, record.work_item_id)


# --- async board mirror -------------------------------------------------------
# Per (run_uid, work_item_id) de-dup so repeated transitions/seeds never re-issue
# the same gh calls. Guarded because the background mirror threads share it.
_MIRROR_STATE_LOCK = threading.Lock()
_MIRROR_CARDED: set[tuple[str, str]] = set()
_MIRROR_STATUS: dict[tuple[str, str], str] = {}
_MIRROR_MILESTONED: set[tuple[str, str]] = set()
_NO_MIRROR_RUNS: set[str] = set()  # runs with no external board -> skip all work fast


def _submit_item_mirror(run_uid: str, work_item_id: str) -> None:
    """Schedule a work item's board mirror off the run's critical path."""
    from agentic_company.platform.mirror_dispatch import submit_mirror

    submit_mirror((run_uid, work_item_id), lambda: mirror_work_item_now(run_uid, work_item_id))


def mirror_work_item_now(run_uid: str, work_item_id: str) -> None:
    """Apply one work item's current state to the external board (worker thread).

    Reads the item's CURRENT status (so stacked events coalesce to the latest) and
    only issues the gh calls that changed since this process last mirrored it.
    """
    if run_uid in _NO_MIRROR_RUNS:
        return  # known internal-board run -> no external board, skip without a DB hit
    try:
        from agentic_company.platform.run_mirror import get_run_mirror

        repo, db_run_id = _repo_and_run(run_uid)
        mirror = get_run_mirror(repo, db_run_id)
        if mirror is None:
            _NO_MIRROR_RUNS.add(run_uid)  # remember -> later items skip instantly
            return
        from agentic_company.ports.board import BoardItem

        item = get_work_item(run_uid, work_item_id)
        key = (run_uid, work_item_id)
        with _MIRROR_STATE_LOCK:
            need_card = key not in _MIRROR_CARDED
            need_status = _MIRROR_STATUS.get(key) != item.status
            need_milestone = key not in _MIRROR_MILESTONED
        if need_card:
            mirror.mirror_item(
                BoardItem(work_item_id=work_item_id, title=item.title, body=_issue_body(item))
            )
            with _MIRROR_STATE_LOCK:
                _MIRROR_CARDED.add(key)
        if need_status:
            mirror.mirror_status(work_item_id, item.status)
            with _MIRROR_STATE_LOCK:
                _MIRROR_STATUS[key] = item.status
        if need_milestone:
            sprint_title = _sprint_title(item.sprint_id)
            if sprint_title:  # group the card under its sprint (board Milestone)
                mirror.mirror_milestone(work_item_id, sprint_title)
                with _MIRROR_STATE_LOCK:
                    _MIRROR_MILESTONED.add(key)
    except Exception as exc:  # best-effort: a board mirror must not break a run
        LOGGER.warning("Work-item mirror failed (%s): %s", work_item_id, exc)


# Friendly agent labels — the SAME names ADL shows on its own dashboard, so a
# board comment reads "Builder" / "Quality Reviewer", not "fullstack-agent".
_AGENT_DISPLAY = {
    "business-analyst-agent": "Business Analyst",
    "architect-agent": "Solution Architect",
    "project-manager-agent": "Delivery Planner",
    "fullstack-agent": "Builder",
    "qa-agent": "Quality Reviewer",
    "deployment-agent": "Publisher",
    "documentation-handoff-agent": "Release Reporter",
    "team-lead-agent": "Delivery Lead",
    "head-agent": "Coordinator",
}
# Only these artifact suffixes go on the board (no .json / internal traces).
_BOARD_ARTIFACT_SUFFIXES = (".md", ".csv", ".png", ".mmd")


def _issue_body(item: RuntimeWorkItem) -> str:
    """A meaningful GitHub issue body (not just '_Tracked by ADL_')."""
    sprint = _sprint_title(item.sprint_id) or item.sprint_id or "—"
    owner = _AGENT_DISPLAY.get(item.owner_agent, item.owner_agent or "—")
    lines = [
        f"**Work item** `{item.work_item_id}` · **Sprint:** {sprint} · **Owner:** {owner}",
    ]
    if item.title:
        lines += ["", f"### {item.title}"]
    if item.source_refs:
        lines += ["", "**Source refs:** " + ", ".join(f"`{r}`" for r in item.source_refs)]
    lines += [
        "",
        "_Mirrored from Agentic Delivery Lab — status, sprint and agent updates sync._",
    ]
    return "\n".join(lines)


def _submit_response_comment(
    run_uid: str,
    work_item_id: str,
    from_agent: str,
    content: str,
    artifact_refs: list[str],
    message_id: str,
) -> None:
    """Schedule an agent's final message (+ its artifacts) as a board comment."""
    if not work_item_id or not (content or "").strip():
        return
    from agentic_company.platform.mirror_dispatch import submit_mirror

    refs = list(artifact_refs or [])
    submit_mirror(
        (run_uid, work_item_id, "comment", message_id),
        lambda: mirror_response_comment_now(
            run_uid, work_item_id, from_agent, content, refs, message_id
        ),
    )


def mirror_response_comment_now(
    run_uid: str,
    work_item_id: str,
    from_agent: str,
    content: str,
    artifact_refs: list[str],
    message_id: str,
) -> None:
    """Post an agent's final message + artifact links onto its board card."""
    if run_uid in _NO_MIRROR_RUNS:
        return
    try:
        from agentic_company.platform.run_mirror import get_run_mirror
        from agentic_company.ports.board import BoardComment, BoardItem

        repo, db_run_id = _repo_and_run(run_uid)
        mirror = get_run_mirror(repo, db_run_id)
        if mirror is None:
            _NO_MIRROR_RUNS.add(run_uid)
            return
        item = get_work_item(run_uid, work_item_id)
        # Ensure the card exists before commenting (idempotent).
        mirror.mirror_item(
            BoardItem(work_item_id=work_item_id, title=item.title, body=_issue_body(item))
        )
        role = _AGENT_DISPLAY.get(from_agent, from_agent or "Agent")
        body = f"**{role}**\n\n{content.strip()}"
        arts = [r for r in artifact_refs if str(r).lower().endswith(_BOARD_ARTIFACT_SUFFIXES)]
        if arts:
            body += "\n\n**Artifacts:**\n" + "\n".join(f"- `{a}`" for a in arts)
        mirror.mirror_comment(
            BoardComment(
                work_item_id,
                body,
                idempotency_key=f"{work_item_id}:msg:{message_id}",
            )
        )
    except Exception as exc:  # best-effort: a comment mirror must not break a run
        LOGGER.warning("Response-comment mirror failed (%s): %s", work_item_id, exc)


def _sprint_title(sprint_id: str) -> str:
    """Board Milestone name for a work item's sprint.

    The SAME label ADL shows on its own board: 'sprint-01' -> 'Sprint 1',
    'planning' -> 'Planning'. We mirror the sprint_id label, NOT the PM's
    descriptive sprint title, so the milestones read Sprint 1 / Sprint 2 / ...
    """
    from agentic_company.console.web.product import sprint_label

    sid = (sprint_id or "").strip()
    return sprint_label(sid) if sid else ""


def _mirror_seed_work_items(repo: Any, db_run_id: int, run_uid: str) -> None:
    """Schedule a board mirror for every current work item the moment items exist.

    So the whole backlog shows up at once (all in their column, usually To Do)
    instead of cards trickling in as items are later picked up. Each item is
    dispatched to the background pool, so they mirror in parallel without blocking
    the run. De-dup makes a repeated seed a no-op.
    """
    try:
        for item in repo.list_work_items(db_run_id):
            _submit_item_mirror(run_uid, item.work_item_id)
    except Exception as exc:  # best-effort: a board mirror must not break a run
        LOGGER.warning("Seed mirror failed for run %s: %s", db_run_id, exc)


def claim_work_item_for_execution(record: ToolExecutionRecord) -> WorkItemClaimResult:
    """Claim a sprint work item before running an expensive specialist worker.

    The Team Lead may receive several tool calls in a single model turn. This DB
    precondition is the runtime gate: only one non-planning work item per sprint
    can be active at a time, and a second queued tool call must fail before it
    writes a request or starts Codex.
    """

    record.validate()

    def operation() -> tuple[Any, int, WorkItemClaimResult]:
        repo, db_run_id = _repo_and_run(record.run_id)
        now = _now()
        with repo.connect() as conn:
            result = _claim_work_item_for_execution_conn(conn, db_run_id, record, now)
        return repo, db_run_id, result

    _repo, _db_run_id, result = _with_db_retry(operation)
    if result.claimed:  # the item just went in_progress -> mirror its start...
        _submit_item_mirror(record.run_id, record.work_item_id)
        # ...and let its card settle (bounded) before the worker starts on it.
        from agentic_company.platform.mirror_dispatch import flush_mirror

        flush_mirror((record.run_id, record.work_item_id))
    return result


def _claim_work_item_for_execution_conn(
    conn: Any,
    db_run_id: int,
    record: ToolExecutionRecord,
    now: str,
) -> WorkItemClaimResult:
    target = conn.execute(
        """
        SELECT work_item_id, sprint_id, status, active
        FROM work_items
        WHERE run_id = ? AND work_item_id = ?
        """,
        (db_run_id, record.work_item_id),
    ).fetchone()
    if target is None:
        raise ValueError(f"Unknown work_item_id for run {record.run_id}: {record.work_item_id}")
    if str(target["sprint_id"]) != record.sprint_id:
        return WorkItemClaimResult(
            claimed=False,
            work_item_id=record.work_item_id,
            reason=(
                f"work_item_id {record.work_item_id} belongs to sprint "
                f"{target['sprint_id']}, not {record.sprint_id}."
            ),
        )
    if str(target["status"]) == "done":
        return WorkItemClaimResult(
            claimed=False,
            work_item_id=record.work_item_id,
            reason=f"Cannot start {record.work_item_id}; it is already done.",
        )
    conn.execute(
        """
        UPDATE sprints
        SET updated_at = ?
        WHERE run_id = ? AND sprint_id = ?
        """,
        (now, db_run_id, record.sprint_id),
    )
    blocking = conn.execute(
        """
        SELECT work_item_id
        FROM work_items
        WHERE run_id = ?
          AND sprint_id = ?
          AND work_item_id <> ?
          AND (active = 1 OR status IN ('in_progress', 'review'))
        ORDER BY delivery_order, work_item_id
        LIMIT 1
        """,
        (db_run_id, record.sprint_id, record.work_item_id),
    ).fetchone()
    if blocking is not None:
        blocking_id = str(blocking["work_item_id"])
        return WorkItemClaimResult(
            claimed=False,
            work_item_id=record.work_item_id,
            blocking_work_item_id=blocking_id,
            reason=(
                f"Cannot start {record.work_item_id}; {blocking_id} is already active "
                f"in {record.sprint_id}."
            ),
        )
    _record_work_item_transition_conn(conn, db_run_id, record, now)
    return WorkItemClaimResult(claimed=True, work_item_id=record.work_item_id)


def record_run_lifecycle(
    run_id: str,
    status: str,
    *,
    generated_app_url: str = "",
    target_project_dir: str = "",
) -> None:
    """Persist run-level lifecycle fields in the canonical console DB row.

    A run's outcome is settled by the first terminal status written: once the row
    is terminal, a later differing status is ignored so a late finalizer cannot
    overwrite a user stop or a recorded failure. The status check and write happen
    in one atomic statement so concurrent finalizers cannot race.
    """

    from agentic_company.platform.run_finalizer import TERMINAL_RUN_STATUSES

    repo, db_run_id = _repo_and_run(run_id)
    repo.update_run_status(
        db_run_id,
        status,
        generated_app_url=generated_app_url,
        keep_status_when_in=tuple(TERMINAL_RUN_STATUSES),
    )
    if target_project_dir:
        repo.update_run_target_project_dir(db_run_id, target_project_dir)


def request_run_control_intent(run_id: str, intent: str, reason: str = "") -> None:
    """Persist an operator control intent on the canonical run row."""

    normalized = intent.strip().lower()
    if normalized not in {"cancel", "pause", "resume", "retry", "restart"}:
        raise ValueError(f"Unsupported run control intent: {intent}")
    repo, db_run_id = _repo_and_run(run_id)
    repo.set_run_control_intent(db_run_id, intent=normalized, reason=reason)


def run_control_intent(run_id: str) -> str:
    """Return the current DB-backed control intent for a run."""

    try:
        repo, _ = _repo_and_run(run_id)
    except ValueError:
        return ""
    row = _run_row(repo, run_id)
    return str(row["control_intent"] if "control_intent" in row.keys() else "")


def build_run_reconcile_snapshot(run_id: str) -> RunReconcileSnapshot:
    """Build the frozen DB world snapshot used by the Phase 1 reconciler."""

    repo, db_run_id = _repo_and_run(run_id)
    row = _run_row(repo, run_id)
    sprints = sprint_ids(run_id)
    states = [sprint_completion_state(run_id, sprint_id) for sprint_id in sprints]
    with repo.connect() as conn:
        active_row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) AS active_count,
                SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked_count,
                SUM(
                    CASE
                        WHEN sprint_id != 'planning' AND status != 'done'
                        THEN 1
                        ELSE 0
                    END
                ) AS open_delivery_count
            FROM work_items
            WHERE run_id = ?
            """,
            (db_run_id,),
        ).fetchone()
    return RunReconcileSnapshot(
        run_id=run_id,
        db_run_id=db_run_id,
        status=str(row["status"]),
        updated_at=str(row["updated_at"]),
        control_intent=str(row["control_intent"] if "control_intent" in row.keys() else ""),
        control_intent_reason=str(
            row["control_intent_reason"] if "control_intent_reason" in row.keys() else ""
        ),
        sprint_count=len(states),
        empty_sprints=sum(1 for state in states if not state.has_items),
        incomplete_sprints=sum(1 for state in states if state.has_items and not state.is_complete),
        blocked_sprints=sum(1 for state in states if state.is_blocked),
        open_delivery_items=int(active_row["open_delivery_count"] or 0) if active_row else 0,
        active_items=int(active_row["active_count"] or 0) if active_row else 0,
        blocked_items=int(active_row["blocked_count"] or 0) if active_row else 0,
    )


def reconcile_run(run_id: str) -> RunReconcileResult:
    """Run one deterministic DB reconciliation pass for a delivery run."""

    snapshot = build_run_reconcile_snapshot(run_id)
    if snapshot.control_intent == "cancel":
        return _apply_cancel_reconciliation(snapshot)
    if (
        snapshot.status == RunStatus.RUNNING
        and snapshot.sprint_count > 0
        and snapshot.empty_sprints == 0
        and snapshot.incomplete_sprints == 0
        and snapshot.blocked_sprints == 0
        and snapshot.open_delivery_items == 0
    ):
        return _apply_run_status_reconciliation(
            snapshot,
            status=RunStatus.COMPLETED,
            action="finalize_completed",
            reason="All DB sprints are complete.",
            clear_intent=False,
        )
    repo, _ = _repo_and_run(run_id)
    applied = repo.cas_update_run_reconcile_state(
        snapshot.db_run_id,
        expected_updated_at=snapshot.updated_at,
        reconcile_status="noop",
        reconcile_reason="No reconciliation action required.",
    )
    return RunReconcileResult(
        action="noop",
        applied=applied,
        reason="No reconciliation action required.",
        status=snapshot.status,
    )


def reconcile_stale_console_runs(
    repository: Any | None = None,
    *,
    stale_after_seconds: int = 300,
) -> list[RunReconcileResult]:
    """Stop and reconcile runs orphaned by a previous console process.

    A live console process refreshes its process row while it is executing. On a
    fresh web-console startup, only process rows older than the heartbeat window
    are treated as orphaned. Explicit ``stop_requested`` rows are reconciled
    immediately because they already represent an operator cancel intent.
    """

    from agentic_company.console.web.db import ConsoleRepository

    repo = repository or ConsoleRepository()
    if repository is None:
        repo.init_schema()
    cutoff = (datetime.now(UTC) - timedelta(seconds=stale_after_seconds)).isoformat(
        timespec="seconds"
    )
    stale_run_ids = repo.list_run_uids_with_console_process_status(
        process_name="codex_execution",
        process_statuses=("starting", "running"),
        exclude_run_statuses=tuple(str(status) for status in TERMINAL_RUN_STATUSES),
        updated_before=cutoff,
    )
    stopped_run_ids = repo.list_run_uids_with_console_process_status(
        process_name="codex_execution",
        process_statuses=("stop_requested",),
        exclude_run_statuses=tuple(str(status) for status in TERMINAL_RUN_STATUSES),
    )
    results: list[RunReconcileResult] = []
    for stale_run_id in [*stale_run_ids, *stopped_run_ids]:
        stale_run = repo.get_run_by_uid(stale_run_id)
        if stale_run is None:
            continue
        with repo.connect() as conn:
            row = conn.execute(
                "SELECT control_intent FROM runs WHERE id = ?",
                (stale_run.id,),
            ).fetchone()
        if str(row["control_intent"] if row else "") != "cancel":
            repo.set_run_control_intent(
                stale_run.id,
                intent="cancel",
                reason="Console restarted before the run completed.",
            )
        results.append(reconcile_run(stale_run_id))
    return results


def record_generated_app_url(run_id: str, generated_app_url: str) -> None:
    """Persist a run's generated app URL independent of its lifecycle status."""

    if not generated_app_url:
        return
    repo, db_run_id = _repo_and_run(run_id)
    repo.update_run_generated_app_url(db_run_id, generated_app_url)


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


def latest_delivery_state_snapshot(run_id: str) -> dict[str, Any] | None:
    """Load the latest graph state snapshot from canonical DB storage."""

    try:
        repo, db_run_id = _repo_and_run(run_id)
    except ValueError:
        return None
    return repo.latest_delivery_state_snapshot(db_run_id)


def record_activity_event(record: ActivityEventRecord) -> None:
    """Persist user-facing task activity without mutating work-item state."""

    record.validate()

    def operation() -> None:
        repo, db_run_id = _repo_and_run(record.run_id)
        now = _now()
        with repo.connect() as conn:
            _record_activity_event_conn(conn, db_run_id, record, now)

    _with_db_retry(operation)


def _record_activity_event_conn(
    conn: Any,
    db_run_id: int,
    record: ActivityEventRecord,
    now: str,
) -> None:
    row = conn.execute(
        "SELECT work_item_id FROM work_items WHERE run_id = ? AND work_item_id = ?",
        (db_run_id, record.work_item_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown work_item_id for run {record.run_id}: {record.work_item_id}")
    conn.execute(
        """
        INSERT INTO activity_events (
            run_id, event_id, work_item_id, owner_agent, agent_id, tool_name,
            message, status, artifact_ids, visibility, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'user', ?)
        ON CONFLICT(run_id, event_id) DO UPDATE SET
            work_item_id = excluded.work_item_id,
            owner_agent = excluded.owner_agent,
            agent_id = excluded.agent_id,
            tool_name = excluded.tool_name,
            message = excluded.message,
            status = excluded.status,
            artifact_ids = excluded.artifact_ids,
            visibility = excluded.visibility,
            created_at = excluded.created_at
        """,
        (
            db_run_id,
            record.event_id,
            record.work_item_id,
            record.owner_agent,
            record.agent_id,
            record.tool_name,
            record.message,
            record.status,
            json.dumps(list(record.artifact_ids), sort_keys=True),
            now,
        ),
    )


def record_artifact_link(
    run_dir: Path,
    request: ArtifactRegistrationRequest,
) -> ArtifactRecord:
    request.validate()
    artifact_path = resolve_run_artifact_path(run_dir, request.relative_path)
    if not artifact_path.is_file():
        raise ValueError(
            f"Artifact registration requires an existing run-local file: {request.relative_path}"
        )
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

    def operation() -> None:
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

    _with_db_retry(operation)
    _with_db_retry(lambda: _record_artifact_content(repo, db_run_id, run_dir, record))
    return record


def artifact_links_for_paths(run_id: str, paths: list[str]) -> tuple[Any, ...]:
    """Return DB-backed artifact links for explicit artifact IDs or run-local paths."""

    repo, db_run_id = _repo_and_run(run_id)
    run_row = _run_row(repo, run_id)
    run_dir = Path(str(run_row["run_dir"]))
    raw_refs = [str(path or "").strip() for path in paths if str(path or "").strip()]
    if not raw_refs:
        return ()
    records = repo.list_artifact_records(db_run_id)
    by_path = {record.relative_path: record for record in records}
    by_id = {record.artifact_id: record for record in records}
    resolved = []
    missing = []
    for ref in raw_refs:
        if ref in by_id:
            resolved.append(by_id[ref])
            continue
        path = _registered_artifact_lookup_path(run_dir, ref)
        record = by_path.get(path)
        if record is None:
            missing.append(path)
        else:
            resolved.append(record)
    if missing:
        raise ValueError(
            "Artifact refs must be registered in DB before use: " + ", ".join(sorted(missing))
        )
    return tuple(record.to_tool_ref() for record in resolved)


def _registered_artifact_lookup_path(run_dir: Path, path: str) -> str:
    raw = str(path or "").strip()
    raw_path = Path(raw)
    if raw_path.is_absolute():
        try:
            return raw_path.resolve().relative_to(run_dir.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError(f"Artifact ref must stay inside run directory: {raw}") from exc
    normalized = normalize_artifact_path(raw)
    missing_leading_root = Path(f"/{normalized}")
    if missing_leading_root.is_absolute():
        try:
            return missing_leading_root.resolve().relative_to(run_dir.resolve()).as_posix()
        except ValueError:
            pass
    return normalized


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


def stop_requested(run_id: str) -> bool:
    """Return whether the console process model has a durable stop flag."""

    try:
        repo, db_run_id = _repo_and_run(run_id)
    except ValueError:
        return False
    row = _run_row(repo, run_id)
    if "control_intent" in row.keys() and str(row["control_intent"] or "") == "cancel":
        return True
    state = repo.get_console_process_state(db_run_id, "codex_execution")
    return bool(state and state.stop_requested_at)


def run_stop_requested(run_id: str, run_dir: Path | str) -> bool:
    """Whether a user stop has been requested for a run.

    Checks the run-local stop file, the durable DB flag, and then the runtime
    cache so coordinators can halt between tool calls, not only between graph
    nodes. Cache failures are ignored because Postgres and the stop file are the
    source of truth.
    """

    if (Path(run_dir) / ".stop-requested").exists():
        return True
    try:
        if stop_requested(str(run_id)):
            return True
    except ValueError:
        return False
    except Exception as exc:
        LOGGER.warning("Durable stop flag read failed run_id=%s error=%s", run_id, exc)

    try:
        from agentic_company.platform.runtime_cache import redis_error_types, runtime_cache_from_env

        return runtime_cache_from_env().stop_requested(str(run_id))
    except redis_error_types() as exc:
        LOGGER.warning("Redis runtime stop flag read failed run_id=%s error=%s", run_id, exc)
        return False


def _repo_and_run(run_id: str):
    from agentic_company.console.web.db import ConsoleRepository

    repo = ConsoleRepository()
    repo.init_schema()
    row = _run_row(repo, run_id)
    return repo, int(row["id"])


def _record_work_item_transition_conn(
    conn: Any,
    db_run_id: int,
    record: ToolExecutionRecord,
    now: str,
) -> None:
    event_id = f"work:{record.tool_call_id}:{record.attempt_id}:{uuid4().hex}"
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
        raw_requested_status=record.status,
        requested_status=requested_status,
        owner_agent=record.owner_agent,
        tool_name=record.tool_name,
    )
    lane = _lane_for_status(effective_status)
    effective_owner = (
        from_owner if from_status == "done" and effective_status == "done" else record.owner_agent
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


def _update_sprint_status(run_id: str, sprint_id: str, status: str) -> None:
    def operation() -> None:
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

    _with_db_retry(operation)


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
    if suffix in {
        ".css",
        ".csv",
        ".dockerignore",
        ".env",
        ".example",
        ".html",
        ".js",
        ".jsx",
        ".log",
        ".md",
        ".mjs",
        ".mmd",
        ".py",
        ".sh",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }:
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


def _with_db_retry(operation: Any) -> Any:
    from agentic_company.console.web.sql_backend import retry_database_operation

    return retry_database_operation(operation)


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
    return classify_work_item_status(status).value


def _lane_for_status(status: str) -> str:
    normalized = _normalize_status(status)
    return "qa" if normalized == "review" else normalized


# The per-sprint handoff only produces a report, so the coordination card stays
# IN PROGRESS until the head completes delivery rather than going terminal on
# every sprint (it never sits in review — review is for feature/QA/deploy items).
_HANDOFF_TOOLS = {"run_handoff"}
_PLANNING_TOOLS = {"run_business_analyst", "run_architect", "run_project_manager"}


def _transition_allowed(current: str, target: str) -> bool:
    """A done card is terminal: reject any move back out of done.

    Forward moves (including QA's in_progress -> done) are allowed; only the
    terminal regression that reopened coordination cards mid-run is blocked.
    """

    return not (current == "done" and target != "done")


def _effective_transition_status(
    *,
    current_status: str,
    raw_requested_status: str,
    requested_status: str,
    owner_agent: str,
    tool_name: str,
) -> str:
    """Apply tool semantics that cannot be inferred from status text alone."""

    current = _normalize_status(current_status)
    requested = _normalize_status(requested_status)
    if current == "done" and tool_name == "inspect_sprint_status":
        return current
    raw = raw_requested_status.strip().lower()
    if (
        "deployed" in raw
        and requested == "done"
        and owner_agent == "deployment-agent"
        and tool_name in {"codex_exec", "run_deployment"}
    ):
        return "review"
    if requested == "done" and tool_name in _HANDOFF_TOOLS:
        requested = "in_progress"  # coordination card stays in_progress, never review
    if current == "in_progress" and requested == "done" and tool_name in _PLANNING_TOOLS:
        return requested
    if (
        current == "in_progress"
        and requested == "done"
        and owner_agent == "qa-agent"
        and tool_name in {"run_qa", "run_post_deploy_qa"}
    ):
        return requested
    if not _transition_allowed(current, requested):
        return current
    try:
        transition(WorkItemStatus(current), WorkItemStatus(requested))
    except (InvalidStatusTransition, ValueError):
        return current
    return requested


def _apply_cancel_reconciliation(snapshot: RunReconcileSnapshot) -> RunReconcileResult:
    repo, _ = _repo_and_run(snapshot.run_id)
    now = _now()
    reason = snapshot.control_intent_reason or "Stopped by user."
    with repo.connect() as conn:
        current = conn.execute(
            "SELECT status, updated_at FROM runs WHERE id = ? FOR UPDATE",
            (snapshot.db_run_id,),
        ).fetchone()
        if current is None or str(current["updated_at"]) != snapshot.updated_at:
            return RunReconcileResult(
                action="cancel",
                applied=False,
                reason="stale_snapshot",
                status=snapshot.status,
            )
        current_status = str(current["status"] or "")
        if current_status in TERMINAL_RUN_STATUSES:
            cursor = conn.execute(
                """
                UPDATE runs
                SET control_intent = '',
                    control_intent_reason = '',
                    control_intent_requested_at = '',
                    reconcile_status = 'ignored_terminal',
                    reconcile_reason = ?,
                    reconciled_at = ?,
                    updated_at = ?
                WHERE id = ? AND updated_at = ? AND status = ?
                """,
                (
                    f"Cancel ignored because run is already terminal: {current_status}.",
                    now,
                    now,
                    snapshot.db_run_id,
                    snapshot.updated_at,
                    current_status,
                ),
            )
            return RunReconcileResult(
                action="cancel_ignored_terminal",
                applied=cursor.rowcount > 0,
                reason=(
                    f"Cancel ignored because run is already terminal: {current_status}."
                    if cursor.rowcount > 0
                    else "stale_snapshot"
                ),
                status=current_status,
            )
        rows = conn.execute(
            """
            SELECT work_item_id, status, owner_agent
            FROM work_items
            WHERE run_id = ?
              AND status IN ('in_progress', 'review')
            ORDER BY sprint_id, delivery_order, work_item_id
            """,
            (snapshot.db_run_id,),
        ).fetchall()
        for row in rows:
            work_item_id = str(row["work_item_id"])
            from_status = str(row["status"])
            owner = str(row["owner_agent"] or "delivery-graph")
            event_id = f"reconcile:{snapshot.run_id}:cancel:{work_item_id}:{uuid4().hex}"
            conn.execute(
                """
                UPDATE work_items
                SET status = 'blocked',
                    lane = 'blocked',
                    active = 0,
                    blocker = ?,
                    updated_at = ?
                WHERE run_id = ? AND work_item_id = ?
                """,
                (reason, now, snapshot.db_run_id, work_item_id),
            )
            conn.execute(
                """
                INSERT INTO work_item_events (
                    run_id, event_id, work_item_id, event_type, from_status,
                    to_status, from_owner, to_owner, agent_id, tool_name,
                    tool_call_id, message, visibility, created_at
                )
                VALUES (?, ?, ?, 'reconcile_cancel', ?, 'blocked', ?, ?, ?, ?,
                        ?, ?, 'user', ?)
                """,
                (
                    snapshot.db_run_id,
                    event_id,
                    work_item_id,
                    from_status,
                    owner,
                    owner,
                    "runtime-reconciler",
                    "reconcile_run",
                    event_id,
                    reason,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO activity_events (
                    run_id, event_id, work_item_id, owner_agent, agent_id,
                    tool_name, message, status, artifact_ids, visibility,
                    created_at
                )
                VALUES (?, ?, ?, ?, 'runtime-reconciler', 'reconcile_run',
                        ?, 'blocked', '[]', 'user', ?)
                """,
                (snapshot.db_run_id, event_id, work_item_id, owner, reason, now),
            )
        conn.execute(
            """
            UPDATE work_items
            SET active = 0, updated_at = ?
            WHERE run_id = ? AND active = 1
            """,
            (now, snapshot.db_run_id),
        )
        conn.execute(
            """
            UPDATE sprints
            SET status = 'blocked', updated_at = ?
            WHERE run_id = ? AND status NOT IN ('done', 'blocked')
            """,
            (now, snapshot.db_run_id),
        )
        cursor = conn.execute(
            """
            UPDATE runs
            SET status = ?,
                control_intent = '',
                control_intent_reason = '',
                control_intent_requested_at = '',
                reconcile_status = 'applied',
                reconcile_reason = ?,
                reconciled_at = ?,
                updated_at = ?
            WHERE id = ? AND updated_at = ?
              AND status NOT IN ('completed', 'blocked', 'failed', 'failed_to_start', 'stopped')
            """,
            (RunStatus.STOPPED.value, reason, now, now, snapshot.db_run_id, snapshot.updated_at),
        )
    return RunReconcileResult(
        action="cancel",
        applied=cursor.rowcount > 0,
        reason=reason if cursor.rowcount > 0 else "stale_snapshot",
        status=RunStatus.STOPPED.value if cursor.rowcount > 0 else snapshot.status,
    )


def _apply_run_status_reconciliation(
    snapshot: RunReconcileSnapshot,
    *,
    status: RunStatus,
    action: str,
    reason: str,
    clear_intent: bool,
) -> RunReconcileResult:
    repo, _ = _repo_and_run(snapshot.run_id)
    applied = repo.cas_update_run_reconcile_state(
        snapshot.db_run_id,
        expected_updated_at=snapshot.updated_at,
        status=status.value,
        control_intent="" if clear_intent else None,
        reconcile_status="applied",
        reconcile_reason=reason,
    )
    return RunReconcileResult(
        action=action,
        applied=applied,
        reason=reason if applied else "stale_snapshot",
        status=status.value if applied else snapshot.status,
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
