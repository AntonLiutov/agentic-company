from agentic_company.platform.agent.runtime_modes import (
    RiskMode,
    RunMode,
    mode_policy,
    start_gate_required,
)


def test_runtime_mode_policy_maps_current_console_modes():
    simple = mode_policy("simple_prototype")
    medium = mode_policy("internal_tool")
    complex_policy = mode_policy("full_product")

    assert simple.run_mode is RunMode.SIMPLE
    assert simple.requires_planning is False
    assert "fullstack-agent" in simple.required_agents
    assert medium.run_mode is RunMode.MEDIUM
    assert medium.requires_planning is True
    assert complex_policy.run_mode is RunMode.COMPLEX
    assert complex_policy.requires_architecture is True
    assert complex_policy.requires_deployment is True


def test_enterprise_mode_defaults_to_safe_approval_gates():
    policy = mode_policy("enterprise")

    assert policy.run_mode is RunMode.ENTERPRISE
    assert policy.requires_approval_gates is True
    assert policy.default_risk_mode is RiskMode.SAFE


def test_start_gate_required_is_driven_by_risk_then_mode():
    # Risk mode is the primary, load-bearing control.
    assert start_gate_required("simple_prototype", "autonomous") is False  # never gates
    assert start_gate_required("enterprise", "autonomous") is False  # autonomy wins
    assert start_gate_required("simple_prototype", "safe") is True  # safe always gates
    assert start_gate_required("full_product", "safe") is True
    # assisted defers to the run-mode policy: only enterprise requires a gate.
    assert start_gate_required("enterprise", "assisted") is True
    assert start_gate_required("simple_prototype", "assisted") is False
    assert start_gate_required("internal_tool", "assisted") is False
    # unknown values fail open (no gate), never wedge a run.
    assert start_gate_required("nonsense", "weird") is False
