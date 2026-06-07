"""Artifact registry for dashboard-ready delivery outputs."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from agentic_company.platform.tool_contracts import ArtifactLink

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

    def to_tool_ref(self, *, url: str = "") -> ArtifactLink:
        return ArtifactLink(
            artifact_id=self.artifact_id,
            path=self.relative_path,
            label=self.label,
            artifact_type=self.artifact_type,
            visibility=self.visibility,
            url=url,
        )


def artifact_id_for(run_id: int | str, relative_path: str) -> str:
    """Return a stable artifact id for a run-local path."""

    normalized_path = normalize_artifact_path(relative_path)
    digest = hashlib.sha1(f"{run_id}:{normalized_path}".encode()).hexdigest()[:16]
    return f"art_{digest}"


def normalize_artifact_path(path: str | Path) -> str:
    """Normalize run-local artifact paths for manifests and URLs."""

    return str(path).replace("\\", "/").lstrip("/")


def resolve_run_artifact_path(run_dir: Path, relative_path: str | Path) -> Path:
    """Resolve a run-local artifact path and reject paths outside the run."""

    root = run_dir.resolve()
    raw_path = Path(str(relative_path))
    normalized_path = normalize_artifact_path(relative_path)
    candidate = (
        raw_path.resolve()
        if _is_absolute_artifact_path(relative_path)
        else (run_dir / normalized_path).resolve()
    )
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Artifact path must stay inside run directory: {normalized_path}"
        ) from exc
    return candidate


def register_artifact(
    run_dir: Path,
    *,
    artifact_id: str,
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
    """Validate explicit artifact metadata and build a DB-ready artifact record."""

    normalized_path = normalize_artifact_path(relative_path)
    resolved_run_id = run_id if run_id not in (None, "") else _run_id_from_dir(run_dir)
    required = {
        "artifact_id": artifact_id,
        "run_id": resolved_run_id,
        "relative_path": normalized_path,
        "owner_agent": owner_agent,
        "artifact_type": artifact_type,
        "visibility": visibility,
        "label": label,
        "source_tool": source_tool,
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        raise ValueError(
            "Artifact registration requires explicit metadata: " + ", ".join(sorted(missing))
        )
    record = ArtifactRecord(
        artifact_id=artifact_id,
        project_id=project_id,
        run_id=resolved_run_id,
        work_item_id=work_item_id,
        owner_agent=owner_agent,
        artifact_type=artifact_type,
        visibility=normalize_visibility(visibility),
        storage_uri=storage_uri,
        relative_path=normalized_path,
        label=label,
        created_at=_artifact_created_at(resolve_run_artifact_path(run_dir, normalized_path)),
        source_tool=source_tool,
        source_model=source_model,
        external_refs=list(external_refs or []),
        metadata=dict(metadata or {}),
    )
    return record


def artifact_record_from_mapping(value: Mapping[str, Any]) -> ArtifactRecord | None:
    """Build an ArtifactRecord from untrusted JSON/DB values."""

    required = (
        "artifact_id",
        "artifact_type",
        "visibility",
        "owner_agent",
        "source_tool",
        "label",
        "run_id",
    )
    missing = [field for field in required if not str(value.get(field) or "").strip()]
    if missing:
        raise ValueError("Missing required artifact metadata: " + ", ".join(missing))
    raw_path = value.get("relative_path") or ""
    relative_path = normalize_artifact_path(str(raw_path))
    run_id = value.get("run_id")
    if not relative_path:
        raise ValueError("Missing required artifact metadata: relative_path")
    external_refs = value.get("external_refs", [])
    metadata = value.get("metadata", {})
    return ArtifactRecord(
        artifact_id=str(value["artifact_id"]),
        project_id=_optional_int(value.get("project_id")),
        run_id=run_id,
        work_item_id=_optional_str(value.get("work_item_id")),
        owner_agent=str(value["owner_agent"]),
        artifact_type=str(value["artifact_type"]),
        visibility=normalize_visibility(str(value["visibility"])),
        storage_uri=str(value.get("storage_uri") or ""),
        relative_path=relative_path,
        label=str(value["label"]),
        created_at=str(value.get("created_at") or datetime.now(UTC).isoformat()),
        source_tool=str(value["source_tool"]),
        source_model=str(value.get("source_model") or ""),
        external_refs=external_refs if isinstance(external_refs, list) else [],
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def tool_artifact_refs_from_records(
    records: Iterable[ArtifactRecord],
    *,
    run_url_prefix: str = "",
) -> tuple[ArtifactLink, ...]:
    """Convert registry records to contract-ready artifact links."""

    refs: list[ArtifactLink] = []
    for record in records:
        url = f"{run_url_prefix}/by-id/{record.artifact_id}" if run_url_prefix else ""
        refs.append(record.to_tool_ref(url=url))
    return tuple(refs)


def normalize_visibility(value: str) -> str:
    normalized = value.strip().lower()
    if normalized == "user":
        return "business"
    if normalized in {
        "business",
        "release",
        "qa_evidence",
        "developer",
        "internal",
    }:
        return normalized
    return "developer"


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


def _is_absolute_artifact_path(path: str | Path) -> bool:
    raw = str(path)
    return Path(raw).is_absolute() or PureWindowsPath(raw).is_absolute()
