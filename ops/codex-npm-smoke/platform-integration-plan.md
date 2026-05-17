# Platform Codex Execution Migration Plan

This smoke test is the baseline for moving Agentic Company Codex execution to an
Azure VM-friendly path.

Target VM OS: **Ubuntu 24.04 LTS**. The Linux script should be treated as the
deployment baseline; the PowerShell script is for local Windows validation only.

## Findings From Official Codex Docs

- Use `codex exec` for automation and CI-style non-interactive runs.
- Use `--json` to stream machine-readable events and `--output-last-message` for
  the final agent message.
- Use `--output-schema` when downstream platform code needs stable fields.
- Use `CODEX_API_KEY` for `codex exec` API-key automation. VM/runtime
  configuration should set `CODEX_API_KEY` explicitly. Do not rely on aliases or
  runner-side fallback from any other environment variable.
- Use explicit sandbox and approval settings. For deterministic automation, do
  not rely on user-level `~/.codex/config.toml`.
- Use `--search` when live internet access is part of the contract.
- Current Codex CLI builds may emit non-fatal plugin-sync warnings under
  API-key auth because remote plugin sync expects ChatGPT auth. Platform
  readiness should be based on process exit code, JSONL events, final message,
  and required artifacts, not on absence of stderr warnings.

References:

- https://developers.openai.com/codex/noninteractive
- https://developers.openai.com/codex/cli/reference
- https://developers.openai.com/codex/config-reference
- https://developers.openai.com/codex/concepts/sandboxing

## Migration Direction

Replace ad hoc platform Codex subprocess setup with one shared execution layer:

1. Add a VM-safe Codex bootstrap/provider that can resolve either a configured
   binary or a local npm-installed `@openai/codex` binary.
2. Add a `CodexExecutionSpec`-style contract for model, reasoning effort,
   sandbox, approval policy, working directory, writable roots, search mode,
   output schema, timeout, and artifact paths.
3. Build commands with `codex exec -`, not multiline prompt arguments.
4. Always pass prompt text through stdin.
5. Always capture JSONL events and final message separately.
6. Require explicit `CODEX_API_KEY` in the VM/runtime environment.
7. Set a run-local or VM-local `CODEX_HOME` so platform agents do not inherit
   developer plugins, skills, auth state, or config.
8. Prefer structured outputs for platform contracts. Let Codex reason and return
   contract JSON; let platform code write final canonical artifacts when the
   artifact is a platform-owned contract.
9. Use direct Codex file writes only for true code-generation/editing tasks, and
   then make writable roots explicit with `--cd` and repeated `--add-dir`.
10. Keep `danger-full-access` only for isolated VM/runner jobs that truly need
    Docker, Azure CLI, dependency installs, or deployment-side effects.

## Platform Changes To Make Next

- Update `src/agentic_company/integrations/codex/runner.py` first; do not patch
  individual agents separately.
- Add command-builder tests for:
  - stdin prompt mode,
  - explicit `CODEX_API_KEY` requirement,
  - isolated `CODEX_HOME`,
  - `--search` when enabled,
  - structured output schema support,
  - explicit writable roots.
- Add one integration smoke that uses the npm Codex path from this folder before
  moving the platform to Azure VM.
- Run the Linux smoke on an Ubuntu 24.04 LTS Azure VM before replacing the
  production platform runner.
- After the shared runner is stable, migrate Fullstack, QA, Deployment, Handoff,
  BA, Architect, PM, and status inspection to the shared contract.
