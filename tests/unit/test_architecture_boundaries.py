from agentic_company.agents.fullstack import CodexCliRunner, FullstackAgent
from agentic_company.agents.handoff import HandoffAgent
from agentic_company.orchestration import WorkflowStage, ordered_stages


def test_agent_boundary_imports_expose_current_runners():
    assert CodexCliRunner.__name__ == "CodexCliRunner"
    assert FullstackAgent.__name__ == "FullstackAgent"
    assert HandoffAgent.__name__ == "HandoffAgent"


def test_workflow_stages_include_agent_delivery_loop():
    assert ordered_stages() == [
        WorkflowStage.PLANNING,
        WorkflowStage.CREDENTIALS,
        WorkflowStage.FULLSTACK,
        WorkflowStage.QA,
        WorkflowStage.DEPLOYMENT,
        WorkflowStage.HANDOFF,
    ]
