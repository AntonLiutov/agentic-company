# Fresh-from-scratch setup (the clean way)

A single, ordered checklist to stand up the console on Azure correctly, with secrets in
Key Vault and a managed Postgres, then drop the old messy resource groups. Everything is
scripted + reproducible per env. Detail for each piece is in `README.md`.

## Decisions to make first

1. **Domain** for the console, e.g. `console.yourdomain.com` (needed for HTTPS + the OAuth
   callback). Certs are free (Let's Encrypt); you just need the domain + a DNS A-record.
2. **VM:** keep the existing `vm-agentic-demo-001` (recommended — codex/playwright/docker/uv
   already installed) or stand up a fresh one (Path B). Either way, secrets + DB move to a
   clean new RG.
3. **New RG name + region**, e.g. `rg-agentic-platform` in `eastus`.

## Path A — keep the VM, fresh managed resources (recommended)

```bash
# 0. one-time: a clean RG for the platform's managed resources
az group create -n rg-agentic-platform -l eastus

# 1. provision Key Vault + Postgres into the new RG (reads the VM's managed identity + IP).
#    Prompts for this env's GitHub OAuth client id/secret (create the OAuth app first, step 4).
infra/provision.sh dev rg-agentic-platform vm-agentic-demo-001 rg-agentic-company-vm-demo
#    -> prints the real vault name + Postgres FQDN (they carry a unique suffix).

# 2. put those into ops/deploy/env.nonsecret.dev (replace the __SET_*__ sentinels):
#       AGENTIC_KEY_VAULT=<printed vault>   AGENTIC_PG_HOST=<printed FQDN>
#       GITHUB_OAUTH_CALLBACK_URL=https://console.yourdomain.com/auth/github/callback
#    commit it.

# 3. on the VM: render shared/.env from Key Vault (managed identity) + install the units
sudo ENV_NAME=dev bash ops/deploy/bootstrap_vm.sh

# 4. GitHub OAuth app (one per env): New OAuth App, callback =
#    https://console.yourdomain.com/auth/github/callback. Its client id/secret were entered
#    at step 1 (or re-run ops/deploy/seed-secrets.sh <vault> <db-pw> <id> <secret>).

# 5. DNS: A-record console.yourdomain.com -> the VM's public IP (pin the IP to STATIC).

# 6. TLS + nginx (free Let's Encrypt cert, auto-renew). NSG must allow inbound 80 + 443.
sudo DOMAIN=console.yourdomain.com EMAIL=you@yourdomain.com bash ops/deploy/setup-tls.sh

# 7. CD: register the self-hosted runner on the VM as `deployer` (label vm-dev) — the exact
#    ./config.sh command is printed by bootstrap_vm.sh. Then in the repo: create Environments
#    dev/prod and disable fork-PR runs on self-hosted runners.

# 8. FIRST deploy: there is NO /opt/agentic-company/current until a release lands, so do not
#    start the service by hand. Trigger the dev deploy manually (deploy.yml has a
#    workflow_dispatch trigger) — it runs migrations, creates `current`, and starts the app:
gh workflow run "Deploy (dev)" --ref main
#    then check:  curl -fsS https://console.yourdomain.com/healthz   # {"status":"ok","sha":"<sha>"}
#    Subsequent merges to main auto-deploy.

# 9. once the new setup is proven, DROP the old junk:
az group delete -n rg-agentic-dev --yes            # (already done this session)
# and, if you fully migrate off it, the old VM RG's leftovers — keeping the VM if Path A.
```

## Path B — fresh VM too (zero cruft)

Same as Path A, but first create a new Ubuntu VM (static public IP) in the new RG, then do
the full host setup before `bootstrap_vm.sh`: install docker + docker compose (Redis), uv,
the **standalone** codex CLI + bundled node, and Playwright browsers — per
`docs/milestones/phase-3/vm-runbook.md` (the codex/playwright runtime the workers need).
Then continue from Path A step 1 with the new VM's name. More work; only worth it if you
want the host itself pristine.

## Certificates & subdomains (recap)

- **Cert:** `setup-tls.sh` runs `certbot --nginx -d <domain>` → free 90-day cert,
  auto-renewed by `certbot.timer`. Needs the A-record + inbound 80 open for the challenge.
- **Subdomains:** add a DNS record per subdomain; for TLS either a cert per subdomain
  (`certbot -d a.dom -d b.dom`) or a **wildcard** `*.dom` via the DNS-01 challenge (needs
  your DNS provider's API). Generated apps already get free TLS on `*.azurecontainerapps.io`.

## NSG reminders

- Inbound: keep SSH IP-gated; **open 80 + 443** for the console + the cert challenge.
- Outbound: allow 443 to `*.vault.azure.net`, `login.microsoftonline.com`, IMDS
  `169.254.169.254`, and the Postgres FQDN:5432 (the secret fetch + DB).
