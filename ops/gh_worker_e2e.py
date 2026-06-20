"""REAL end-to-end: can a Codex WORKER deliver to GitHub under an INJECTED token?

This is the multi-user / VM readiness probe. When a console user logs in with
their own GitHub, their per-user OAuth token is injected into the Codex worker as
GH_TOKEN — NOT the host's stored `gh auth`. This script proves a worker so
authenticated can drive the WHOLE GitHub lifecycle by itself:

    create repo -> clone -> branch -> commit -> push -> open PR -> merge -> delete repo

It runs a real `codex exec` worker (default model gpt-5.4-mini, reasoning=medium,
sandbox=workspace-write — exactly a real Builder) with ONLY the token injected,
then verifies the outcome straight from the GitHub API using that same token (so
the check is valid on a fresh VM that has no host `gh auth` at all). It cleans up
the throwaway repo if the worker did not get as far as deleting it.

Token resolution (first match wins):
    --token <t>   |   $GH_TOKEN / $GITHUB_TOKEN   |   `gh auth token` (host)

Usage:
    python ops/gh_worker_e2e.py [--token <t>] [--model gpt-5.4-mini] [--keep]

Run it locally first (smoke), then on the Azure VM with the SAME command — a green
run on the VM means the VM is delivery-ready for end users.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

# Importing the package triggers load_dotenv(), so CODEX_API_KEY and the rest of
# the repo .env are present before we build the worker environment.
from agentic_company.integrations.codex.runner import (
    _NATIVE_SKILLS_READY,
    build_codex_exec_command,
    build_codex_exec_environment,
    stream_codex_exec_to_log,
)

API = "https://api.github.com"
STEPS = ("auth", "create", "clone", "commit", "push", "pr", "merge", "delete")


def gh_api(method: str, path: str, token: str, *, body: dict | None = None):
    """Call the GitHub REST API with the token. Returns (status, json, headers)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "ADL-gh-selftest",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {}), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        payload = {}
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            pass
        return exc.code, payload, dict(exc.headers)


def resolve_token(arg_token: str) -> str:
    if arg_token:
        return arg_token.strip()
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    try:
        out = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=15
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


def build_prompt(owner: str, repo: str) -> str:
    return (
        "You are an ADL delivery worker. Validate full GitHub delivery from inside this "
        "sandbox, end to end, by running the exact lifecycle below. Do NOT ask for "
        "confirmation — run the commands yourself.\n\n"
        f'AUTH: You are authenticated to GitHub as "{owner}". A token is in the environment '
        "variable GH_TOKEN (and GITHUB_TOKEN); `gh` picks it up automatically. For `git "
        "push`, FIRST run `gh auth setup-git` so git reuses that token (GIT_CONFIG_GLOBAL "
        "points at a writable file, so this is allowed). If a push still fails to "
        "authenticate, set the remote to a tokenized URL of the form "
        f"https://x-access-token:<the GH_TOKEN value>@github.com/{owner}/{repo}.git and "
        "retry. Never print the token value.\n\n"
        f'TASK — use a throwaway private repo named "{repo}" (it does not exist yet; you '
        "create it and later delete it):\n"
        f"  1. CONFIRM AUTH:  `gh api user` and check the login is \"{owner}\".\n"
        f"  2. CREATE REPO:   `gh repo create {repo} --private --add-readme` "
        "(this gives it an initialized `main` branch).\n"
        f"  3. CLONE:         clone {owner}/{repo} into a subfolder ./work, then cd into it.\n"
        "  4. BRANCH+COMMIT: `git switch -c selftest`; create a file SELFTEST.md containing "
        "one line; `git add SELFTEST.md`; commit with "
        '`-c user.email=adl@local -c user.name="ADL Selftest"`.\n'
        "  5. PUSH:          `git push -u origin selftest`.\n"
        f"  6. OPEN PR:       `gh pr create --repo {owner}/{repo} --base main --head selftest "
        '--title "ADL selftest" --body "automated delivery self-test"`.\n'
        f"  7. MERGE PR:      `gh pr merge --repo {owner}/{repo} selftest --squash "
        "--delete-branch`.\n"
        f"  8. DELETE REPO:   `gh repo delete {owner}/{repo} --yes`.\n\n"
        "When finished, output ONE final line in EXACTLY this format (one token per step, "
        "value ok or fail):\n"
        "SELFTEST auth=ok create=ok clone=ok commit=ok push=ok pr=ok merge=ok delete=ok\n"
        "For any step that failed, add one short line after it with the error text."
    )


