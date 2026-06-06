import inspect

from agentic_company.agents.head.contracts import HEAD_TOOL_CONTRACT_REGISTRY
from agentic_company.agents.head.executor import langchain_tools as head_langchain_tools
from agentic_company.agents.team_lead.contracts import TEAM_LEAD_TOOL_CONTRACT_REGISTRY
from agentic_company.agents.team_lead.executor import langchain_tools as team_lead_langchain_tools


def test_head_tool_contract_parameters_match_callable_signatures():
    tools = {tool.__name__: tool for tool in head_langchain_tools(_NullToolbox())}

    for contract in HEAD_TOOL_CONTRACT_REGISTRY.all():
        signature_params = set(inspect.signature(tools[contract.tool_name]).parameters)
        expected = set(contract.required_parameters) | set(contract.optional_parameters)
        assert expected <= signature_params, contract.tool_name


def test_team_lead_tool_contract_parameters_match_callable_signatures():
    tools = {tool.__name__: tool for tool in team_lead_langchain_tools(_NullToolbox())}

    for contract in TEAM_LEAD_TOOL_CONTRACT_REGISTRY.all():
        signature_params = set(inspect.signature(tools[contract.tool_name]).parameters)
        expected = set(contract.required_parameters) | set(contract.optional_parameters)
        assert expected <= signature_params, contract.tool_name


class _NullToolbox:
    def __getattr__(self, name):
        def _tool(*args, **kwargs):
            return "{}"

        return _tool
