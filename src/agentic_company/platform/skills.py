"""Composable runtime skills for ADL AgentExecutors.

Skills are loaded as Codex-style filesystem packages:

```
skill-id/
  SKILL.md
  agents/adl.yaml
```

`SKILL.md` stays portable and model-facing. `agents/adl.yaml` carries the ADL
runtime contract used for tool validation, artifact expectations, dashboard
mapping, governance, trace, and future external board integrations.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from agentic_company.platform.state import DeliveryState

KNOWN_ARTIFACT_TYPES = {
    "requirements_brief",
    "architecture_report",
    "delivery_plan",
    "execution_summary",
    "qa_report",
    "repair_request",
    "deployment_summary",
    "release_report",
    "screenshot_evidence",
    "codex_log",
    "tool_request",
    "tool_result",
    "debug_trace",
}
KNOWN_VISIBILITIES = {"business", "release", "qa_evidence", "developer", "internal"}
KNOWN_SKILL_STATUSES = {"active", "experimental", "disabled", "deprecated"}
KNOWN_TRUST_LEVELS = {"system", "project", "user", "generated"}
KNOWN_DASHBOARD_STATUSES = {"todo", "in_progress", "review", "done", "blocked"}
KNOWN_RISK_LEVELS = {"low", "medium", "high"}
KNOWN_EXTERNAL_SYSTEMS = {"internal", "github", "jira", "azure_devops", "linear"}


@dataclass(frozen=True, slots=True)
class SkillExpectedArtifact:
    """Artifact contract a selected skill should produce or consume."""

    artifact_type: str
    visibility: str
    required: bool = True
    dashboard_label: str = ""
    external_reference_type: str = "work_item"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SkillSelectionRules:
    """Rules used to select a skill for an agent/stage."""

    applies_to_agents: tuple[str, ...]
    default_for_agents: tuple[str, ...]
    stages: tuple[str, ...]
    trigger_keywords: tuple[str, ...]
    negative_triggers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillRuntimeContract:
    """Runtime contract connecting a skill to tools and artifacts."""

    required_tools: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    consumes_artifacts: tuple[str, ...]
    produces_artifacts: tuple[SkillExpectedArtifact, ...]


@dataclass(frozen=True, slots=True)
class SkillDashboardContract:
    """Dashboard and future external-board mapping for a skill."""

    status: str
    work_item_stage: str
    external_systems_supported: tuple[str, ...]
    comment_template: str = ""
    labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillGovernance:
    """Governance contract for risk, checkpoints, and approvals."""

    risk_level: str
    requires_human_approval: bool
    max_repair_attempts: int
    checkpoint_before: bool
    checkpoint_after: bool


@dataclass(frozen=True, slots=True)
class SkillValidation:
    """Lightweight validation/eval metadata for a skill package."""

    eval_prompts: tuple[str, ...]
    success_signals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SkillPackage:
    """Full loaded skill package: portable skill plus ADL sidecar contract."""

    skill_id: str
    name: str
    description: str
    body: str
    version: str
    status: str
    trust_level: str
    selection: SkillSelectionRules
    runtime_contract: SkillRuntimeContract
    dashboard: SkillDashboardContract
    governance: SkillGovernance
    validation: SkillValidation
    examples: tuple[dict[str, Any], ...]
    source_path: str
    contract_path: str
    contract_hash: str


@dataclass(frozen=True, slots=True)
class SkillDescriptor:
    """Static skill definition used by agent selection and prompt rendering."""

    skill_id: str
    name: str
    version: str
    description: str
    trigger: str
    applies_to_agents: tuple[str, ...]
    default_for_agents: tuple[str, ...]
    instructions: tuple[str, ...]
    required_tools: tuple[str, ...]
    expected_artifacts: tuple[SkillExpectedArtifact, ...]
    dashboard_status: str
    work_item_stage: str
    external_systems_supported: tuple[str, ...]
    examples: tuple[dict[str, Any], ...]
    body: str
    source_path: str
    contract_path: str
    contract_hash: str
    status: str = "active"
    trust_level: str = "system"
    governance: SkillGovernance | None = None
    validation: SkillValidation | None = None

    @classmethod
    def from_package(cls, package: SkillPackage) -> SkillDescriptor:
        """Build the runtime descriptor used by current AgentExecutor code."""

        return cls(
            skill_id=package.skill_id,
            name=package.name,
            version=package.version,
            description=package.description,
            trigger=", ".join(package.selection.trigger_keywords),
            applies_to_agents=package.selection.applies_to_agents,
            default_for_agents=package.selection.default_for_agents,
            instructions=_instruction_summary(package.body),
            required_tools=package.runtime_contract.required_tools,
            expected_artifacts=package.runtime_contract.produces_artifacts,
            dashboard_status=package.dashboard.status,
            work_item_stage=package.dashboard.work_item_stage,
            external_systems_supported=package.dashboard.external_systems_supported,
            examples=package.examples,
            body=package.body,
            source_path=package.source_path,
            contract_path=package.contract_path,
            contract_hash=package.contract_hash,
            status=package.status,
            trust_level=package.trust_level,
            governance=package.governance,
            validation=package.validation,
        )

    def to_metadata(self) -> dict[str, Any]:
        """Return compact metadata suitable for trace and dashboards."""

        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "applies_to_agents": list(self.applies_to_agents),
            "default_for_agents": list(self.default_for_agents),
            "required_tools": list(self.required_tools),
            "dashboard_status": self.dashboard_status,
            "work_item_stage": self.work_item_stage,
            "source_path": self.source_path,
            "contract_hash": self.contract_hash,
            "expected_artifacts": [artifact.to_dict() for artifact in self.expected_artifacts],
        }


@dataclass(frozen=True, slots=True)
class SkillSelection:
    """One selected skill and the reason it was selected."""

    skill_id: str
    version: str
    reason: str = ""
    work_item_stage: str = ""
    dashboard_status: str = ""
    source_path: str = ""
    contract_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SkillSelectionResult:
    """Selected descriptors plus compact selection metadata."""

    agent_id: str
    stage: str
    selections: tuple[SkillSelection, ...]
    descriptors: tuple[SkillDescriptor, ...]

    def to_trace_data(self) -> list[dict[str, Any]]:
        return [
            {
                **selection.to_dict(),
                "name": descriptor.name,
                "required_tools": list(descriptor.required_tools),
                "expected_artifacts": [
                    artifact.to_dict() for artifact in descriptor.expected_artifacts
                ],
            }
            for selection, descriptor in zip(self.selections, self.descriptors, strict=True)
        ]


class SkillCatalog:
    """Immutable catalog of runtime skill descriptors."""

    def __init__(self, skills: tuple[SkillDescriptor, ...]) -> None:
        self._skills = {skill.skill_id: skill for skill in skills if skill.status == "active"}
        if len(self._skills) != len([skill for skill in skills if skill.status == "active"]):
            raise ValueError("Active skill ids must be unique.")
        self.validate()

    def get(self, skill_id: str) -> SkillDescriptor:
        return self._skills[skill_id]

    def maybe_get(self, skill_id: str) -> SkillDescriptor | None:
        return self._skills.get(skill_id)

    def all(self) -> tuple[SkillDescriptor, ...]:
        return tuple(self._skills.values())

    def ids(self) -> tuple[str, ...]:
        return tuple(self._skills)

    def validate(self) -> None:
        for skill in self._skills.values():
            if not skill.version:
                raise ValueError(f"{skill.skill_id} is missing version.")
            if not skill.body.strip():
                raise ValueError(f"{skill.skill_id} is missing SKILL.md body.")
            if not skill.applies_to_agents:
                raise ValueError(f"{skill.skill_id} is missing applies_to_agents.")
            if not skill.required_tools:
                raise ValueError(f"{skill.skill_id} is missing required_tools.")
            if not skill.expected_artifacts:
                raise ValueError(f"{skill.skill_id} is missing expected_artifacts.")
            if not skill.examples:
                raise ValueError(f"{skill.skill_id} is missing examples.")
            if skill.dashboard_status not in KNOWN_DASHBOARD_STATUSES:
                raise ValueError(
                    f"{skill.skill_id} has unknown dashboard status {skill.dashboard_status}."
                )
            if not {"internal", "github", "jira", "azure_devops"}.issubset(
                set(skill.external_systems_supported)
            ):
                raise ValueError(f"{skill.skill_id} is missing future board support.")
            for system in skill.external_systems_supported:
                if system not in KNOWN_EXTERNAL_SYSTEMS:
                    raise ValueError(f"{skill.skill_id} has unknown external system {system}.")
            for artifact in skill.expected_artifacts:
                if artifact.artifact_type not in KNOWN_ARTIFACT_TYPES:
                    raise ValueError(
                        f"{skill.skill_id} has unknown artifact type {artifact.artifact_type}."
                    )
                if artifact.visibility not in KNOWN_VISIBILITIES:
                    raise ValueError(
                        f"{skill.skill_id} has unknown visibility {artifact.visibility}."
                    )
            if skill.governance:
                if skill.governance.risk_level not in KNOWN_RISK_LEVELS:
                    raise ValueError(
                        f"{skill.skill_id} has unknown risk level {skill.governance.risk_level}."
                    )
                if skill.governance.max_repair_attempts < 0:
                    raise ValueError(f"{skill.skill_id} has negative max_repair_attempts.")


def select_skills_for_agent(
    *,
    agent_id: str,
    stage: str = "",
    delivery_state: DeliveryState | None = None,
    catalog: SkillCatalog | None = None,
) -> SkillSelectionResult:
    """Select default skills for an AgentExecutor invocation."""

    active_catalog = catalog or DEFAULT_SKILL_CATALOG
    skill_ids = list(DEFAULT_SKILLS_BY_AGENT.get(agent_id, ()))
    if _repair_context(delivery_state or {}, stage) and "repair-loop" not in skill_ids:
        skill_ids.append("repair-loop")
    descriptors = tuple(
        active_catalog.get(skill_id)
        for skill_id in _unique(skill_ids)
        if active_catalog.maybe_get(skill_id)
    )
    selections = tuple(
        SkillSelection(
            skill_id=skill.skill_id,
            version=skill.version,
            reason=_selection_reason(skill.skill_id, agent_id, stage),
            work_item_stage=skill.work_item_stage,
            dashboard_status=skill.dashboard_status,
            source_path=skill.source_path,
            contract_hash=skill.contract_hash,
        )
        for skill in descriptors
    )
    return SkillSelectionResult(
        agent_id=agent_id,
        stage=stage,
        selections=selections,
        descriptors=descriptors,
    )


def render_skill_instructions(
    skills: tuple[SkillDescriptor, ...] | SkillSelectionResult,
) -> str:
    """Render selected skills as a compact prompt block."""

    descriptors = skills.descriptors if isinstance(skills, SkillSelectionResult) else skills
    if not descriptors:
        return ""
    blocks = ["Selected runtime skills:"]
    for skill in descriptors:
        contract = {
            "skill_id": skill.skill_id,
            "version": skill.version,
            "source_path": skill.source_path,
            "contract_hash": skill.contract_hash,
            "trigger": skill.trigger,
            "required_tools": list(skill.required_tools),
            "expected_artifacts": [artifact.to_dict() for artifact in skill.expected_artifacts],
            "dashboard": {
                "status": skill.dashboard_status,
                "work_item_stage": skill.work_item_stage,
                "external_systems_supported": list(skill.external_systems_supported),
            },
            "governance": asdict(skill.governance) if skill.governance else {},
            "example": skill.examples[0] if skill.examples else {},
        }
        blocks.append(
            "\n".join(
                [
                    f"## {skill.skill_id} v{skill.version}",
                    "Contract hints:",
                    json.dumps(contract, indent=2, sort_keys=True),
                    "Playbook:",
                    skill.body.strip(),
                ]
            )
        )
    blocks.append(
        "Use only these selected skill instructions; do not assume the full skill catalog."
    )
    return "\n\n".join(blocks)


def selected_skill_trace_data(
    selections: tuple[SkillSelection, ...] | SkillSelectionResult,
) -> list[dict[str, Any]]:
    """Return stable selected skill metadata for trace payloads."""

    if isinstance(selections, SkillSelectionResult):
        return selections.to_trace_data()
    return [selection.to_dict() for selection in selections]


# Codex worker agent ids differ from the planner ids that skills declare in
# ``applies_to_agents``: the QA worker runs as ``qa-codex-agent`` but skills target
# ``qa-agent``, etc. Normalize so the injected skill index actually resolves for the
# worker — otherwise QA/Deployment/Handoff Codex workers silently get ZERO skills.
_SKILL_AGENT_ALIASES = {
    "qa-codex-agent": "qa-agent",
    "deployment-codex-agent": "deployment-agent",
    "handoff-codex-agent": "documentation-handoff-agent",
}


def canonical_skill_agent_id(agent_id: str) -> str:
    """Map a Codex worker agent id to the planner id skills are declared against."""
    return _SKILL_AGENT_ALIASES.get(agent_id, agent_id)


def applicable_skills_for_agent(
    agent_id: str,
    *,
    catalog: SkillCatalog | None = None,
) -> tuple[SkillDescriptor, ...]:
    """Every active skill an agent MAY load (by ``applies_to_agents``)."""

    active = catalog or DEFAULT_SKILL_CATALOG
    canonical = canonical_skill_agent_id(agent_id)
    return tuple(
        skill for skill in active.all() if canonical in skill.applies_to_agents
    )


def provision_native_skills(
    workspace_dir: Path | str,
    *,
    catalog: SkillCatalog | None = None,
) -> Path:
    """Provision the skill catalog into Codex's NATIVE discovery path.

    Codex auto-discovers skills from ``<dir>/.agents/skills/<id>/SKILL.md`` and triggers
    them by their ``description`` (progressive disclosure: only name + description sit in
    context until the model selects one). The worker runs with cwd =
    ``<workspace>/generated-project``, so Codex scans ``$CWD/../.agents/skills`` =
    ``<workspace>/.agents/skills`` — outside the deliverable working tree, so nothing
    leaks into the project's git/PR. We copy each authored ``SKILL.md`` there verbatim
    (it is already Codex-native: ``name`` + ``description`` frontmatter + body) — no ADL
    sidecar, no hand-injected prompt index. Returns the skills root.

    Idempotent and guarded: re-running refreshes the files; a copy failure for one skill
    never blocks the others or the run.
    """

    active = catalog or DEFAULT_SKILL_CATALOG
    skills_root = Path(workspace_dir) / ".agents" / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    for skill in active.all():
        src = Path(skill.source_path)
        if not src.is_file():
            continue
        dest_dir = skills_root / skill.skill_id
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest_dir / "SKILL.md")
        except Exception:  # one skill failing must never block the rest or the run
            continue
    return skills_root


SKILL_CATALOG_DIR = Path(__file__).with_name("skill_catalog")


def load_skill_catalog(catalog_dir: Path = SKILL_CATALOG_DIR) -> SkillCatalog:
    """Load runtime skills from ADL skill packages."""

    skill_dirs = sorted(path for path in catalog_dir.iterdir() if path.is_dir())
    if not skill_dirs:
        raise ValueError(f"No skill package directories found under {catalog_dir}.")
    return SkillCatalog(tuple(_load_skill_package(path) for path in skill_dirs))


def default_skills_by_agent(catalog: SkillCatalog) -> dict[str, tuple[str, ...]]:
    """Return default skill ids from ADL sidecar contracts."""

    mapping: dict[str, list[str]] = {}
    for skill in catalog.all():
        for agent_id in skill.default_for_agents:
            mapping.setdefault(agent_id, []).append(skill.skill_id)
    return {agent_id: tuple(skill_ids) for agent_id, skill_ids in mapping.items()}


def _load_skill_package(skill_dir: Path) -> SkillDescriptor:
    skill_path = skill_dir / "SKILL.md"
    contract_path = skill_dir / "agents" / "adl.yaml"
    if not skill_path.exists():
        raise ValueError(f"{skill_dir} is missing SKILL.md.")
    if not contract_path.exists():
        raise ValueError(f"{skill_dir} is missing agents/adl.yaml.")

    skill_text = skill_path.read_text(encoding="utf-8")
    contract_text = contract_path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(skill_text)
    metadata = _parse_skill_frontmatter(frontmatter, source=skill_path)
    contract = _load_yaml_contract(contract_text, source=contract_path)
    package = SkillPackage(
        skill_id=_required_string(contract, "skill_id", source=contract_path),
        name=_required_string(metadata, "name", source=skill_path),
        description=_required_string(metadata, "description", source=skill_path),
        body=body.strip(),
        version=_required_string(contract, "version", source=contract_path),
        status=_required_string(contract, "status", source=contract_path),
        trust_level=_required_string(contract, "trust_level", source=contract_path),
        selection=_selection_rules(_required_mapping(contract, "selection", source=contract_path)),
        runtime_contract=_runtime_contract(
            _required_mapping(contract, "contracts", source=contract_path),
            source=contract_path,
        ),
        dashboard=_dashboard_contract(
            _required_mapping(contract, "dashboard", source=contract_path),
            source=contract_path,
        ),
        governance=_governance(
            _required_mapping(contract, "governance", source=contract_path),
            source=contract_path,
        ),
        validation=_validation(
            _required_mapping(contract, "validation", source=contract_path),
            source=contract_path,
        ),
        examples=_examples(contract.get("examples"), source=contract_path),
        source_path=str(skill_path),
        contract_path=str(contract_path),
        contract_hash=_contract_hash(skill_text, contract_text),
    )
    _validate_package(package, skill_dir)
    return SkillDescriptor.from_package(package)


def _split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must start with YAML frontmatter delimiter.")
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    raise ValueError("SKILL.md frontmatter is missing closing delimiter.")


def _parse_skill_frontmatter(frontmatter: str, *, source: Path) -> dict[str, Any]:
    parsed = yaml.safe_load(frontmatter) or {}
    if not isinstance(parsed, dict):
        raise ValueError(f"{source} frontmatter must be a mapping.")
    extra_keys = set(parsed) - {"name", "description"}
    if extra_keys:
        raise ValueError(f"{source} has non-portable frontmatter keys: {sorted(extra_keys)}.")
    return dict(parsed)


def _load_yaml_contract(text: str, *, source: Path) -> dict[str, Any]:
    parsed = yaml.safe_load(text) or {}
    if not isinstance(parsed, dict):
        raise ValueError(f"{source} must be a YAML mapping.")
    return dict(parsed)


def _selection_rules(value: dict[str, Any]) -> SkillSelectionRules:
    return SkillSelectionRules(
        applies_to_agents=_string_tuple(value.get("applies_to_agents")),
        default_for_agents=_string_tuple(value.get("default_for_agents")),
        stages=_string_tuple(value.get("stages")),
        trigger_keywords=_string_tuple(value.get("trigger_keywords")),
        negative_triggers=_string_tuple(value.get("negative_triggers")),
    )


def _runtime_contract(value: dict[str, Any], *, source: Path) -> SkillRuntimeContract:
    return SkillRuntimeContract(
        required_tools=_string_tuple(value.get("required_tools")),
        allowed_tools=_string_tuple(value.get("allowed_tools")),
        consumes_artifacts=_string_tuple(value.get("consumes_artifacts")),
        produces_artifacts=_expected_artifacts(value.get("produces_artifacts"), source=source),
    )


def _dashboard_contract(value: dict[str, Any], *, source: Path) -> SkillDashboardContract:
    status = str(value.get("status") or "")
    if status not in KNOWN_DASHBOARD_STATUSES:
        raise ValueError(f"{source} has unknown dashboard status {status}.")
    return SkillDashboardContract(
        status=status,
        work_item_stage=str(value.get("work_item_stage") or ""),
        external_systems_supported=_string_tuple(value.get("external_systems_supported")),
        comment_template=str(value.get("comment_template") or ""),
        labels=_string_tuple(value.get("labels")),
    )


def _governance(value: dict[str, Any], *, source: Path) -> SkillGovernance:
    risk_level = str(value.get("risk_level") or "")
    if risk_level not in KNOWN_RISK_LEVELS:
        raise ValueError(f"{source} has unknown risk_level {risk_level}.")
    return SkillGovernance(
        risk_level=risk_level,
        requires_human_approval=bool(value.get("requires_human_approval", False)),
        max_repair_attempts=int(value.get("max_repair_attempts", 0)),
        checkpoint_before=bool(value.get("checkpoint_before", False)),
        checkpoint_after=bool(value.get("checkpoint_after", False)),
    )


def _validation(value: dict[str, Any], *, source: Path) -> SkillValidation:
    validation = SkillValidation(
        eval_prompts=_string_tuple(value.get("eval_prompts")),
        success_signals=_string_tuple(value.get("success_signals")),
    )
    if not validation.eval_prompts:
        raise ValueError(f"{source} validation.eval_prompts is required.")
    if not validation.success_signals:
        raise ValueError(f"{source} validation.success_signals is required.")
    return validation


def _expected_artifacts(value: Any, *, source: Path) -> tuple[SkillExpectedArtifact, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{source} produces_artifacts must be a list.")
    artifacts: list[SkillExpectedArtifact] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{source} produces_artifacts items must be objects.")
        artifacts.append(
            SkillExpectedArtifact(
                artifact_type=str(item.get("artifact_type") or ""),
                visibility=str(item.get("visibility") or ""),
                required=bool(item.get("required", True)),
                dashboard_label=str(item.get("dashboard_label") or ""),
                external_reference_type=str(item.get("external_reference_type") or "work_item"),
            )
        )
    return tuple(artifacts)


def _examples(value: Any, *, source: Path) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise ValueError(f"{source} examples must be a list.")
    examples: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{source} examples items must be objects.")
        examples.append(dict(item))
    return tuple(examples)


def _validate_package(package: SkillPackage, skill_dir: Path) -> None:
    if package.skill_id != skill_dir.name:
        raise ValueError(f"{skill_dir} skill_id must match folder name.")
    if package.status not in KNOWN_SKILL_STATUSES:
        raise ValueError(f"{package.skill_id} has unknown status {package.status}.")
    if package.trust_level not in KNOWN_TRUST_LEVELS:
        raise ValueError(f"{package.skill_id} has unknown trust level {package.trust_level}.")
    if not package.body:
        raise ValueError(f"{package.skill_id} has an empty SKILL.md body.")
    if package.name != package.skill_id:
        raise ValueError(f"{package.skill_id} SKILL.md name must equal skill_id.")
    if not package.selection.applies_to_agents:
        raise ValueError(f"{package.skill_id} has no applies_to_agents.")
    if not package.selection.default_for_agents:
        raise ValueError(f"{package.skill_id} has no default_for_agents.")
    if not package.selection.stages:
        raise ValueError(f"{package.skill_id} has no stages.")
    if not package.selection.trigger_keywords:
        raise ValueError(f"{package.skill_id} has no trigger_keywords.")
    if not package.runtime_contract.required_tools:
        raise ValueError(f"{package.skill_id} has no required_tools.")
    if not package.runtime_contract.allowed_tools:
        raise ValueError(f"{package.skill_id} has no allowed_tools.")
    if not package.runtime_contract.produces_artifacts:
        raise ValueError(f"{package.skill_id} has no produces_artifacts.")
    for artifact_type in package.runtime_contract.consumes_artifacts:
        if artifact_type not in KNOWN_ARTIFACT_TYPES:
            raise ValueError(f"{package.skill_id} consumes unknown artifact {artifact_type}.")
    for artifact in package.runtime_contract.produces_artifacts:
        if artifact.artifact_type not in KNOWN_ARTIFACT_TYPES:
            raise ValueError(
                f"{package.skill_id} produces unknown artifact {artifact.artifact_type}."
            )
        if artifact.visibility not in KNOWN_VISIBILITIES:
            raise ValueError(f"{package.skill_id} uses unknown visibility {artifact.visibility}.")
    if not package.dashboard.work_item_stage:
        raise ValueError(f"{package.skill_id} has no dashboard work_item_stage.")
    if not {"internal", "github", "jira", "azure_devops"}.issubset(
        set(package.dashboard.external_systems_supported)
    ):
        raise ValueError(f"{package.skill_id} is not board-ready.")
    if package.governance.max_repair_attempts < 0:
        raise ValueError(f"{package.skill_id} has negative max_repair_attempts.")
    if not package.examples:
        raise ValueError(f"{package.skill_id} has no examples.")


def _instruction_summary(body: str) -> tuple[str, ...]:
    lines = []
    in_workflow = False
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            in_workflow = line == "## Workflow"
            continue
        if in_workflow and line and (line[0].isdigit() or line.startswith("- ")):
            lines.append(line)
    return tuple(lines) or (body.strip(),)


def _contract_hash(skill_text: str, contract_text: str) -> str:
    digest = hashlib.sha256()
    digest.update(skill_text.encode("utf-8"))
    digest.update(b"\n---adl-contract---\n")
    digest.update(contract_text.encode("utf-8"))
    return digest.hexdigest()[:16]


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if str(item))
    if isinstance(value, str) and value:
        return (value,)
    return ()


def _required_mapping(metadata: dict[str, Any], key: str, *, source: Path) -> dict[str, Any]:
    value = metadata.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{source} is missing required mapping {key}.")
    return dict(value)


def _required_string(metadata: dict[str, Any], key: str, *, source: Path) -> str:
    value = str(metadata.get(key) or "").strip()
    if not value:
        raise ValueError(f"{source} is missing required key {key}.")
    return value


DEFAULT_SKILL_CATALOG = load_skill_catalog()
DEFAULT_SKILLS_BY_AGENT = default_skills_by_agent(DEFAULT_SKILL_CATALOG)


def _selection_reason(skill_id: str, agent_id: str, stage: str) -> str:
    if skill_id == "repair-loop":
        return f"{agent_id} may need bounded Repair during {stage or 'runtime'}."
    return f"{skill_id} is the default skill for {agent_id} during {stage or 'runtime'}."


def _repair_context(state: DeliveryState, stage: str) -> bool:
    status_values = [
        str(state.get("status") or ""),
        str(state.get("qa_status") or ""),
        str(state.get("deployment_status") or ""),
        str(state.get("post_deploy_qa_status") or ""),
        stage,
    ]
    text = " ".join(status_values).lower()
    if any(token in text for token in ("repair", "failed", "blocked", "precondition")):
        return True
    if state.get("blockers"):
        return True
    if state.get("fix_request_artifacts"):
        return True
    return bool(state.get("repair_attempts") or state.get("post_deploy_repair_attempts"))


def _unique(values: list[str] | tuple[str, ...]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique
