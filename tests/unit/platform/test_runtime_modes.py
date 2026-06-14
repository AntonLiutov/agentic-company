from agentic_company.platform.runtime_modes import RiskMode, RunMode, mode_policy


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