def parse_summary(text: str) -> dict[str, str]:
    """Pull the SELFTEST result line into {step: ok|fail}."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        if line.strip().upper().startswith("SELFTEST"):
            for token in line.split():
                if "=" in token:
                    key, _, val = token.partition("=")
                    if key.lower() in STEPS:
                        result[key.lower()] = val.strip().lower()
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--token", default="", help="GitHub token (else env / gh auth token)")
    ap.add_argument("--model", default="gpt-5.4-mini", help="Codex model (token-billed)")
    ap.add_argument("--reasoning", default="medium")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--keep", action="store_true", help="do not delete the repo on cleanup")
    args = ap.parse_args()
    try:  # Windows consoles default to cp1252; keep prints from crashing on non-ASCII.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    token = resolve_token(args.token)
    if not token:
        print("[selftest] no GitHub token (pass --token, set GH_TOKEN, or `gh auth login`)")
        return 2

    # Who is this token? + which scopes does it carry (need repo + delete_repo).
    status, user, headers = gh_api("GET", "/user", token)
    if status != 200 or not user.get("login"):
        print(f"[selftest] token rejected by GitHub ({status}): {user.get('message', '')}")
        return 2
    owner = str(user["login"])
    scopes = (headers.get("X-OAuth-Scopes") or headers.get("x-oauth-scopes") or "").strip()
    scope_set = {s.strip() for s in scopes.split(",") if s.strip()}
    print(f"[selftest] token acts as: {owner}")
    print(f"[selftest] token scopes : {scopes or '(fine-grained / none reported)'}")
    have_repo = ("repo" in scope_set) or not scope_set  # fine-grained tokens report no scopes
    have_delete = ("delete_repo" in scope_set) or not scope_set
    if not have_repo:
        print("[selftest] WARNING: token lacks the 'repo' scope — create/PR/merge will fail.")
    if not have_delete:
        print(
            "[selftest] WARNING: token lacks 'delete_repo' — the worker's delete step (and "
            "cleanup) will fail. Re-auth with it: `gh auth refresh -h github.com -s delete_repo`."
        )

    repo = f"adl-selftest-{os.getpid()}"
    # Don't collide with an existing repo.
    exists, _, _ = gh_api("GET", f"/repos/{owner}/{repo}", token)
    if exists == 200:
        repo = f"{repo}-{abs(hash(repo)) % 9000 + 1000}"
    print(f"[selftest] throwaway repo: {owner}/{repo}")

    work = Path(tempfile.mkdtemp(prefix="gh-worker-e2e-"))
    proj = work / "generated-project"
    proj.mkdir(parents=True, exist_ok=True)
    summary = work / "summary.md"
    log = work / "exec.log"
    raw = work / "raw.jsonl"

    # Run via the npm Codex CLI + CODEX_API_KEY (token billing), NOT the VS Code
    # extension: the Azure VM has no extension, so this is the VM-representative
    # path. The repo ships a local npm codex under ops/codex-npm-smoke/.
    os.environ["AGENTIC_CODEX_BINARY_MODE"] = "npm"
    os.environ["AGENTIC_CODEX_REASONING_EFFORT"] = args.reasoning

    # The worker env is the real allowlisted Codex environment PLUS the injected
    # per-user token (the VM path). gh state + git global config are redirected to
    # writable workspace files so `gh auth setup-git` works under workspace-write.
    env = build_codex_exec_environment(proj)
    env["GH_TOKEN"] = token
    env["GITHUB_TOKEN"] = token
    env["GH_CONFIG_DIR"] = str(work / ".ghconfig")
    env["GIT_CONFIG_GLOBAL"] = str(work / ".gitconfig")
    env["GIT_TERMINAL_PROMPT"] = "0"  # never block on a credential prompt

    _NATIVE_SKILLS_READY.clear()
    cmd = build_codex_exec_command(
        codex_binary=None,
        model=args.model,
        sandbox="workspace-write",
        target_project_dir=str(proj),
        run_dir=work,
        summary_path=summary,
        force_sandbox=True,
    )
    print(f"[selftest] model={args.model} reasoning={args.reasoning} sandbox=workspace-write")
    print(f"[selftest] codex binary: {cmd[0]}  (npm CLI = VM path, token-billed)")
    print("[selftest] running REAL Codex worker (token injected, no host gh auth used) ...")
    stream_codex_exec_to_log(
        cmd,
        build_prompt(owner, repo),
        args.timeout,
        log,
        raw,
        env=env,
        trace_agent_id="gh-selftest",
        trace_run_dir=work,
    )

    # ---- verify from GitHub's side (token API), not just the worker's word ----
    raw_text = raw.read_text(encoding="utf-8", errors="ignore") if raw.exists() else ""
    low = raw_text.lower()
    ran = {
        "create": "repo create" in low,
        "push": "git push" in low or "push -u" in low,
        "pr": "pr create" in low,
        "merge": "pr merge" in low,
        "delete": "repo delete" in low,
    }
    answer = summary.read_text(encoding="utf-8").strip() if summary.exists() else ""
    reported = parse_summary(answer + "\n" + raw_text)

    final_status, _, _ = gh_api("GET", f"/repos/{owner}/{repo}", token)
    repo_gone = final_status == 404

    print("\n================ TRUTH (from GitHub) ================")
    print("worker EXECUTED these commands (from raw events):")
    for k in ("create", "push", "pr", "merge", "delete"):
        print(f"    gh/git {k:<7}: {'yes' if ran[k] else 'NO'}")
    print(f"repo {owner}/{repo} now: {'DELETED (404)' if repo_gone else f'still exists ({final_status})'}")
    if reported:
        print("worker self-report  :", " ".join(f"{k}={reported.get(k, '?')}" for k in STEPS))
    print("====================================================")
    print("\n--- worker final message (first 1200 chars) ---")
    print(answer[:1200] or "(no summary captured)")

    # cleanup: if the repo still exists and we're not keeping it, delete via API.
    if not repo_gone and not args.keep:
        del_status, del_body, _ = gh_api("DELETE", f"/repos/{owner}/{repo}", token)
        if del_status == 204:
            print(f"\n[cleanup] deleted leftover repo {owner}/{repo}")
            repo_gone = True
        else:
            print(
                f"\n[cleanup] could NOT delete {owner}/{repo} ({del_status}: "
                f"{del_body.get('message', '')}). Delete it by hand if needed."
            )

    # Verdict: the full delivery path is proven when the worker actually executed
    # create+push+pr+merge and the repo lifecycle completed (created then gone).
    core_ok = all(ran[k] for k in ("create", "push", "pr", "merge"))
    delete_ok = ran["delete"] or reported.get("delete") == "ok"
    verdict = core_ok and delete_ok and repo_gone
    print("\n================= VERDICT =================")
    print("FULL GitHub delivery under injected token:", "PASS" if verdict else "FAIL")
    if not verdict:
        print("  core (create/push/pr/merge executed):", core_ok)
        print("  delete executed                     :", delete_ok)
        print("  repo confirmed gone                 :", repo_gone)
    print("==========================================")
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
