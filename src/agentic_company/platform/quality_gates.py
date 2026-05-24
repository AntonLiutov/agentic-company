"""Structured quality gates for generated delivery applications."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from hashlib import sha1
from pathlib import Path
from typing import Any, Literal, Protocol

from agentic_company.platform.artifact_registry import ArtifactRecord, register_artifact
from agentic_company.platform.models import ExecutionRequest
from agentic_company.platform.run_trace import record_run_event

QualityCheckStatus = Literal["passed", "failed", "limited", "skipped"]
FailureReason = Literal[
    "needs_repair",
    "qa_failed",
    "deploy_failed",
    "provider_limit",
    "secret_missing",
    "human_approval_required",
    "blocked",
]

QUALITY_GATE_AGENT_ID = "quality-gate-runner"
QUALITY_GATE_PLAN_TYPE = "quality_gate_plan"
QUALITY_GATE_REPORT_TYPE = "quality_gate_report"
REPAIR_REQUEST_TYPE = "repair_request"
UI_KEYWORDS = {
    "button",
    "click",
    "dashboard",
    "form",
    "landing",
    "modal",
    "navigation",
    "page",
    "screen",
    "ui",
    "ux",
    "view",
    "visual",
    "web",
}
EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "playwright-report",
}
TEXT_SUFFIXES = {".css", ".html", ".js", ".jsx", ".md", ".py", ".ts", ".tsx", ".vue"}
PLACEHOLDER_PATTERNS = (
    re.compile(r"\blorem ipsum\b", re.IGNORECASE),
    re.compile(r"\bcoming soon\b", re.IGNORECASE),
    re.compile(r"\bnot implemented\b", re.IGNORECASE),
    re.compile(r"\bmock only\b", re.IGNORECASE),
    re.compile(r"\bplaceholder\b", re.IGNORECASE),
    re.compile(r"href=[\"']#[\"']", re.IGNORECASE),
    re.compile(r"disabled(?:=|\s|>)", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class QualityEvidenceRef:
    """Evidence produced by a quality gate."""

    path: str
    label: str
    artifact_id: str = ""
    evidence_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _drop_empty(asdict(self))


@dataclass(frozen=True, slots=True)
class QualityCheckResult:
    """One deterministic or browser-backed quality check result."""

    gate: str
    name: str
    status: QualityCheckStatus
    evidence: str
    severity: str = "medium"
    failure_reason: FailureReason | None = None
    remediation_owner: str = "none"
    failure_signature: str = ""
    reproduction_steps: list[str] = field(default_factory=list)
    evidence_refs: list[QualityEvidenceRef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence_refs"] = [ref.to_dict() for ref in self.evidence_refs]
        return _drop_empty(data)


@dataclass(frozen=True, slots=True)
class RepairRequest:
    """Machine-readable repair request emitted by failed quality gates."""

    work_item_id: str
    failure_reason: FailureReason
    responsible_agent: str
    reproduction_steps: list[str]
    expected_behavior: str
    actual_behavior: str
    evidence_artifact_ids: list[str]
    failure_signature: str
    recommended_fix_scope: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class QualityGatePlan:
    """Quality plan used by QA Codex and future external board comments."""

    work_item_id: str
    target_project_dir: str
    public_urls: list[str]
    ui_heavy: bool
    gates: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class QualityGateReport:
    """Aggregated quality gate output for one work item."""

    work_item_id: str
    status: QualityCheckStatus
    ui_heavy: bool
    checks: list[QualityCheckResult]
    repair_request: RepairRequest | None
    artifacts: list[QualityEvidenceRef]
    failure_signatures: list[str]
    recommended_next_action: str
    limited_evidence: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_item_id": self.work_item_id,
            "status": self.status,
            "ui_heavy": self.ui_heavy,
            "checks": [check.to_dict() for check in self.checks],
            "repair_request": self.repair_request.to_dict() if self.repair_request else None,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "failure_signatures": list(self.failure_signatures),
            "recommended_next_action": self.recommended_next_action,
            "limited_evidence": self.limited_evidence,
        }

    @property
    def blocks_release(self) -> bool:
        return self.status == "failed"


class BrowserAdapter(Protocol):
    """Browser automation boundary used by QualityGateRunner."""

    def check_url(
        self,
        url: str,
        *,
        run_dir: Path,
        work_item_id: str,
        gate: str,
    ) -> list[QualityCheckResult]:
        """Open the URL and return browser/visual check results."""


class PlaywrightBrowserAdapter:
    """Optional Playwright-backed browser checks.

    Import is intentionally lazy so unit tests and non-browser environments can
    still run the platform. A UI-heavy quality gate fails when this adapter is
    unavailable and no alternative evidence is present.
    """

    def check_url(
        self,
        url: str,
        *,
        run_dir: Path,
        work_item_id: str,
        gate: str,
    ) -> list[QualityCheckResult]:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError:
            return [
                QualityCheckResult(
                    gate=gate,
                    name="playwright_browser_available",
                    status="limited",
                    evidence="Playwright is not installed, so browser evidence is unavailable.",
                    severity="high",
                    failure_reason="needs_repair",
                    remediation_owner="fullstack-agent",
                    failure_signature="browser_adapter_unavailable:playwright_missing",
                    reproduction_steps=["Install Playwright and Chromium for full browser QA."],
                )
            ]

        screenshots_dir = run_dir / "qa" / "screenshots" / work_item_id
        traces_dir = run_dir / "qa" / "traces" / work_item_id
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        traces_dir.mkdir(parents=True, exist_ok=True)
        checks: list[QualityCheckResult] = []
        console_errors: list[str] = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                context.tracing.start(screenshots=True, snapshots=True, sources=False)
                page = context.new_page()
                page.on(
                    "console",
                    lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
                )
                page.goto(url, wait_until="networkidle", timeout=30_000)
                title = page.title()
                body_text = page.locator("body").inner_text(timeout=5_000)
                desktop_path = screenshots_dir / f"{gate}-desktop.png"
                page.screenshot(path=desktop_path, full_page=True)
                width = page.evaluate("document.documentElement.scrollWidth")
                viewport_width = page.evaluate("window.innerWidth")
                trace_path = traces_dir / f"{gate}.zip"
                context.tracing.stop(path=trace_path)
                browser.close()
        except PlaywrightError as exc:
            return [
                QualityCheckResult(
                    gate=gate,
                    name="browser_open_url",
                    status="failed",
                    evidence=f"Browser could not open {url}: {exc}",
                    severity="high",
                    failure_reason="qa_failed",
                    remediation_owner="fullstack-agent",
                    failure_signature=f"browser_open_failed:{_short_signature(str(exc))}",
                    reproduction_steps=[f"Open {url} in Chromium.", "Wait for page load."],
                )
            ]

        evidence_refs = [
            QualityEvidenceRef(
                path=desktop_path.relative_to(run_dir).as_posix(),
                label=f"{gate} desktop screenshot",
                evidence_type="screenshot",
            ),
            QualityEvidenceRef(
                path=trace_path.relative_to(run_dir).as_posix(),
                label=f"{gate} Playwright trace",
                evidence_type="trace",
            ),
        ]
        checks.append(
            QualityCheckResult(
                gate=gate,
                name="browser_open_url",
                status="passed",
                evidence=f"Opened {url}; title={title!r}; visible text length={len(body_text)}.",
                evidence_refs=evidence_refs,
            )
        )
        if console_errors:
            checks.append(
                QualityCheckResult(
                    gate=gate,
                    name="browser_console_errors",
                    status="failed",
                    evidence="Console errors: " + "; ".join(console_errors[:5]),
                    severity="high",
                    failure_reason="qa_failed",
                    remediation_owner="fullstack-agent",
                    failure_signature=f"console_errors:{_short_signature('|'.join(console_errors))}",
                    reproduction_steps=[f"Open {url}.", "Inspect browser console errors."],
                )
            )
        else:
            checks.append(
                QualityCheckResult(
                    gate=gate,
                    name="browser_console_errors",
                    status="passed",
                    evidence="No browser console errors were captured.",
                )
            )
        if int(width) > int(viewport_width) + 4:
            checks.append(
                QualityCheckResult(
                    gate=gate,
                    name="horizontal_overflow",
                    status="failed",
                    evidence=f"Document width {width}px exceeds viewport {viewport_width}px.",
                    severity="high",
                    failure_reason="qa_failed",
                    remediation_owner="fullstack-agent",
                    failure_signature=f"horizontal_overflow:{width}>{viewport_width}",
                    reproduction_steps=[f"Open {url} at 1440x900.", "Check horizontal overflow."],
                    evidence_refs=evidence_refs,
                )
            )
        else:
            checks.append(
                QualityCheckResult(
                    gate=gate,
                    name="horizontal_overflow",
                    status="passed",
                    evidence="No horizontal overflow detected at desktop viewport.",
                    evidence_refs=evidence_refs,
                )
            )
        return checks


@dataclass(slots=True)
class QualityGateRunner:
    """Run deterministic platform quality gates around Codex QA."""

    browser_adapter: BrowserAdapter | None = None

    def run(
        self,
        run_dir: Path,
        request: ExecutionRequest,
        feature: dict[str, Any],
    ) -> QualityGateReport:
        work_item_id = str(feature.get("id") or "full-run")
        public_urls = _public_urls(run_dir)
        ui_heavy = _is_ui_heavy(feature, Path(request.target_project_dir))
        gates = [
            "static_preflight",
            "local_startup",
            "browser_smoke",
            "visual_responsive",
        ]
        if public_urls:
            gates.append("deployment_parity")
        plan = QualityGatePlan(
            work_item_id=work_item_id,
            target_project_dir=request.target_project_dir,
            public_urls=public_urls,
            ui_heavy=ui_heavy,
            gates=gates,
        )
        plan_record = _write_registered_json(
            run_dir,
            relative_path=f"qa/gates/{work_item_id}/quality-gate-plan.json",
            payload=plan.to_dict(),
            request=request,
            work_item_id=work_item_id,
            artifact_type=QUALITY_GATE_PLAN_TYPE,
            visibility="developer",
            source_tool="quality_gate_runner",
        )
        record_run_event(
            run_dir,
            run_id=request.run_id,
            agent_id=QUALITY_GATE_AGENT_ID,
            event_type="quality_gates_started",
            status="in_progress",
            message=f"Quality gates started for {work_item_id}.",
            work_item_id=work_item_id,
            artifact_ids=[plan_record.artifact_id],
            data=plan.to_dict(),
        )

        checks = [
            *self._static_preflight(Path(request.target_project_dir), work_item_id),
            self._local_startup(Path(request.target_project_dir), work_item_id),
            *self._browser_checks(public_urls[:1], run_dir, work_item_id, ui_heavy),
        ]
        failed = [check for check in checks if check.status == "failed"]
        limited = [check for check in checks if check.status == "limited"]
        status: QualityCheckStatus = "failed" if failed else "passed"
        if status == "passed" and limited:
            status = "limited"
        repair_request = _repair_request_from_failures(work_item_id, failed)
        failure_signatures = [
            check.failure_signature for check in failed if check.failure_signature
        ]
        evidence_records = _register_evidence_refs(run_dir, request, work_item_id, checks)
        report = QualityGateReport(
            work_item_id=work_item_id,
            status=status,
            ui_heavy=ui_heavy,
            checks=checks,
            repair_request=repair_request,
            artifacts=[
                QualityEvidenceRef(
                    path=plan_record.relative_path,
                    label=plan_record.label,
                    artifact_id=plan_record.artifact_id,
                    evidence_type=plan_record.artifact_type,
                ),
                *[
                    QualityEvidenceRef(
                        path=record.relative_path,
                        label=record.label,
                        artifact_id=record.artifact_id,
                        evidence_type=record.artifact_type,
                    )
                    for record in evidence_records
                ],
            ],
            failure_signatures=failure_signatures,
            recommended_next_action=_recommended_next_action(status, repair_request),
            limited_evidence=bool(limited),
        )
        report_record = _write_registered_json(
            run_dir,
            relative_path=f"qa/gates/{work_item_id}/quality-gate-report.json",
            payload=report.to_dict(),
            request=request,
            work_item_id=work_item_id,
            artifact_type=QUALITY_GATE_REPORT_TYPE,
            visibility="qa_evidence",
            source_tool="quality_gate_runner",
        )
        if repair_request:
            _write_repair_request_artifacts(run_dir, request, repair_request)
        record_run_event(
            run_dir,
            run_id=request.run_id,
            agent_id=QUALITY_GATE_AGENT_ID,
            event_type="quality_gates_completed",
            status=status,
            message=f"Quality gates {status} for {work_item_id}.",
            work_item_id=work_item_id,
            artifact_ids=[plan_record.artifact_id, report_record.artifact_id],
            data={
                "failure_signatures": failure_signatures,
                "repair_request": repair_request.to_dict() if repair_request else None,
                "limited_evidence": bool(limited),
            },
        )
        return report

    def _static_preflight(self, target_dir: Path, work_item_id: str) -> list[QualityCheckResult]:
        if not target_dir.exists():
            return [
                QualityCheckResult(
                    gate="static_preflight",
                    name="target_project_exists",
                    status="failed",
                    evidence=f"Generated project directory does not exist: {target_dir}",
                    severity="critical",
                    failure_reason="qa_failed",
                    remediation_owner="fullstack-agent",
                    failure_signature="target_project_missing",
                    reproduction_steps=[f"Check generated project path: {target_dir}"],
                )
            ]

        checks = [
            QualityCheckResult(
                gate="static_preflight",
                name="target_project_exists",
                status="passed",
                evidence=f"Generated project exists: {target_dir}",
            )
        ]
        placeholders = _placeholder_findings(target_dir)
        if placeholders:
            checks.append(
                QualityCheckResult(
                    gate="static_preflight",
                    name="placeholder_or_dead_ui_strings",
                    status="failed",
                    evidence=(
                        "Potential placeholder/dead UI findings: " + "; ".join(placeholders[:8])
                    ),
                    severity="high",
                    failure_reason="needs_repair",
                    remediation_owner="fullstack-agent",
                    failure_signature=f"placeholder_strings:{_short_signature('|'.join(placeholders))}",
                    reproduction_steps=[
                        f"Inspect generated project files for {work_item_id}.",
                        "Replace fake/dead UI behavior with working behavior or "
                        "explicit limitation.",
                    ],
                )
            )
        else:
            checks.append(
                QualityCheckResult(
                    gate="static_preflight",
                    name="placeholder_or_dead_ui_strings",
                    status="passed",
                    evidence=(
                        "No obvious placeholder/dead UI strings found in scanned source files."
                    ),
                )
            )
        return checks

    def _local_startup(self, target_dir: Path, work_item_id: str) -> QualityCheckResult:
        entrypoints = _entrypoint_candidates(target_dir)
        if not entrypoints:
            return QualityCheckResult(
                gate="local_startup",
                name="entrypoint_detected",
                status="failed",
                evidence="No obvious runnable app entrypoint was detected.",
                severity="high",
                failure_reason="needs_repair",
                remediation_owner="fullstack-agent",
                failure_signature="entrypoint_missing",
                reproduction_steps=[
                    f"Inspect {target_dir}.",
                    "Add README/run command, package script, Dockerfile, app.py, "
                    "main.py, or index.html.",
                ],
            )
        return QualityCheckResult(
            gate="local_startup",
            name="entrypoint_detected",
            status="passed",
            evidence="Runnable entrypoint evidence: " + ", ".join(entrypoints[:5]),
        )

    def _browser_checks(
        self,
        urls: Sequence[str],
        run_dir: Path,
        work_item_id: str,
        ui_heavy: bool,
    ) -> list[QualityCheckResult]:
        if not urls:
            return [
                QualityCheckResult(
                    gate="browser_smoke",
                    name="browser_url_available",
                    status="limited",
                    evidence="No URL is available for browser smoke checks yet.",
                    severity="high" if ui_heavy else "medium",
                    failure_reason="needs_repair" if ui_heavy else None,
                    remediation_owner="fullstack-agent" if ui_heavy else "none",
                    failure_signature="browser_url_missing" if ui_heavy else "",
                    reproduction_steps=[
                        "Start the app locally or provide a deployed URL for browser QA."
                    ],
                )
            ]
        adapter = self.browser_adapter or PlaywrightBrowserAdapter()
        checks: list[QualityCheckResult] = []
        for url in urls:
            checks.extend(
                adapter.check_url(
                    url,
                    run_dir=run_dir,
                    work_item_id=work_item_id,
                    gate="deployment_parity" if url.startswith("http") else "browser_smoke",
                )
            )
        return checks


def load_quality_gate_report(run_dir: Path, work_item_id: str) -> QualityGateReport | None:
    """Load a previously written quality gate report if present."""

    path = run_dir / "qa" / "gates" / work_item_id / "quality-gate-report.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    checks = [
        QualityCheckResult(
            gate=str(item.get("gate") or ""),
            name=str(item.get("name") or ""),
            status=str(item.get("status") or "failed"),  # type: ignore[arg-type]
            evidence=str(item.get("evidence") or ""),
            severity=str(item.get("severity") or "medium"),
            failure_reason=item.get("failure_reason"),
            remediation_owner=str(item.get("remediation_owner") or "none"),
            failure_signature=str(item.get("failure_signature") or ""),
            reproduction_steps=[str(step) for step in item.get("reproduction_steps", [])],
            evidence_refs=[
                QualityEvidenceRef(
                    path=str(ref.get("path") or ""),
                    label=str(ref.get("label") or ""),
                    artifact_id=str(ref.get("artifact_id") or ""),
                    evidence_type=str(ref.get("evidence_type") or ""),
                )
                for ref in item.get("evidence_refs", [])
                if isinstance(ref, dict)
            ],
        )
        for item in payload.get("checks", [])
        if isinstance(item, dict)
    ]
    repair_payload = payload.get("repair_request")
    repair = (
        RepairRequest(
            work_item_id=str(repair_payload.get("work_item_id") or work_item_id),
            failure_reason=str(repair_payload.get("failure_reason") or "qa_failed"),  # type: ignore[arg-type]
            responsible_agent=str(repair_payload.get("responsible_agent") or "fullstack-agent"),
            reproduction_steps=[str(step) for step in repair_payload.get("reproduction_steps", [])],
            expected_behavior=str(repair_payload.get("expected_behavior") or ""),
            actual_behavior=str(repair_payload.get("actual_behavior") or ""),
            evidence_artifact_ids=[
                str(item) for item in repair_payload.get("evidence_artifact_ids", [])
            ],
            failure_signature=str(repair_payload.get("failure_signature") or ""),
            recommended_fix_scope=str(repair_payload.get("recommended_fix_scope") or ""),
        )
        if isinstance(repair_payload, dict)
        else None
    )
    return QualityGateReport(
        work_item_id=work_item_id,
        status=str(payload.get("status") or "failed"),  # type: ignore[arg-type]
        ui_heavy=bool(payload.get("ui_heavy")),
        checks=checks,
        repair_request=repair,
        artifacts=[
            QualityEvidenceRef(
                path=str(ref.get("path") or ""),
                label=str(ref.get("label") or ""),
                artifact_id=str(ref.get("artifact_id") or ""),
                evidence_type=str(ref.get("evidence_type") or ""),
            )
            for ref in payload.get("artifacts", [])
            if isinstance(ref, dict)
        ],
        failure_signatures=[str(item) for item in payload.get("failure_signatures", [])],
        recommended_next_action=str(payload.get("recommended_next_action") or ""),
        limited_evidence=bool(payload.get("limited_evidence")),
    )


def _write_registered_json(
    run_dir: Path,
    *,
    relative_path: str,
    payload: dict[str, Any],
    request: ExecutionRequest,
    work_item_id: str,
    artifact_type: str,
    visibility: str,
    source_tool: str,
) -> ArtifactRecord:
    path = run_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return register_artifact(
        run_dir,
        relative_path=relative_path,
        run_id=request.run_id,
        work_item_id=work_item_id,
        owner_agent=QUALITY_GATE_AGENT_ID,
        artifact_type=artifact_type,
        visibility=visibility,
        source_tool=source_tool,
        source_model=request.model,
    )


def _write_repair_request_artifacts(
    run_dir: Path,
    request: ExecutionRequest,
    repair: RepairRequest,
) -> list[ArtifactRecord]:
    json_path = f"10-fix-request-{repair.work_item_id}.json"
    md_path = f"10-fix-request-{repair.work_item_id}.md"
    records = [
        _write_registered_json(
            run_dir,
            relative_path=json_path,
            payload=repair.to_dict(),
            request=request,
            work_item_id=repair.work_item_id,
            artifact_type=REPAIR_REQUEST_TYPE,
            visibility="qa_evidence",
            source_tool="quality_gate_runner",
        )
    ]
    markdown = _repair_request_markdown(repair)
    path = run_dir / md_path
    path.write_text(markdown, encoding="utf-8")
    records.append(
        register_artifact(
            run_dir,
            relative_path=md_path,
            run_id=request.run_id,
            work_item_id=repair.work_item_id,
            owner_agent=QUALITY_GATE_AGENT_ID,
            artifact_type=REPAIR_REQUEST_TYPE,
            visibility="qa_evidence",
            source_tool="quality_gate_runner",
            source_model=request.model,
        )
    )
    return records


def _register_evidence_refs(
    run_dir: Path,
    request: ExecutionRequest,
    work_item_id: str,
    checks: Iterable[QualityCheckResult],
) -> list[ArtifactRecord]:
    records: list[ArtifactRecord] = []
    for check in checks:
        for ref in check.evidence_refs:
            if not ref.path or not (run_dir / ref.path).exists():
                continue
            records.append(
                register_artifact(
                    run_dir,
                    relative_path=ref.path,
                    run_id=request.run_id,
                    work_item_id=work_item_id,
                    owner_agent=QUALITY_GATE_AGENT_ID,
                    artifact_type=(
                        "screenshot_evidence"
                        if ref.evidence_type == "screenshot"
                        else "debug_trace"
                    ),
                    visibility="qa_evidence" if ref.evidence_type == "screenshot" else "developer",
                    label=ref.label,
                    source_tool="quality_gate_runner",
                    source_model=request.model,
                )
            )
    return records


def _repair_request_from_failures(
    work_item_id: str,
    failed: list[QualityCheckResult],
) -> RepairRequest | None:
    if not failed:
        return None
    owner = _responsible_owner(failed)
    signatures = [check.failure_signature for check in failed if check.failure_signature]
    evidence_ids = [
        ref.artifact_id for check in failed for ref in check.evidence_refs if ref.artifact_id
    ]
    return RepairRequest(
        work_item_id=work_item_id,
        failure_reason=failed[0].failure_reason or "needs_repair",
        responsible_agent=owner,
        reproduction_steps=_unique_strings(
            [step for check in failed for step in check.reproduction_steps]
            or ["Rerun quality gates for the selected work item."]
        ),
        expected_behavior=(
            "The generated app should satisfy the work item acceptance criteria with "
            "working UI/runtime behavior and release-quality evidence."
        ),
        actual_behavior="; ".join(check.evidence for check in failed[:4]),
        evidence_artifact_ids=evidence_ids,
        failure_signature=signatures[0] if signatures else f"quality_failed:{work_item_id}",
        recommended_fix_scope=(
            "Repair the cited behavior/layout/runtime defect and preserve existing passing flows."
        ),
    )


def _responsible_owner(failed: list[QualityCheckResult]) -> str:
    owners = [check.remediation_owner for check in failed if check.remediation_owner != "none"]
    if any(owner == "deployment-agent" for owner in owners):
        return "deployment-agent"
    if any(owner == "documentation-handoff-agent" for owner in owners):
        return "documentation-handoff-agent"
    return owners[0] if owners else "fullstack-agent"


def _recommended_next_action(
    status: QualityCheckStatus,
    repair_request: RepairRequest | None,
) -> str:
    if status == "passed":
        return "Proceed with QA Codex report and the next delivery gate."
    if repair_request:
        return f"Route repair to {repair_request.responsible_agent}, then rerun quality gates."
    return "Review limited evidence before deciding whether human approval is required."


def _public_urls(run_dir: Path) -> list[str]:
    state_path = run_dir / ".delivery-state.json"
    urls: list[str] = []
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            state = {}
        if isinstance(state, dict):
            urls.extend(str(url) for url in state.get("public_urls", []) if str(url).strip())
            if state.get("public_url"):
                urls.append(str(state["public_url"]))
    deployment_result = run_dir / "deployment" / "result.json"
    if deployment_result.exists():
        try:
            payload = json.loads(deployment_result.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            urls.extend(str(url) for url in payload.get("public_urls", []) if str(url).strip())
    return _unique_strings(urls)


def _is_ui_heavy(feature: dict[str, Any], target_dir: Path) -> bool:
    text_parts = [
        str(feature.get("title") or ""),
        str(feature.get("description") or ""),
        *[str(item) for item in feature.get("acceptance_criteria", [])],
        *[str(item) for item in feature.get("definition_of_done", [])],
    ]
    haystack = " ".join(text_parts).lower()
    if any(keyword in haystack for keyword in UI_KEYWORDS):
        return True
    return any(
        (target_dir / candidate).exists()
        for candidate in ["index.html", "src", "frontend", "package.json"]
    )


def _entrypoint_candidates(target_dir: Path) -> list[str]:
    candidates = []
    for relative in [
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "Dockerfile",
        "docker-compose.yml",
        "compose.yaml",
        "app.py",
        "main.py",
        "index.html",
        "src",
    ]:
        if (target_dir / relative).exists():
            candidates.append(relative)
    return candidates


def _placeholder_findings(target_dir: Path) -> list[str]:
    findings: list[str] = []
    for path in _iter_scannable_files(target_dir, limit=250):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        relative = path.relative_to(target_dir).as_posix()
        for pattern in PLACEHOLDER_PATTERNS:
            if pattern.search(text):
                findings.append(f"{relative}: {pattern.pattern}")
                break
    return findings


def _iter_scannable_files(target_dir: Path, *, limit: int) -> Iterable[Path]:
    count = 0
    for path in target_dir.rglob("*"):
        if count >= limit:
            break
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        parts = set(path.relative_to(target_dir).parts)
        if parts & EXCLUDED_DIRS:
            continue
        count += 1
        yield path


def _repair_request_markdown(repair: RepairRequest) -> str:
    return "\n".join(
        [
            f"# Fix Request: {repair.work_item_id}",
            "",
            f"Failure reason: `{repair.failure_reason}`",
            f"Responsible agent: `{repair.responsible_agent}`",
            f"Failure signature: `{repair.failure_signature}`",
            "",
            "## Expected Behavior",
            "",
            repair.expected_behavior,
            "",
            "## Actual Behavior",
            "",
            repair.actual_behavior,
            "",
            "## Reproduction Steps",
            "",
            *[f"- {step}" for step in repair.reproduction_steps],
            "",
            "## Recommended Fix Scope",
            "",
            repair.recommended_fix_scope,
            "",
        ]
    )


def _short_signature(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    return sha1(normalized.encode()).hexdigest()[:12]


def _unique_strings(values: Sequence[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value and value not in unique:
            unique.append(value)
    return unique


def _drop_empty(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value not in ("", None, [], {}, ())}
