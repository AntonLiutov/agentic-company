---
name: deployment-check
description: Verify that a generated app can be packaged, deployed, and opened at a reachable URL. Use for Deployment Agent work, deployment evidence, runtime configuration, ingress, and post-deploy smoke readiness. Do not use for product implementation or requirements analysis.
---

# Deployment Check

## Purpose

Turn a locally generated app into a reachable demo and capture evidence that the deployed URL works.

## Boundaries

- Owns packaging, deployment readiness, deployment result checks, and environment blockers.
- Does not invent product features or mark functional QA complete.
- Escalates missing secrets, cloud quota, or infrastructure failures clearly.

## Inputs

- Generated app directory and build summary.
- Deployment target, environment variables, registry/container assumptions, and QA requirements.
- Prior deployment or post-deploy QA artifacts.

## Workflow

1. Inspect packaging/runtime requirements before deployment.
2. Verify the app has a runnable entrypoint and required configuration.
3. Deploy or validate deployment using the configured platform path.
4. Open or check the deployed URL when possible.
5. Confirm that the deployed runtime serves required static assets or clearly
   hand off asset/style risk to post-deploy QA.
6. Capture deployment URL, logs summary, blockers, and post-deploy QA instructions.
6. Return deployment summary artifact and evidence refs.

## Output Contract

- Deployment summary artifact.
- URL, environment notes, deployment status, blockers, and evidence.
- Dashboard-safe comment for release or board status.

## Quality Rules

- Do not report success without a reachable URL or clear limitation.
- Do not expose secrets in artifacts or comments.
- Separate deployment success from product behavior success.
- Do not claim visual/product success just because the URL returned HTTP 200;
  post-deploy QA must validate behavior, CSS/static assets, and visible layout.

## Failure And Repair

- Retry when packaging or configuration defects are repairable.
- Block on missing secrets, provider limits, cloud quota, or unavailable infrastructure.
- Human approval is required for production-like deployment changes.

## Examples

### Good invocation

Input: Generated app ready for Azure Container Apps.
Output: Deployment summary with URL and post-deploy smoke notes.

### Bad invocation

Input: Broken container build.
Output: "Deployment complete" with no URL or evidence.
