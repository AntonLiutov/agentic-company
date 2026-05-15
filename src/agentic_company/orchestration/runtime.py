"""Runtime boundary for executing and persisting delivery graph state."""

from __future__ import annotations

import json
import logging
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
from agentic_company.platform.state import DeliveryState, initial_delivery_state

LOGGER = logging.getLogger(__name__)
DEFAULT_STATE_FILENAME = ".delivery-state.json"
GRAPH_AGENT_ID = "delivery-graph"


@dataclass(slots=True)
class DeliveryGraphRuntime:
    """Start the delivery graph and persist its state for consoles and future resumes."""

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
        max_repair_attempts: int = 3,
    ) -> DeliveryState:
        """Load or create graph state, invoke the graph, and persist the final state."""

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
                nodes=self._evented_nodes(event_log, state["run_id"]),
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
        return cast(DeliveryState, json.loads(state_path.read_text(encoding="utf-8")))

    def save_state(self, run_dir: Path, state: DeliveryState) -> Path:
        """Persist delivery state atomically inside the run directory."""

        state_path = self.state_path(run_dir)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(state_path)
        return state_path

    def state_path(self, run_dir: Path) -> Path:
        """Return the runtime state artifact path for a run."""

        return run_dir / self.state_filename

    def _evented_nodes(self, event_log: Path, run_id: str) -> DeliveryGraphNodes:
        nodes = self.nodes or DeliveryGraphNodes()
        return DeliveryGraphNodes(
            planning=self._evented_node(event_log, run_id, "planning", nodes.planning),
            fullstack=self._evented_node(
                event_log,
                run_id,
                "fullstack",
                nodes.fullstack,
            ),
            qa=self._evented_node(event_log, run_id, "qa", nodes.qa),
            deployment=self._evented_node(event_log, run_id, "deployment", nodes.deployment),
            handoff=self._evented_node(event_log, run_id, "handoff", nodes.handoff),
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
