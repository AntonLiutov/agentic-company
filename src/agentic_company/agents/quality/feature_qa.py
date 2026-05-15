"""Compatibility entry point for feature-scoped QA execution."""

from __future__ import annotations

from agentic_company.agents.quality.graph import (
    FeatureQaRunner,
    run_quality_agent_graph,
)
from agentic_company.platform.state import DeliveryState


def run_feature_quality_agent(
    state: DeliveryState,
    *,
    runner: FeatureQaRunner | None = None,
) -> DeliveryState:
    """Run feature-scoped QA through the QA Agent LangGraph."""

    return run_quality_agent_graph(state, runner=runner)
