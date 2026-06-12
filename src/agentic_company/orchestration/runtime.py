"""Platform graph runner for executing and persisting delivery state."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from agentic_company.orchestration.graphs import (
    DELIVERY_GRAPH_NODE_ORDER,
    DeliveryGraphNodes,
    run_delivery_graph,
)
from agentic_company.platform.events import write_event
from agentic_company.platform.run_finalizer import RunStatus, resolve_run_status
from agentic_company.platform.runtime_db import (
    latest_delivery_state_snapshot,
    record_run_lifecycle,
    run_stop_requested,
    run_target_project_dir,
)
from agentic_company.platform.state import (
    DELIVERY_STATE_SNAPSHOT,
    DeliveryState,
    initial_delivery_state,
    write_delivery_state,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_STATE_FILENAME = DELIVERY_STATE_SNAPSHOT.as_posix()
GRAPH_AGENT_ID = "delivery-graph"


@dataclass(slots=True)
class DeliveryGraphRuntime:
    """Start a configured platform graph and persist state for consoles and future resumes."""

    state_filename: str = DEFAULT_STATE_FILENAME
    nodes: DeliveryGraphNodes | None = None
    node_order: Sequence[str] | None = None

    def start(
        self,
        run_dir: Path,
        *,
        run_id: str | None = None,
        requirements_path: Path | None = None,
        target_project_dir: Path | None = None,
        max_repair_attempts: int = 5,
    ) -> DeliveryState:
        """Load or create graph state, invoke configured nodes, and persist final state."""

        run_dir.mkdir(parents=True, exist_ok=True)
        event_log = run_dir
        node_order = list(self.node_order or DELIVERY_GRAPH_NODE_ORDER)
        state = self.load_state(run_dir)
        if state is None:
            runtime_run_id = run_id or run_dir.name
            resolved_target_project_dir = target_project_dir or Path(
                run_target_project_dir(runtime_run_id)
            )
            state = initial_delivery_state(
                run_id=runtime_run_id,
                run_dir=run_dir,
                requirements_path=requirements_path,
                target_project_dir=resolved_target_project_dir,
                max_repair_attempts=max_repair_attempts,
            )
            record_run_lifecycle(
                str(state["run_id"]),
                RunStatus.RUNNING,
                target_project_dir=str(resolved_target_project_dir),
            )
            self.save_state(run_dir, state)
            self._write_state_event(event_log, state)

        LOGGER.info(
            "Delivery graph starting run_id=%s stage=%s status=%s",
            state["run_id"],
            state["stage"],
            state["status"],
        )
        graph_started = time.perf_counter()
        write_event(
            event_log,
            state["run_id"],
            GRAPH_AGENT_ID,
            "delivery_graph_started",
            {
                "node_order": node_order,
                "stage": state["stage"],
                "status": state["status"],
                "state_artifact": self.state_filename,
            },
        )
        try:
            final_state = run_delivery_graph(
                state,
                nodes=self._evented_nodes(event_log, state["run_id"], node_order),
                node_order=node_order,
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - graph_started) * 1000)
            record_run_lifecycle(state["run_id"], RunStatus.FAILED)
            write_event(
                event_log,
                state["run_id"],
                GRAPH_AGENT_ID,
                "delivery_graph_failed",
                {
                    "node_order": node_order,
                    "stage": state["stage"],
                    "status": RunStatus.FAILED,
                    "error": str(exc),
                    "duration_ms": duration_ms,
                },
            )
            raise
        self.save_state(run_dir, final_state)
        self._write_state_event(event_log, final_state)
        write_event(
            event_log,
            final_state["run_id"],
            GRAPH_AGENT_ID,
            "delivery_graph_completed",
            {
                "node_order": node_order,
                "stage": final_state["stage"],
                "status": final_state["status"],
                "state_artifact": self.state_filename,
                "duration_ms": int((time.perf_counter() - graph_started) * 1000),
            },
        )
        record_run_lifecycle(
            final_state["run_id"],
            resolve_run_status(final_state),
            generated_app_url=str(final_state.get("public_url") or ""),
            target_project_dir=str(final_state.get("target_project_dir") or ""),
        )
        LOGGER.info(
            "Delivery graph completed run_id=%s stage=%s status=%s",
            final_state["run_id"],
            final_state["stage"],
            final_state["status"],
        )
        return final_state

    def load_state(self, run_dir: Path) -> DeliveryState | None:
        """Read the persisted delivery state if one exists."""

        db_state = latest_delivery_state_snapshot(run_dir.name)
        if db_state is not None:
            return cast(DeliveryState, db_state)
        state_path = self.state_path(run_dir)
        if not state_path.exists():
            return None
        for attempt in range(3):
            try:
                raw_state = state_path.read_text(encoding="utf-8")
                return cast(DeliveryState, json.loads(raw_state))
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.05 * (attempt + 1))
        return None

    def save_state(self, run_dir: Path, state: DeliveryState) -> Path:
        """Persist delivery state atomically inside the run directory."""

        return write_delivery_state(state, self.state_path(run_dir))

    def state_path(self, run_dir: Path) -> Path:
        """Return the runtime state artifact path for a run."""

        return run_dir / self.state_filename

    def _evented_nodes(
        self,
        event_log: Path,
        run_id: str,
        node_order: Sequence[str],
    ) -> DeliveryGraphNodes:
        nodes = self.nodes or DeliveryGraphNodes()
        active_nodes = set(node_order)

        def evented(
            node_name: str,
            node: Callable[[DeliveryState], DeliveryState] | None,
        ) -> Callable[[DeliveryState], DeliveryState] | None:
            if node is None and node_name not in active_nodes:
                return None
            return self._evented_node(event_log, run_id, node_name, node)

        return DeliveryGraphNodes(
            head=evented("head", nodes.head),
            business_analyst=evented("business_analyst", nodes.business_analyst),
            architecture=evented("architecture", nodes.architecture),
            project_management=evented("project_management", nodes.project_management),
            team_lead=evented("team_lead", nodes.team_lead),
            fullstack=evented("fullstack", nodes.fullstack),
            qa=evented("qa", nodes.qa),
            deployment=evented("deployment", nodes.deployment),
            handoff=evented("handoff", nodes.handoff),
        )

    def _evented_node(
        self,
        event_log: Path,
        run_id: str,
        node_name: str,
        node: Callable[[DeliveryState], DeliveryState] | None,
    ) -> Callable[[DeliveryState], DeliveryState]:
        if node is None:
            raise ValueError(f"Delivery graph node is not configured: {node_name}")

        def run(state: DeliveryState) -> DeliveryState:
            should_stop = run_stop_requested(run_id, state["run_dir"])
            if should_stop:
                stopped: DeliveryState = {**state}
                stopped["status"] = "stopped"
                stopped["blockers"] = [*stopped.get("blockers", []), "Stopped by user"]
                write_event(
                    event_log,
                    run_id,
                    GRAPH_AGENT_ID,
                    "delivery_graph_node_skipped",
                    {
                        "node": node_name,
                        "stage": stopped["stage"],
                        "status": stopped["status"],
                        "reason": "user_requested_stop",
                    },
                )
                self.save_state(Path(stopped["run_dir"]), stopped)
                self._write_state_event(event_log, stopped)
                return stopped

            blockers = state.get("blockers", [])
            if blockers:
                write_event(
                    event_log,
                    run_id,
                    GRAPH_AGENT_ID,
                    "delivery_graph_node_skipped",
                    {
                        "node": node_name,
                        "stage": state["stage"],
                        "status": state["status"],
                        "reason": "state_has_blockers",
                        "blockers": blockers,
                    },
                )
                return state

            write_event(
                event_log,
                run_id,
                GRAPH_AGENT_ID,
                "delivery_graph_node_started",
                {
                    "node": node_name,
                    "stage": state["stage"],
                    "status": state["status"],
                },
            )
            node_started = time.perf_counter()
            try:
                updated = node(state)
            except Exception as exc:
                duration_ms = int((time.perf_counter() - node_started) * 1000)
                write_event(
                    event_log,
                    run_id,
                    GRAPH_AGENT_ID,
                    "delivery_graph_node_failed",
                    {
                        "node": node_name,
                        "stage": state["stage"],
                        "status": "failed",
                        "error": str(exc),
                        "duration_ms": duration_ms,
                    },
                )
                raise
            duration_ms = int((time.perf_counter() - node_started) * 1000)
            write_event(
                event_log,
                run_id,
                GRAPH_AGENT_ID,
                "delivery_graph_node_completed",
                {
                    "node": node_name,
                    "stage": updated["stage"],
                    "status": updated["status"],
                    "duration_ms": duration_ms,
                },
            )
            self.save_state(Path(updated["run_dir"]), updated)
            self._write_state_event(event_log, updated)
            return updated

        return run

    def _write_state_event(self, event_log: Path, state: DeliveryState) -> None:
        write_event(
            event_log,
            state["run_id"],
            GRAPH_AGENT_ID,
            "delivery_graph_state_written",
            {
                "stage": state["stage"],
                "status": state["status"],
                "state_artifact": self.state_filename,
            },
        )
