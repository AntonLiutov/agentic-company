# QA browser runtime

The QA worker captures full-page screenshots with a repo-local Playwright +
Chromium so it does not depend on a flaky host browser. This directory holds that
install (`node_modules/`, `browsers/`) — both are git-ignored.

## Setup (one command)

On the machine or VM that runs delivery, from the repo root:

```bash
uv run --extra app agentic-qa-setup
```

This installs the `playwright` package and the Chromium build into this folder,
verifies it with a real headless screenshot, and appends the two paths to `.env`:

```
PLAYWRIGHT_BROWSERS_PATH=ops/qa-runtime/browsers
NODE_PATH=ops/qa-runtime/node_modules
```

Restart the web console afterwards so it picks up `.env`. Re-running is safe
(idempotent). Requires Node.js (which ships `npm`) on PATH; on Linux the script
also pulls Chromium's system libraries via `--with-deps`.

## How the worker uses it

The Codex env allowlist passes `PLAYWRIGHT_*` and `NODE_PATH` into the QA
subprocess, so the worker can `require('playwright')` from any working directory
and find the pre-installed Chromium. See the `browser-smoke-qa` skill for the
launch flags and screenshot recipe.
