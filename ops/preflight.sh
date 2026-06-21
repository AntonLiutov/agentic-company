#!/usr/bin/env bash
# Preflight — run BEFORE every git push / PR so we never trip on CI.
# Mirrors .github/workflows/ci.yml exactly:  bash ops/preflight.sh
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
fail=0

echo "== ruff format --check . =="
uv run ruff format --check . || { echo "  FIX: uv run ruff format ."; fail=1; }

echo "== ruff check . =="
uv run ruff check . || { echo "  FIX: uv run ruff check --fix ."; fail=1; }

echo "== pytest --cov-fail-under=75 =="
AGENTIC_CODEX_SANDBOX_OVERRIDE='' uv run pytest --cov=agentic_company --cov-report=term-missing --cov-fail-under=75 -q || fail=1

if [ "$fail" = 0 ]; then
  echo "PREFLIGHT OK - safe to push"
else
  echo "PREFLIGHT FAILED - fix the above before pushing"
  exit 1
fi
