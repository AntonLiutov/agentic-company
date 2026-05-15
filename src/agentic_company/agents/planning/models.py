"""Planning Agent data contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class IntakeBrief:
    project_name: str
    source_path: str
    goal: str
    target_user: str
    core_features: list[str]
    required_configuration: list[str]
    preferred_stack: list[str]
    non_goals: list[str]
    acceptance_criteria: list[str]
    open_questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class ProjectClassification:
    project_type: str
    complexity: str
    delivery_mode: str
    rationale: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class StaffingDecision:
    project_type: str
    complexity: str
    delivery_mode: str
    selected_agents: list[str]
    optional_agents: list[str]
    rationale: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class WorkflowPhase:
    name: str
    owner: str
    outputs: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class FeatureWorkItem:
    id: str
    title: str
    user_value: str
    acceptance_criteria: list[str]
    dependencies: list[str]
    suggested_owner_agent: str
    delivery_order: int
    test_notes: list[str]
    deployment_notes: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class WorkflowPlan:
    workflow_id: str
    project_name: str
    phases: list[WorkflowPhase]
    project_archetype: str = "single-service-streamlit"
    feature_queue: list[FeatureWorkItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "workflow_id": self.workflow_id,
            "project_name": self.project_name,
            "project_archetype": self.project_archetype,
            "phases": [phase.to_dict() for phase in self.phases],
            "feature_queue": [feature.to_dict() for feature in self.feature_queue],
        }
