# Contributing

This repository holds reusable company-level agent definitions, workflows, schemas, templates, and orchestration primitives.

## Working Principles

- Keep client-specific delivery work out of this repo.
- Prefer explicit contracts over loose prompt prose.
- Update human-readable docs and machine-readable config together.
- Keep orchestration code small until a workflow has proven it needs more machinery.

## Local Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest
```

## Agent Changes

When changing an agent, update both files in that agent folder:

- `README.md` for human review
- `agent.yaml` for future orchestration loading

The two files should agree on responsibilities, inputs, outputs, escalation rules, and handoff expectations.
