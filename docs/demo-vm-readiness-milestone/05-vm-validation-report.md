# VM Validation Report

## Summary

Workstream D is accepted as complete for the current demo branch and VM. The VM
run completed the platform flow end to end, including deployment, post-deploy QA,
and final Head completion. No new paid full E2E rerun is required for this
milestone unless the application or deployment architecture changes materially.

## Accepted Evidence

- Run id: `console-20260517-173927`
- VM quality checks: `ruff check`, `ruff format --check`, and `pytest` passed
- Final platform status: `head_delivery_completed`
- Deployment status: `deployed`
- Post-deploy QA status: `passed`
- Blockers: none recorded in the final delivery state
- Screenshots and browser evidence: captured under VM-local QA artifacts
- Handoff evidence: sprint handoff refs, deployment refs, reports, and evidence
  manifests captured in the VM-local ignored run artifacts

The working app URL and Azure resource identifiers are intentionally not written
to committed docs. They are available in Azure and in VM-local run evidence.

## Workstream D Checklist

| ID | Acceptance | Status |
| --- | --- | --- |
| DVR-D1 | `ruff`, format check, and `pytest` results captured | Complete |
| DVR-D2 | Run id, deployment status, QA status, and handoff refs captured | Complete |
| DVR-D3 | Logs, screenshots, redaction note, and report evidence organized | Complete |
| DVR-D4 | VM validation report says what worked, what failed, and what remains | Complete |

## Residual Risks

- The live app responded successfully during validation, but Azure revision
  metadata/logs showed a SQLite database-lock startup issue on one revision.
- The current SQLite plus mounted storage path is acceptable for this demo, but
  PostgreSQL is the recommended durable shared persistence path before treating
  the pattern as production-ready.
- Future validation should repeat the full E2E only after meaningful platform,
  deployment, or persistence changes.
