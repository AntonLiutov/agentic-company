# 02 Workstreams And Tasks

## Workstream A: Repository Cleanup

Goal: make the repo safe to move, archive, and deploy without local noise.

Tasks:

| ID | Task | Owner | Acceptance |
| --- | --- | --- | --- |
| DVR-A1 | Audit root, `runs/`, generated outputs, logs, coverage, screenshots, and temporary files | Developer | List of keep/delete/ignore candidates is written |
| DVR-A2 | Update `.gitignore` for local run outputs and generated demo evidence | Developer | New local artifacts do not appear in `git status` after a run |
| DVR-A3 | Remove irrelevant tracked files if any were accidentally committed | Developer | Working tree contains only source/docs/config intended for repo |
| DVR-A4 | Add repo movement notes | Developer | VM setup doc references exact clone, env, install, and run steps |

## Workstream B: VM Preparation

Goal: make the platform runnable on a clean VM.

Tasks:

| ID | Task | Owner | Acceptance |
| --- | --- | --- | --- |
| DVR-B1 | Define VM baseline requirements | DevOps | OS, CPU/RAM/disk, open ports, Docker, Node, Python, uv, Azure CLI documented |
| DVR-B2 | Add VM bootstrap checklist or script | DevOps | Steps are copy-pasteable and ordered |
| DVR-B3 | Document secrets/env transfer | DevOps | Required env vars listed with where to put them and what not to commit |
| DVR-B4 | Run smoke test on VM | DevOps | Health checks and console startup evidence captured |

## Workstream C: Codex CLI Setup

Goal: remove workstation/VS Code dependency for Codex usage.

Tasks:

| ID | Task | Owner | Acceptance |
| --- | --- | --- | --- |
| DVR-C1 | Document npm Codex install | DevOps | `npm install -g @openai/codex` path is documented |
| DVR-C2 | Document API-key auth | DevOps | `OPENAI_API_KEY` stdin login path is documented |
| DVR-C3 | Add startup/preflight check for Codex | Developer | Failure message says exactly how to install/login |
| DVR-C4 | Test `codex exec` on VM | DevOps | Minimal non-interactive command succeeds and logs version/session evidence |

## Workstream D: VM Run Validation

Goal: prove the same flow runs outside the development workstation.

Tasks:

| ID | Task | Owner | Acceptance |
| --- | --- | --- | --- |
| DVR-D1 | Run full platform tests on VM | Developer | `ruff`, format check, and `pytest` results captured |
| DVR-D2 | Run one end-to-end demo request | Developer | Run id, timings, deployment URL, QA status, and handoff refs captured |
| DVR-D3 | Collect VM evidence | Developer | Logs, screenshots, env redaction note, and final report are organized |
| DVR-D4 | Write VM validation report | Reporter | Report says what worked, what failed, and what remains |

## Workstream E: Product Console Web App

Goal: plan the real web app that replaces the current Streamlit console.

Tasks:

| ID | Task | Owner | Acceptance |
| --- | --- | --- | --- |
| DVR-E1 | Define console information architecture | UX | Left nav, center timeline/chat, right board/artifacts, debug mode specified |
| DVR-E2 | Capture current console pain points | UX | Status refresh, artifact preview, logs, board, handoff, and deployment notes covered |
| DVR-E3 | Create UI implementation brief | UX/Developer | Brief is actionable for Next.js/FastAPI or equivalent implementation |
| DVR-E4 | Prepare screenshot targets | UX | List of screenshots and desired states is ready |

## Workstream F: Presentation And Pitch

Goal: make the success understandable in 3-5 minutes.

Tasks:

| ID | Task | Owner | Acceptance |
| --- | --- | --- | --- |
| DVR-F1 | Write demo story | Reporter | Problem, flow, moment of value, and outcome are clear |
| DVR-F2 | Prepare slide outline | Reporter | 6-8 slide outline ready |
| DVR-F3 | Prepare video script | Reporter | 2-3 minute narration with screen sequence ready |
| DVR-F4 | Prepare pitch notes | Reporter | Short pitch, technical proof points, and risks/follow-ups ready |

## Recommended Order

1. Clean repo and update ignores.
2. Document and test Codex npm/API-key auth.
3. Prepare VM and run tests.
4. Run one E2E on VM and collect evidence.
5. Capture screenshots.
6. Prepare presentation and video materials.
7. Start web console implementation in a separate branch if time remains.

