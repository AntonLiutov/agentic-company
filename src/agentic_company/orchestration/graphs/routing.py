"""Routing constants for the first company delivery graph."""

from __future__ import annotations

DELIVERY_GRAPH_NODE_ORDER = [
    "planning",
    "fullstack",
    "qa",
    "deployment",
    "handoff",
]

CONSOLE_EXECUTION_NODE_ORDER = [
    "fullstack",
    "qa",
]

CONSOLE_DEPLOYMENT_NODE_ORDER = [
    "deployment",
    "handoff",
]
