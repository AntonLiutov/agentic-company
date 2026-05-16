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
from agentic_company.platform.artifacts import (
    EXECUTION_REQUEST_ARTIFACT,
    load_execution_request,
)
from agentic_company.platform.events import write_event
from agentic_company.platform.state import (
    DeliveryState,
    initial_delivery_state,
    write_delivery_state,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_STATE_FILENAME = ".delivery-state.json"
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
        event_log = run_dir / "events.jsonl"
        node_order = list(self.node_order or DELIVERY_GRAPH_NODE_ORDER)
        state = self.load_state(run_dir)
        if state is None:
            state = initial_delivery_state(
                run_id=run_id or run_dir.name,
                run_dir=run_dir,
                requirements_path=requirements_path,
                target_project_dir=target_project_dir,
                max_repair_attempts=max_repair_attempts,
            )
            state = self._hydrate_existing_run_context(run_dir, state)
            self.save_state(run_dir, state)
            self._write_state_event(event_log, state)

        LOGGER.info(
            "Delivery graph starting run_id=%s stage=%s status=%s",
            state["run_id"],
            state["stage"],
            state["status"],
        )
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
            write_event(
                event_log,
                state["run_id"],
                GRAPH_AGENT_ID,
                "delivery_graph_failed",
                {
                    "node_order": node_order,
                    "stage": state["stage"],
                    "status": "failed",
                    "error": str(exc),
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
            },
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
            try:
                updated = node(state)
            except Exception as exc:
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
                    },
                )
                raise
            write_event(
                event_log,
                run_id,
                GRAPH_AGENT_ID,
                "delivery_graph_node_completed",
                {
                    "node": node_name,
                    "stage": updated["stage"],
                    "status": updated["status"],
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

    def _hydrate_existing_run_context(
        self,
        run_dir: Path,
        state: DeliveryState,
    ) -> DeliveryState:
        request_path = run_dir / EXECUTION_REQUEST_ARTIFACT
        if not request_path.exists():
            return state

        request = load_execution_request(run_dir)
        updated: DeliveryState = {**state}
        updated["target_project_dir"] = request.target_project_dir
        updated["feature_queue"] = request.feature_queue
        updated["completed_feature_ids"] = request.completed_feature_ids
        updated["feature_statuses"] = {
            feature_id: "qa_passed" for feature_id in request.completed_feature_ids
        }
        updated["feature_repair_attempts"] = {}
        active_feature = request.active_feature
        updated["active_feature_id"] = (
            str(active_feature["id"]) if active_feature and active_feature.get("id") else None
        )
        return updated
