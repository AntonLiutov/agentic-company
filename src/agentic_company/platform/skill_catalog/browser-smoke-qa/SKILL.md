---
name: browser-smoke-qa
description: Validate generated app behavior in a real browser and capture screenshot evidence. Use for Quality Reviewer work, main user flows, buttons, navigation, forms, persistence, and visible errors. Do not use for implementation, deployment setup, or release writing.
---

# Browser Smoke QA

## Purpose

Verify that the generated app behaves like a usable prototype, not only that files
exist. For any web UI, capture full-page screenshot evidence — source inspection
alone is never sufficient.

## Boundaries

- Owns browser/runtime behavior validation and pass/fail evidence.
- Does not repair code directly unless explicitly routed through Builder.
- Escalates clear defects as repair requests with exact reproduction steps.

## Browser Screenshot Runtime (REQUIRED for web UI)

Use the pre-installed Playwright + Chromium. Do NOT drive the host Chrome/Edge by
hand and do NOT hand-roll a Chrome DevTools (CDP) client — that path crashes on the
GPU process and wastes many minutes on retries.

Run this once, deterministically, under the workspace-write sandbox:

1. Serve the built app on a local port (background), e.g. from the app directory:
   `python -m http.server 8000` (or a static Node server). Use 127.0.0.1.
2. Drive Playwright (Chromium) headless with the stability flags below, navigate,
   wait for the page to settle, and write full-page screenshots into the run dir
   under `qa/screenshots/<work-item>-<view>.png`.
3. Stop the server.

Canonical Node script (Playwright is pre-installed; PLAYWRIGHT_BROWSERS_PATH and
NODE_PATH are set in the environment):

```js
const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: [
      "--disable-gpu",
      "--disable-dev-shm-usage",
      "--disable-software-rasterizer",
      "--force-color-profile=srgb",
      "--font-render-hinting=none",
    ],
  });
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  await page.goto("http://127.0.0.1:8000/", { waitUntil: "networkidle", timeout: 20000 });
  // Trigger any lazy content, then capture the whole page (handles long/heavy pages).
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(300);
  await page.screenshot({ path: "qa/screenshots/<work-item>-full.png", fullPage: true });
  await browser.close();
})();
```

Stability rules:

- One attempt through Playwright. Cap browser launch + navigation at ~20s. If it
  fails, retry at most once. Never fall back to ad-hoc CDP against host Chrome/Edge.
- The `--disable-gpu` + `--disable-dev-shm-usage` flags are mandatory on this host;
  they are the documented fix for the "GPU process isn't usable" crash.
- `fullPage: true` captures heavy/long pages natively — always use it for proof.

## Workflow

1. Serve the app and capture full-page screenshots of each primary view (above).
2. Exercise the primary user flow from the requirements in the same Playwright
   session (click, navigate, submit, verify persistence where relevant).
3. Also run a functional DOM check of the core behaviors as a second signal.
4. Check visible errors, console/runtime failures, and obvious layout breakage.
5. Produce pass/fail evidence with exact reproduction steps for defects.
6. Return a QA report and repair request when needed.

## Output Contract

- QA report artifact citing the screenshots in `qa/screenshots/` by path.
- Tested flows, pass/fail status, defects, screenshot evidence, and repair
  recommendation.
- Dashboard-safe comment suitable for a review column or issue comment.

## Result Vocabulary

- `passed` — functional behavior verified AND full-page screenshot evidence captured.
- `passed_with_limited_visual_evidence` — functional behavior verified but the
  browser runtime was unavailable, so screenshots could not be captured. Use this
  instead of a plain `passed` so reviewers know visual proof is missing.
- `failed` / repair request — a real defect was found.

## Quality Rules

- Do not pass work based only on source inspection.
- For web UI, always attempt Playwright screenshots first; only downgrade to
  `passed_with_limited_visual_evidence` if the pre-installed browser truly cannot run.
- Do not ignore broken buttons, placeholder flows, invisible text, or unusable layout.

## Failure And Repair

- Retry after Builder repairs the cited defects.
- Block when the app cannot start and the cause is environment/secrets rather than product code.
- Human approval is required when the QA result depends on external credentials or production data.

## Examples

### Good invocation

Input: Built app and requirements.
Output: QA report with `qa/screenshots/*.png` evidence, tested flow, and repair findings.

### Bad invocation

Input: Generated app files.
Output: "Looks good" without opening or screenshotting the app.
