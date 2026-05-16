# 01 Milestone Charter

## Problem

The platform now has a successful end-to-end proof: planning, implementation, QA, deployment,
post-deploy QA, and handoff can complete. The next risk is not agent capability. The next risk is
demo reliability and portability.

The repo still carries local run artifacts, the console is developer-oriented, VM setup is not fully
codified, Codex setup is still assumed from a local workstation, and the pitch assets are not yet
packaged.

## Objective

Prepare the platform for a clean VM-hosted demo and a strong presentation.

## Scope

In scope:

- Repo cleanup for irrelevant generated outputs, temporary files, old failed run leftovers, and
  accidental local-only artifacts.
- `.gitignore` and docs updates to prevent future artifact churn.
- VM setup checklist and scripts where useful.
- Codex CLI setup through npm and API-key auth.
- VM validation run with recorded evidence.
- Screenshot plan and artifact organization.
- Product-console web app plan to replace the current Streamlit console.
- Presentation deck outline, video script, demo script, notes, and pitch.

Out of scope:

- Rewriting orchestration architecture.
- Adding a new multi-agent design.
- Building multi-tenant production auth.
- Self-hosting LangSmith.
- Perfect cloud cost optimization.
- Creating complex enterprise CI/CD.

## Success Criteria

- A new developer or VM can follow documented steps to run the platform.
- Codex can be installed and authenticated without VS Code UI dependency.
- VM run evidence proves the platform works away from the local laptop.
- Demo assets are organized under docs and/or a clearly named artifacts folder.
- The next UI direction is clear enough for implementation: left navigation, central live timeline,
  right board/artifact panel, debug mode.
- The pitch explains the value in business language, not only technical internals.

## Quality Bar

This milestone should reduce noise.

Every added document, script, or checklist must answer one of these questions:

- Can we move this to a VM?
- Can we run it there?
- Can we show it well?
- Can we explain why it matters?
- Can we avoid reintroducing local artifact mess?

