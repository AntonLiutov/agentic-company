"""Artifact registry for dashboard-ready delivery outputs."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentic_company.platform.artifacts import ArtifactRef, read_json_artifact, write_json_artifact
from agentic_company.platform.tool_contracts import ToolArtifactRef

ARTIFACT_REGISTRY_PATH = "delivery/artifact-registry.json"
USER_FACING_VISIBILITIES = {"business", "release", "qa_evidence"}


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """Stable artifact metadata for UI, traces, skills, and external dashboards."""

    artifact_id: str
    project_id: int | None
    run_id: int | str
    work_item_id: str | None
    owner_agent: str
    artifact_type: str
    visibility: str
    storage_uri: str
    relative_path: str
    label: str
    created_at: str
    source_tool: str
    source_model: str
    external_refs: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_tool_ref(self, *, url: str = "") -> ToolArtifactRef:
        return ToolArtifactRef(
            artifact_id=self.artifact_id,
            path=self.relative_path,
            label=self.label,
            artifact_type=self.artifact_type,
            visibility=self.visibility,
            url=url,
        )


def artifact_id_for(run_id: int | str, relative_path: str) -> str:
    """Return a deterministic artifact id for a run-local path."""

    normalized_path = normalize_artifact_path(relative_path)
    digest = hashlib.sha1(f"{run_id}:{normalized_path}".encode()).hexdigest()[:16]
    return f"art_{digest}"


def normalize_artifact_path(path: str | Path) -> str:
    """Normalize run-local artifact paths for manifests and URLs."""

    return str(path).replace("\\", "/").lstrip("/")


def register_artifact(
    run_dir: Path,
    *,
    relative_path: str | Path,
    run_id: int | str | None = None,
    project_id: int | None = None,
    work_item_id: str | None = None,
    owner_agent: str = "",
    artifact_type: str = "",
    visibility: str = "",
    label: str = "",
    source_tool: str = "",
    source_model: str = "",
    storage_uri: str = "",
    external_refs: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ArtifactRecord:
    """Register or update one artifact in the run-local registry manifest."""

    normalized_path = normalize_artifact_path(relative_path)
    resolved_run_id = run_id if run_id not in (None, "") else _run_id_from_dir(run_dir)
    record = ArtifactRecord(
        artifact_id=artifact_id_for(resolved_run_id, normalized_path),
        project_id=project_id,
        run_id=resolved_run_id,
        work_item_id=work_item_id,
        owner_agent=owner_agent or "unknown",
        artifact_type=artifact_type or infer_artifact_type(normalized_path),
        visibility=normalize_visibility(visibility or infer_visibility(normalized_path)),
        storage_uri=storage_uri,
        relative_path=normalized_path,
        label=label or infer_label(normalized_path),
        created_at=_artifact_created_at(run_dir / normalized_path),
        source_tool=source_tool,
        source_model=source_model,
        external_refs=list(external_refs or []),
        metadata=dict(metadata or {}),
    )
    records = load_artifact_registry(run_dir)
    merged: dict[str, ArtifactRecord] = {item.artifact_id: item for item in records}
    merged[record.artifact_id] = record
    save_artifact_registry(run_dir, sorted(merged.values(), key=lambda item: item.relative_path))
    return record


def register_artifacts_from_refs(
    run_dir: Path,
    refs: Iterable[ArtifactRef | Mapping[str, Any]],
    *,
    run_id: int | str | None = None,
    project_id: int | None = None,
    source_tool: str = "",
    source_model: str = "",
) -> list[ArtifactRecord]:
    """Register current legacy ArtifactRef values as ArtifactRecord rows."""

    records: list[ArtifactRecord] = []
    for ref in refs:
        path = str(ref.get("path", ""))
        if not path:
            continue
        records.append(
            register_artifact(
                run_dir,
                relative_path=path,
                run_id=run_id,
                project_id=project_id,
                owner_agent=str(ref.get("owner_agent", "")),
                artifact_type=infer_artifact_type(path, legacy_kind=str(ref.get("kind", ""))),
                visibility=visibility_from_legacy(str(ref.get("visibility", "")), path),
                source_tool=source_tool,
                source_model=source_model,
                metadata={
                    "legacy_kind": str(ref.get("kind", "")),
                    "implicit_resolution_warnings": [
                        f"artifact_type inferred from path {normalize_artifact_path(path)}",
                        "visibility inferred from legacy artifact reference",
                    ],
                },
            )
        )
    return records


def load_artifact_registry(run_dir: Path) -> list[ArtifactRecord]:
    """Load a run-local artifact registry manifest."""

    path = run_dir / ARTIFACT_REGISTRY_PATH
    if not path.exists():
        return []
    try:
        payload = read_json_artifact(path, normalize_bom=True)
    except (OSError, ValueError):
        return []
    raw_records = payload.get("artifacts", []) if isinstance(payload, dict) else []
    records: list[ArtifactRecord] = []
    for item in raw_records:
        if isinstance(item, dict):
            record = artifact_record_from_mapping(item)
            if record:
                records.append(record)
    return records


def save_artifact_registry(run_dir: Path, records: Iterable[ArtifactRecord]) -> None:
    """Persist artifact records into the run-local manifest."""

    write_json_artifact(
        run_dir / ARTIFACT_REGISTRY_PATH,
        {
            "version": 1,
            "updated_at": datetime.now(UTC).isoformat(),
            "artifacts": [record.to_dict() for record in records],
        },
    )


def get_artifact_by_id(run_dir: Path, artifact_id: str) -> ArtifactRecord | None:
    """Return one artifact record by stable id."""

    for record in load_artifact_registry(run_dir):
        if record.artifact_id == artifact_id:
            return record
    return None


def list_artifacts(
    run_dir: Path,
    *,
    visibility: str | set[str] | None = None,
    owner_agent: str | None = None,
    work_item_id: str | None = None,
) -> list[ArtifactRecord]:
    """List artifact records with optional dashboard-oriented filters."""

    visibilities = {visibility} if isinstance(visibility, str) else visibility
    records = load_artifact_registry(run_dir)
    if visibilities:
        records = [record for record in records if record.visibility in visibilities]
    if owner_agent:
        records = [record for record in records if record.owner_agent == owner_agent]
    if work_item_id:
        records = [record for record in records if record.work_item_id == work_item_id]
    return records


def artifact_record_from_mapping(value: Mapping[str, Any]) -> ArtifactRecord | None:
    """Build an ArtifactRecord from untrusted JSON/DB values."""

    raw_path = value.get("relative_path") or value.get("path") or ""
    relative_path = normalize_artifact_path(str(raw_path))
    run_id = value.get("run_id")
    if not relative_path or run_id in (None, ""):
        return None
    artifact_id = str(value.get("artifact_id") or artifact_id_for(run_id, relative_path))
    external_refs = value.get("external_refs", [])
    metadata = value.get("metadata", {})
    return ArtifactRecord(
        artifact_id=artifact_id,
        project_id=_optional_int(value.get("project_id")),
        run_id=run_id,
        work_item_id=_optional_str(value.get("work_item_id")),
        owner_agent=str(value.get("owner_agent") or value.get("agent") or "unknown"),
        artifact_type=str(value.get("artifact_type") or infer_artifact_type(relative_path)),
        visibility=normalize_visibility(
            str(value.get("visibility") or infer_visibility(relative_path))
        ),
        storage_uri=str(value.get("storage_uri") or ""),
        relative_path=relative_path,
        label=str(value.get("label") or infer_label(relative_path)),
        created_at=str(value.get("created_at") or datetime.now(UTC).isoformat()),
        source_tool=str(value.get("source_tool") or ""),
        source_model=str(value.get("source_model") or ""),
        external_refs=external_refs if isinstance(external_refs, list) else [],
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def tool_artifact_refs_from_records(
    records: Iterable[ArtifactRecord],
    *,
    run_url_prefix: str = "",
) -> tuple[ToolArtifactRef, ...]:
    """Convert registry records to contract-ready artifact refs."""

    refs: list[ToolArtifactRef] = []
    for record in records:
        url = f"{run_url_prefix}/by-id/{record.artifact_id}" if run_url_prefix else ""
        refs.append(record.to_tool_ref(url=url))
    return tuple(refs)


def normalize_visibility(value: str) -> str:
    normalized = value.strip().lower()
    if normalized == "user":
        return "business"
    if normalized in {"business", "release", "qa_evidence", "developer", "internal"}:
        return normalized
    return "developer"


def visibility_from_legacy(value: str, path: str) -> str:
    normalized = value.strip().lower()
    if normalized == "user":
        return infer_visibility(path)
    return normalize_visibility(normalized)


def infer_visibility(path: str) -> str:
    normalized = normalize_artifact_path(path).lower()
    if normalized.endswith("release-report.html"):
        return "release"
    if "qa" in normalized and (normalized.endswith(".md") or "screenshot" in normalized):
        return "qa_evidence"
    if any(token in normalized for token in ("prompt.md", "events.jsonl", ".log", "request.json")):
        return "internal"
    if normalized.endswith(".json") or "/codex/" in normalized or "/decisions/" in normalized:
        return "developer"
    return "business"


def infer_artifact_type(path: str, *, legacy_kind: str = "") -> str:
    normalized = normalize_artifact_path(path).lower()
    filename = Path(normalized).name
    if "business-analysis" in normalized:
        return "requirements_brief"
    if "architecture" in normalized:
        return "architecture_report"
    if "project-management" in normalized or "delivery-plan" in normalized:
        return "delivery_plan"
    if filename.startswith("07-execution-summary"):
        return "execution_summary"
    if filename.startswith("08-qa-report") or "qa-report" in filename:
        return "qa_report"
    if filename.startswith("13-deployment-summary") or "deployment-summary" in filename:
        return "deployment_summary"
    if filename == "release-report.html":
        return "release_report"
    if "screenshot" in normalized:
        return "screenshot_evidence"
    if filename.endswith(".log") or filename == "events.jsonl":
        return "codex_log"
    if filename.endswith("request.json"):
        return "tool_request"
    if filename.endswith("result.json") or filename.startswith("results-"):
        return "tool_result"
    if legacy_kind:
        return legacy_kind.replace("-", "_")
    return "debug_trace" if infer_visibility(normalized) == "internal" else "artifact"


def infer_label(path: str) -> str:
    filename = Path(normalize_artifact_path(path)).name
    labels = {
        "business-analysis.md": "Requirements brief",
        "architecture.md": "Architecture report",
        "project-management.md": "Delivery plan",
        "13-deployment-summary.md": "Deployment summary",
        "release-report.html": "Release report",
    }
    if filename in labels:
        return labels[filename]
    if filename.startswith("07-execution-summary"):
        return "Execution summary"
    if filename.startswith("08-qa-report"):
        return "Quality review report"
    return filename


def _run_id_from_dir(run_dir: Path) -> str:
    return run_dir.name or "run"


def _artifact_created_at(path: Path) -> str:
    if path.exists():
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    return datetime.now(UTC).isoformat()


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
