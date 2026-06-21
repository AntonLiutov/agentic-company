# CD: self-hosted runner on the VM

Auto-deploy the console to the Azure VM on merge to `main`. Chosen over cloud-runner+SSH
and Azure DevOps because the VM's NSG IP-gates port 22 — a runner **on** the VM is
outbound-only (HTTPS:443 long-poll), so the NSG and SSH are irrelevant. dev auto-deploys;
**prod is gated behind a manual, reviewed promotion**.

## Layout on the VM

```
/opt/agentic-company/
  releases/<sha>/      immutable checkout + its own uv .venv
  current  -> releases/<sha>     atomic symlink = live version
  previous -> releases/<sha>     last-known-good (auto-rollback target)
  shared/.env                    prod secrets — symlinked into each release, deploy NEVER writes it
  shared/data/codex-auth/        per-user Codex auth.json, persisted across releases
  shared/runs/                   run artifacts, persisted
  shared/backups/                pre-migration pg_dumps
```

- A dedicated unprivileged **`deployer`** user owns the tree and runs the GitHub Actions
  runner as a boot-persistent systemd service. Its sudoers grant is exactly three commands:
  `systemctl restart|is-active|status agentic-company-console`.
- `agentic-company-console.service` loads `current/.env` (→ `shared/.env`) + `current/.release.env`
  (the per-release sha), so the live env is always the host-local prod secret file.
- nginx (TLS) → `127.0.0.1:$AGENTIC_WEB_PORT` is unchanged.

## Flow

`merge to main` → **CI** (ruff + pytest, cov ≥ 75) → on success **Deploy (dev)** fires via
`workflow_run` (guarded `conclusion==success && head_branch==main`, fork runs off) → the
job lands on the `vm-dev` runner → `ops/deploy/deploy.sh`:
checkout → rsync into `releases/<sha>` → symlink `shared/*` → `uv sync --extra app` →
`pg_dump` backup → `agentic-db-upgrade` → atomic `current` flip → `sudo systemctl restart`
→ poll `/healthz` until it reports the new sha → on failure repoint `current`→`previous`,
restart, exit red (the merge author sees the failure).

## One-time setup (per host)

```bash
sudo REPO_URL=https://github.com/AntonLiutov/agentic-company bash ops/deploy/bootstrap_vm.sh
# then follow the printed MANUAL steps:
#  1. fill /opt/agentic-company/shared/.env  (from shared-env.example)
#  2. register the runner as `deployer` with label vm-dev (vm-staging / vm-prod elsewhere)
#  3. repo Actions settings: disable fork-PR runs; require approval for outside collaborators
#  4. repo Environments: create `dev` (no gate) and `prod` (required reviewers)
```

## Rollback

Automatic on a failed `/healthz` (deploy repoints `current`→`previous` and restarts).
Manual:
```bash
ln -sfn "$(readlink -f /opt/agentic-company/previous)" /opt/agentic-company/current
sudo systemctl restart agentic-company-console
```

## Promotion to staging / prod

The per-host delta is exactly TWO things — the runner **label** and the host-local
`shared/.env`; everything else (workflows, deploy.sh, bootstrap, unit, sudoers, layout)
is identical.

- **Staging:** 2nd VM → `bootstrap_vm.sh` → own `shared/.env` (staging OAuth app + staging
  DNS callback + fresh `APP_SECRET_KEY`) → runner label `vm-staging`. Add a matrix entry to
  `deploy.yml` to auto-deploy it alongside dev.
- **Prod:** same, label `vm-prod`. Promote with the **`Deploy (prod)`** workflow
  (`workflow_dispatch`), which is gated by the `prod` Environment's required reviewers — prod
  is never auto-flipped on a merge.

Each environment needs its OWN GitHub OAuth app (one callback URL per app) and its own
`APP_SECRET_KEY`, so token ciphertexts stay isolated per host.

## Prereqs before turning CD on

1. **Land the work on `main`** — CD deploys from main.
2. **The drop migration `20260621_0006`** runs against prod Postgres and is irreversible.
   deploy.sh takes a `pg_dump` first; prefer reworking destructive migrations to
   expand-contract (add+backfill in one release, drop in a later one).
3. CI must be **green** on main.

## Rejected alternatives (for the record)

- **Cloud GitHub runner → SSH/rsync to the VM:** needs to punch the NSG (dynamic runner IPs)
  via an Azure OIDC app + JIT NSG rule whose teardown can leak on cancel; plus an SSH key in
  GitHub secrets. More moving parts, more secret surface. Kept only as the migration path if
  the VM is ever replaced by an ephemeral/autoscaled fleet.
- **Azure DevOps Pipelines:** not worth migrating off GitHub when the repo, OAuth, and board
  integrations already live there.
- **Secrets via `az vm run-command`:** forbidden — its scripts/output land in Azure logs.
