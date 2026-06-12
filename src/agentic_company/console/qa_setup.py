"""One-command QA browser runtime setup.

Installs a repo-local Playwright + Chromium so the QA worker can capture
screenshots under the workspace-write sandbox, verifies it with a real headless
screenshot, and records the paths in ``.env``. Idempotent and cross-platform.

Run with: ``uv run --extra app agentic-qa-setup``
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
QA_RUNTIME = REPO_ROOT / "ops" / "qa-runtime"
BROWSERS_DIR = QA_RUNTIME / "browsers"
NODE_MODULES = QA_RUNTIME / "node_modules"
PLAYWRIGHT_CLI = NODE_MODULES / "playwright" / "cli.js"
ENV_FILE = REPO_ROOT / ".env"

ENV_LINES = (
    "PLAYWRIGHT_BROWSERS_PATH=ops/qa-runtime/browsers",
    "NODE_PATH=ops/qa-runtime/node_modules",
)


def _log(message: str) -> None:
    print(f"[qa-setup] {message}", flush=True)


def _find(executable: str) -> str:
    found = shutil.which(executable)
    if found:
        return found
    raise SystemExit(
        f"[qa-setup] '{executable}' was not found on PATH. Install Node.js (which ships npm) "
        "on this machine, then re-run: uv run --extra app agentic-qa-setup"
    )


def _run(args: list[str], *, env: dict[str, str] | None = None) -> None:
    _log("$ " + " ".join(args))
    result = subprocess.run(args, cwd=str(QA_RUNTIME), env=env)
    if result.returncode != 0:
        raise SystemExit(f"[qa-setup] command failed ({result.returncode}): {' '.join(args)}")


def _browser_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_DIR)
    env["NODE_PATH"] = str(NODE_MODULES)
    return env


def _ensure_package_json(npm: str) -> None:
    if (QA_RUNTIME / "package.json").exists():
        return
    QA_RUNTIME.mkdir(parents=True, exist_ok=True)
    _run([npm, "init", "-y"])


def _install_playwright(npm: str) -> None:
    _log("installing the playwright npm package (repo-local)...")
    _run([npm, "install", "playwright@latest"])
    if not PLAYWRIGHT_CLI.exists():
        raise SystemExit(f"[qa-setup] playwright install did not produce {PLAYWRIGHT_CLI}")


def _install_chromium(node: str) -> None:
    _log("downloading the Chromium browser build...")
    args = [node, str(PLAYWRIGHT_CLI), "install", "chromium"]
    if platform.system() == "Linux":
        # Linux VMs also need the OS libraries Chromium links against.
        args.insert(3, "--with-deps")
    _run(args, env=_browser_env())


def _verify(node: str) -> None:
    _log("verifying with a real headless screenshot...")
    proof = QA_RUNTIME / "setup-proof.png"
    if proof.exists():
        proof.unlink()
    script = (
        "const { chromium } = require('playwright');"
        "(async () => {"
        "  const b = await chromium.launch({ headless: true, args: ["
        "    '--disable-gpu','--disable-dev-shm-usage','--disable-software-rasterizer',"
        "    '--force-color-profile=srgb','--font-render-hinting=none'] });"
        "  const p = await b.newPage();"
        "  await p.setContent('<h1>qa-setup ok</h1>');"
        "  await p.screenshot({ path: 'setup-proof.png', fullPage: true });"
        "  await b.close();"
        "})().catch(e => { console.error(e); process.exit(1); });"
    )
    _run([node, "-e", script], env=_browser_env())
    if not proof.exists():
        raise SystemExit("[qa-setup] verification screenshot was not produced")
    proof.unlink()
    _log("screenshot verification passed.")


def _update_env() -> None:
    existing = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    missing = [line for line in ENV_LINES if line.split("=", 1)[0] not in existing]
    if not missing:
        _log(".env already points at the QA browser runtime.")
        return
    block = "\n# QA browser runtime (agentic-qa-setup)\n" + "\n".join(missing) + "\n"
    with ENV_FILE.open("a", encoding="utf-8") as handle:
        handle.write(block)
    _log(f"appended {len(missing)} line(s) to .env")


def main() -> int:
    """Install and verify the repo-local QA browser runtime."""

    _log(f"target: {QA_RUNTIME}")
    npm = _find("npm")
    node = _find("node")
    _ensure_package_json(npm)
    _install_playwright(npm)
    _install_chromium(node)
    _verify(node)
    _update_env()
    _log("done. QA can now capture screenshots; restart the console to pick up .env.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
