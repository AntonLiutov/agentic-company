# CD + production secret/DB management

Auto-deploy the console to an Azure VM on merge to `main`, with secrets in **Azure Key
Vault** (read via the VM's **Managed Identity**) and a **managed Postgres Flexible
Server**. dev auto-deploys; **prod is a manual, reviewed promotion**. Reproducible per
env via **Bicep**. Chosen because a runner ON the VM is outbound-only (sidesteps the NSG
SSH IP-gate) and Key Vault + a managed DB take secrets and durable state off the VM disk.

## Pieces

- **Secrets:** one Key Vault per env (`kv-agentic-<env>`). The VM's system-assigned
  Managed Identity has `Key Vault Secrets User`. A oneshot `agentic-secrets-fetch.service`
  runs `Before=` the console, does `az login --identity`, and renders
  `/opt/agentic-company/shared/.env` (0600) from the vault. The app is UNCHANGED — it
  still reads env vars. `deploy.sh` never writes `shared/.env`; only the oneshot does.
  Secrets in the vault: `app-secret-key` (Fernet ROOT key — **immutable per env**),
  `github-oauth-client-id/secret`, `db-app-password`. Non-secret config is the committed
  `ops/deploy/env.nonsecret.<env>`.
- **DB:** Azure Database for PostgreSQL Flexible Server per env, public-access firewalled
  to the VM IP, `sslmode=require`, 7-day managed backups + PITR. Redis stays docker ($0).
- **IaC:** `infra/bicep/` (`main.bicep` → `modules/keyvault.bicep` + `modules/postgres.bicep`),
  per-env `params/<env>.bicepparam`, driven by `infra/provision.sh`.
- **Release layout on the VM:** `/opt/agentic-company/{releases/<sha>, current→release,
  previous→release, shared/{.env, bin/fetch-secrets.sh, env.nonsecret, data/codex-auth,
  runs, backups}}`, owned by an unprivileged `deployer` user with a 4-command sudoers grant.

## Before you provision (operator gotchas — from the Azure vet)

- **Pin the VM's public IP to STATIC.** The Postgres firewall is a single-IP allow rule
  (the VM's IP); a dynamic IP that changes on dealloc/restart silently breaks DB access.
  The firewall uses the VM's instance-level **public** IP — valid only if the VM has one
  and uses default outbound. Behind a NAT Gateway / LB the egress IP differs; switch to
  VNet integration / private endpoint then.
- **NSG outbound must allow 443** to `*.vault.azure.net` (secret fetch),
  `login.microsoftonline.com` + IMDS `169.254.169.254` (managed-identity token), and the
  Postgres FQDN:5432. The NSG today only IP-gates inbound SSH; confirm outbound is open.
- **First secret fetch waits on RBAC propagation.** The `Key Vault Secrets User` grant can
  take ~1–5 min to reach the data plane; `fetch-secrets.sh` retries for up to 5 min, so the
  first `bootstrap_vm.sh` may pause there — that's expected, not a hang.
- **Vault/Postgres names carry a unique suffix** (`uniqueString` per RG) to avoid global
  name collisions — so the real names come from `provision.sh` output; put them into
  `env.nonsecret.<env>` (which ships `__SET_FROM_PROVISION_OUTPUT__` sentinels that fail
  loudly if left unedited).
- **`provision.sh` is idempotent** via `infra/.provision-state/<env>.dbpw` (gitignored) — a
  re-run reuses the same DB password instead of rotating the live credential.

## First-time setup (per env, from scratch)

```bash
# 1. From your machine (az login + az account set --subscription <id>):
#    creates Key Vault + role assignment + Postgres, seeds secrets (prompts for the
#    env's GitHub OAuth client id/secret — create a fresh OAuth app first, callback =
#    https://<env-dns>/auth/github/callback).
infra/provision.sh dev <resource-group> <vm-name> [vm-resource-group]

# 2. Put the printed vault + Postgres FQDN into ops/deploy/env.nonsecret.dev
#    (AGENTIC_KEY_VAULT, AGENTIC_PG_HOST, GITHUB_OAUTH_CALLBACK_URL) and commit it.

# 3. On the VM (renders shared/.env from Key Vault and verifies):
sudo ENV_NAME=dev bash ops/deploy/bootstrap_vm.sh

# 4. Register the self-hosted runner as `deployer`, label vm-dev (printed by bootstrap).
# 5. Repo settings: disable fork-PR runs; create Environments dev (no gate) / prod (required reviewers).
# 6. Land CD on main; trigger the FIRST deploy manually (workflow_run gotcha below); then merges auto-deploy.
```

## Flow

`merge to main` → **CI** (ruff + pytest) → on success **Deploy (dev)** fires via
`workflow_run` → the `vm-dev` runner runs `deploy.sh`: re-render secrets from Key Vault →
rsync into `releases/<sha>` → symlink `shared/*` → `uv sync` → best-effort `pg_dump`
(managed PITR is the real safety) → `agentic-db-upgrade` → atomic `current` flip →
restart → poll `/healthz` 90s for the new sha → on failure roll back to `previous`.

## Gotchas (built into the runbook, repeated here)

- **workflow_run only fires from the default branch.** The merge that introduces
  `deploy.yml` to main does NOT auto-deploy itself — trigger the first deploy manually
  (re-run CI on main / push a trivial commit). Subsequent merges auto-deploy.
- **Fork-PR safety** relies on the repo setting *and* the workflow `if:` guard — set both.
- **Migrations must be EXPAND-CONTRACT** (additive in a release, drops in a later one) so a
  code rollback stays compatible with the advanced schema. The legacy drop migration
  `20260621_0006` is fine on a clean DB; rework it before it runs against data.
- **`APP_SECRET_KEY` is immutable per env.** Changing it orphans every per-user secret;
  "rotation" = a deliberate app-level re-encrypt migration, not an ops swap.

## Rotation

- **GitHub OAuth secret** (zero-downtime — GitHub allows two active secrets): add secret #2
  in GitHub → `az keyvault secret set --name github-oauth-client-secret` → on the VM
  `sudo systemctl restart agentic-secrets-fetch agentic-company-console` → delete the old
  GitHub secret.
- **DB password:** rotate on the server, `az keyvault secret set --name db-app-password`,
  restart the two services.

## Rollback

Automatic on a failed `/healthz` (repoints `current`→`previous`, restarts). Manual:
`ln -sfn "$(readlink -f /opt/agentic-company/previous)" /opt/agentic-company/current && sudo systemctl restart agentic-company-console`.
For a bad schema change, restore the managed Postgres via PITR.

## Promotion to staging / prod

Per-env delta = the runner label + `env.nonsecret.<env>` + that env's Bicep param +
its OWN vault + OWN `APP_SECRET_KEY` (never travels between envs) + its OWN GitHub OAuth
app. Run `infra/provision.sh <env> ...`, set `env.nonsecret.<env>`, `bootstrap_vm.sh` on
that VM with `ENV_NAME=<env>`, register a `vm-<env>` runner. prod deploys only via the
manual **Deploy (prod)** workflow gated by the `prod` Environment's required reviewers.

## Rejected alternatives

Cloud-runner→SSH (NSG hole-punching, key in GitHub), Azure DevOps (not worth migrating off
GitHub), compose-Postgres (no managed backups/PITR), SOPS/systemd-creds for secrets
(workable but Key Vault gives central RBAC + audit + rotation with no app change). Secrets
must NEVER flow through `az vm run-command` (Azure logs them).
