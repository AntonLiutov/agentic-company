# Codex NPM VM Smoke Test

This folder validates that a VM can run Codex CLI through npm without relying on
the VS Code extension binary or any user-level Codex login/config.

The Azure VM target for this work is **Ubuntu 24.04 LTS**. The Linux script is
the VM baseline. The PowerShell script exists for local Windows validation and
parity while developing on this workstation.

The smoke test installs `@openai/codex` locally, loads `CODEX_API_KEY` from
`.env`, runs with web search enabled, and writes:

- `outputs/summary.md` with a short project summary;
- `outputs/milan-weather.md` with today's Milan weather and exact date.

Codex returns the content as structured JSON. The wrapper writes the final files
locally, which keeps the smoke test compatible with strict Codex sandbox
policies.

## Files

```text
ops/codex-npm-smoke/
  README.md
  .env.example
  .gitignore
  smoke-prompt.md
  smoke-output.schema.json
  run-codex-npm-smoke.ps1
  run-codex-npm-smoke.sh
```

Generated files stay local and are ignored:

```text
.env
.codex-home/
.tools/
.npm-cache/
.codex-npm/
outputs/
```

## Environment

Create `.env` in this folder:

```env
CODEX_API_KEY=<your-codex-api-key>
```

Only `CODEX_API_KEY` is supported. It must be set explicitly. The scripts do not
accept legacy aliases, do not copy from any other environment variable at
runtime, and never print the value.

## Windows

From this folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run-codex-npm-smoke.ps1
```

## Linux

This is the preferred path for the Azure VM.

From this folder:

```bash
chmod +x ./run-codex-npm-smoke.sh
./run-codex-npm-smoke.sh
```

## Defaults

- Model: `gpt-5.3-codex`
- Reasoning effort: `medium`
- Service tier/speed: `fast`
- Sandbox: `workspace-write`
- Approval policy: `never`
- Internet/search: mandatory through Codex `--search`
- Codex home: isolated local `.codex-home/`

If `node` and `npm` are missing, the scripts download official Node.js LTS into
`.tools/node/`. Codex is installed locally into `.codex-npm/`, and the run uses
only that npm-installed binary.

On Linux, the script also verifies Codex sandbox prerequisites. If `bubblewrap`
is missing on an apt-based VM, it installs `bubblewrap`, `apparmor-utils`, and
`apparmor-profiles`, then loads the Ubuntu AppArmor profile needed for
restricted user namespaces when that profile is available. This follows the
Codex sandbox guidance for Linux and Ubuntu 24.04.

The scripts use `codex exec -` so the prompt is passed through stdin, `--json`
for event streaming, `--output-schema` for machine-readable final output,
`--ignore-user-config` and an isolated `CODEX_HOME` for VM reproducibility, and
`--ephemeral` to avoid persisting interactive session state. They also pass
`--skip-git-repo-check` so the same smoke test works when the repository is
copied to a VM as a deployment archive without a `.git/` directory.

Current Codex CLI versions may still print plugin-sync warnings when running
with API-key auth because remote plugin sync expects ChatGPT auth. Those warnings
are non-fatal for this smoke test; success is determined by the exit code plus
the required output files. Sandbox/tool failures such as `bwrap` permission
errors are fatal even if Codex returns a structured JSON response.
