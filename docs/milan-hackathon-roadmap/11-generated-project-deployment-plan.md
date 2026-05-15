# 11 Generated Project Deployment Plan

This plan is specifically for deploying the apps created by `agentic-company`.

We are not deploying the platform itself to Azure in this track. The local platform remains the
control plane for now: it plans, generates, tests, deploys, and hands off generated client projects.

## End Goal

The target delivery loop is:

1. Planning console creates a project plan.
2. Fullstack Engineer Agent builds a generated app.
3. QA Agent validates the app locally and through Docker.
4. Deployment Agent deploys the generated app to Azure.
5. QA Agent smoke-tests the public deployed URL.
6. Handoff Agent reports the live URL, evidence, logs, and teardown instructions.

## First Cloud Target

Use Azure Container Apps for generated web apps.

Reasoning:

- Generated apps already produce Docker artifacts.
- Azure Container Apps is simpler than Kubernetes for MVP/demo workloads.
- It supports public ingress, environment variables, secrets, logs, and scale-to-zero style
  operation.
- It does not require us to deploy `agentic-company` itself first.

Azure DevOps can come later for CI/CD. The first deployment runner should be direct tool execution
from the local platform using Azure CLI and Docker.

## Credential Strategy

Initial mode: `local_az_cli`.

The operator authenticates outside the platform:

```powershell
az login
az account show
```

The Deployment Agent then uses the local Azure CLI session. The platform does not store Azure
passwords, service principal secrets, or personal credentials.

Generated app secrets, such as `OPENAI_API_KEY`, should be passed as Azure Container Apps secrets or
environment variables, not baked into images or committed to generated project files.

Status:

- Implemented for the current dev PoC.
- The console starts deployment only after explicit user confirmation.
- The runner uses the local Azure CLI session and generated-project `.env` values.
- Azure account selection remains outside the platform for now.

## Deployment Artifacts

### `11-deployment-plan.json`

Implemented. This deterministic artifact inspects the generated project and answers:

- Is a Dockerfile present?
- Is Docker Compose present?
- Is `.env.example` present?
- Is README operational?
- Which first deployment target is recommended?
- What blocks deployment readiness?

### `12-deployment-request.json`

Implemented. This deterministic artifact is the structured request consumed by the Azure deployment
runner. It describes the target, stable dev resource names, required environment variables, and
preflight checks:

```json
{
  "target": "azure-container-apps",
  "deployment_mode": "dev_reuse",
  "status": "ready",
  "azure_login_required": true,
  "login_required_when": "before running the deployment runner",
  "inputs": {
    "subscription_id": "<azure-subscription-id>",
    "location": "westeurope",
    "resource_group": "rg-agentic-generated-dev",
    "container_registry": "agenticgenerateddevacr",
    "container_app_environment": "agentic-generated-dev-env",
    "container_app_name": "app-simple-llm-chat-dev",
    "image": "agenticgenerateddevacr.azurecr.io/simple-llm-chat:latest",
    "environment_variables": ["OPENAI_API_KEY", "DEFAULT_MODEL"]
  }
}
```

The markdown companion, `12-deployment-request.md`, is readable by a human reviewer. It lists the
Azure CLI, Docker, account-selection, build, push, Container Apps creation, secret wiring, and public
URL lookup commands that the deployment runner executes after explicit confirmation.

The initial deployment mode is `dev_reuse`: all generated projects share the same dev resource group,
container registry, and Container Apps environment, while the Container App name is stable per project
name. This keeps iteration fast by updating an existing app and pushing `latest` instead of creating
new infrastructure for every console run.

### `13-deployment-summary.md`

Written after deployment. It should include:

- deployment status
- public URL
- resource group
- container app name
- image reference
- required secrets configured
- useful log commands
- teardown command

The deployment runner also runs post-deployment Playwright chatbot QA before writing handoff. On a
successful deployment, `09-handoff-summary.md` is written after `13-deployment-summary.md` exists and
reports `Status: deployed`.

## Runner Responsibilities

The current Deployment Runner:

1. Verify `az` is installed.
2. Verify `docker` is installed.
3. Verify `az account show` succeeds.
4. Create or reuse a resource group.
5. Create or reuse Azure Container Registry.
6. Build the generated app image.
7. Push the image to ACR.
8. Create or update an Azure Container App.
9. Configure secrets and environment variables.
10. Capture the public URL.
11. Run post-deploy Playwright chatbot QA against the public URL.
12. Write `13-deployment-summary.md`.
13. Write `09-handoff-summary.md` only when deployment succeeds.

## Post-Deploy QA

After deployment, QA runs a public URL smoke test:

1. Open the deployed URL with Playwright.
2. Wait for the app title and chat input.
3. Send a realistic prompt.
4. Wait for an assistant response.
5. Capture screenshots and transcript.
6. Write deployment QA evidence.

This is implemented as the deployment runner's `Post-deployment chatbot QA` step. Evidence is written
under `deployment/`, including screenshots and browser transcript files.

## Safety Rules

- No automatic cloud resource creation without explicit confirmation.
- No secrets in generated source, Docker image layers, logs, summaries, or PR descriptions.
- Resource names must be predictable and tied to the run or project.
- Every deployment summary must include a teardown command.
- Failed deployment must produce a repairable artifact, not only terminal output.

Current limitations:

- The Azure subscription and account are inherited from local `az` state.
- Resource names are intentionally stable for dev speed, not isolated per customer.
- Teardown is documented in the summary but not yet a first-class console action.
- Rollback/revision handling is not implemented.
- Azure DevOps and GitHub Actions deployment modes are intentionally deferred.

## Later Modes

After `local_az_cli` works, add:

- platform-owned Azure profile
- client-owned Azure profile
- service principal / workload identity federation
- Azure Key Vault secret references
- GitHub Actions or Azure DevOps CI/CD

The important thing is to make one generated app deploy reliably first.
