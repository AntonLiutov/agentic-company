"""GitHub repo adapter — prepares the working repo, branches, commits, opens PRs.

git runs in the run's ``target_dir``; gh (clone/create/pr) is addressed by
``--repo`` so it needs no cwd. The provider token stays host-side in the gh env.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Protocol

from agentic_company.integrations.github.cli import GhLike
from agentic_company.ports.repo import PullRequest, RepoCapabilities, RepoSpec

LOGGER = logging.getLogger("agentic_company.github.repo")

# Secrets safety: ADL must never commit credentials to a delivery repo/PR. We
# write this .gitignore before anything is staged AND unstage any secret-looking
# file as a fail-safe, so a stray .env/key the worker created can't leak.
_SECRETS_MARKER = "# ADL secrets safety — never commit credentials"
_SECRETS_GITIGNORE = f"""{_SECRETS_MARKER}
.env
.env.*
*.env
agent-runtime.env
*.key
*.pem
*.pfx
*.p12
id_rsa
id_rsa.*
id_ed25519
id_ed25519.*
*.secret
.secrets/
secrets/
credentials.json
.npmrc
.pypirc

# ADL run scaffolding — never commit into the deliverable. Root-anchored (leading /) so
# a legit nested path like src/qa/ is untouched; kept untracked at runtime via
# .git/info/exclude too; seeded here so it holds from the very first commit.
/.agents/
/qa/
/execution-summary.md
/07-execution-summary.md
/debug.log
"""
_SECRET_PATH_RE = re.compile(
    r"(^|/)("
    r"\.env(\..+)?|.*\.env|agent-runtime\.env|"
    r".*\.(key|pem|pfx|p12|secret)|id_rsa.*|id_ed25519.*|"
    r"secrets/.*|credentials\.json|\.npmrc|\.pypirc"
    r")$",
    re.IGNORECASE,
)
# ADL run scaffolding, anchored to the project root so a legit nested src/qa/ is never
# matched. The fail-safe drops these from the index even if they slipped in before the
# ignore was seeded (mirrors _SECRET_PATH_RE / _unstage_secrets).
_SCAFFOLDING_PATH_RE = re.compile(
    r"^(\.agents/.*|qa/.*|execution-summary\.md|07-execution-summary\.md|debug\.log)$",
    re.IGNORECASE,
)


class GitError(RuntimeError):
    """A git invocation failed."""


class GitLike(Protocol):
    def run(self, args: list[str], *, cwd: Path) -> str:
        """Run ``git <args>`` in ``cwd`` and return stdout; raise on failure."""


class GitRunner:
    """Runs the real git CLI."""

    def __init__(self, *, git_binary: str = "git", timeout_seconds: int = 120) -> None:
        self._git = git_binary
        self._timeout = timeout_seconds

    def run(self, args: list[str], *, cwd: Path) -> str:
        try:
            proc = subprocess.run(
                [self._git, *args],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except FileNotFoundError as exc:
            raise GitError("git is not installed on the host") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {' '.join(args)} timed out") from exc
        if proc.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
            )
        return proc.stdout


class GitHubRepoAdapter:
    """Provides a working git repo for a run and opens linked pull requests."""

    system = "github"
    capabilities = RepoCapabilities(branch=True, pull_request=True, merge=True, review_comment=True)

    def __init__(self, *, gh: GhLike, git: GitLike, github_token: str = "") -> None:
        self._gh = gh
        self._git = git
        self._github_token = (github_token or "").strip()

    def ensure_repo(self, spec: RepoSpec) -> None:
        target = spec.target_dir
        if spec.mode == "support":
            self._gh.run(["repo", "clone", spec.repository, str(target)])
            self._configure_push_credentials(target)
            return
        # new: commit the generated project, create the remote, push.
        self._git.run(["init", "-b", spec.base_branch or "main"], cwd=target)
        self._write_secrets_gitignore(target)
        self._git.run(["add", "-A"], cwd=target)
        self._unstage_secrets(target)
        self._unstage_scaffolding(target)
        self._git.run(["commit", "-m", "Initial commit by Agentic Delivery Lab"], cwd=target)
        self._gh.run(
            [
                "repo",
                "create",
                spec.repository,
                "--private" if spec.private else "--public",
                "--source",
                str(target),
                "--remote",
                "origin",
                "--push",
            ]
        )
        self._configure_push_credentials(target)

    def _configure_push_credentials(self, target_dir: Path) -> None:
        """Make the worker's ``git push`` authenticate via the per-user token on a host
        with no git credential setup (a fresh VM). A repo-local credential helper reads
        the token from the worker's ``GH_TOKEN`` env at push time, so the token never
        lands in ``.git/config``, the command line, or ``git remote -v`` output. When no
        per-user token is bound we leave it untouched and fall back to the host's own git
        credentials (local development)."""
        if not self._github_token:
            return
        # POSIX shell helper (git ships sh on Windows too): answer only credential `get`
        # with username + the token from the environment.
        helper = (
            '!f() { test "$1" = get && '
            'printf "username=x-access-token\\npassword=%s\\n" "$GH_TOKEN"; }; f'
        )
        try:
            self._git.run(
                ["config", "--local", "--replace-all", "credential.helper", helper],
                cwd=target_dir,
            )
        except GitError:
            LOGGER.warning("Could not configure git push credentials in %s", target_dir)

    def create_branch(self, target_dir: Path, branch: str, *, base: str = "") -> None:
        # Idempotent: on a repair re-run the work item's branch already exists, so
        # switch to it and keep its history. Never reset it to base — that would drop
        # the prior fix and the PR would merge stale code.
        if self._branch_exists(target_dir, branch):
            self._git.run(["checkout", branch], cwd=target_dir)
            return
        args = ["checkout", "-b", branch]
        if base:
            args.append(base)
        self._git.run(args, cwd=target_dir)

    def _branch_exists(self, target_dir: Path, branch: str) -> bool:
        try:
            return bool(self._git.run(["branch", "--list", branch], cwd=target_dir).strip())
        except Exception:
            return False

    def commit_push(self, target_dir: Path, message: str, *, branch: str = "") -> None:
        self._write_secrets_gitignore(target_dir)
        self._git.run(["add", "-A"], cwd=target_dir)
        self._unstage_secrets(target_dir)
        self._unstage_scaffolding(target_dir)
        self._git.run(["commit", "-m", message], cwd=target_dir)
        self._git.run(["push", "-u", "origin", branch or "HEAD"], cwd=target_dir)

    def _write_secrets_gitignore(self, target_dir: Path) -> None:
        """Ensure a credentials-excluding .gitignore exists before staging."""
        path = target_dir / ".gitignore"
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        if _SECRETS_MARKER in existing:
            return
        prefix = "\n" if existing and not existing.endswith("\n") else ""
        path.write_text(existing + prefix + _SECRETS_GITIGNORE, encoding="utf-8")

    def _unstage_secrets(self, target_dir: Path) -> None:
        """Fail-safe: drop any secret-looking file from the index before commit."""
        staged = self._git.run(["diff", "--cached", "--name-only"], cwd=target_dir)
        for rel in staged.splitlines():
            rel = rel.strip()
            if rel and _SECRET_PATH_RE.search(rel):
                LOGGER.warning("Refusing to commit secret-looking file: %s", rel)
                self._git.run(["rm", "--cached", "-r", "--ignore-unmatch", rel], cwd=target_dir)

    def _unstage_scaffolding(self, target_dir: Path) -> None:
        """Fail-safe: drop ADL run scaffolding (.agents/, qa/, execution summary, debug
        log) from the index before commit, so it never lands in the deliverable PR even
        if it was staged before the ignore was seeded. Mirrors ``_unstage_secrets``."""
        staged = self._git.run(["diff", "--cached", "--name-only"], cwd=target_dir)
        for rel in staged.splitlines():
            rel = rel.strip()
            if rel and _SCAFFOLDING_PATH_RE.search(rel):
                LOGGER.info("Dropping ADL scaffolding from the commit: %s", rel)
                self._git.run(["rm", "--cached", "-r", "--ignore-unmatch", rel], cwd=target_dir)

    def open_pr(
        self,
        target_dir: Path,
        *,
        title: str,
        body: str = "",
        base: str = "",
        head: str = "",
    ) -> PullRequest:
        args = [
            "pr",
            "create",
            "--title",
            title,
            "--body",
            body or "Opened by Agentic Delivery Lab.",
        ]
        if base:
            args += ["--base", base]
        if head:
            args += ["--head", head]
        url = self._gh.run(args, cwd=target_dir).strip()
        number = url.rsplit("/", 1)[-1] if url else ""
        return PullRequest(number=number, url=url, branch=head)

    def find_pr(self, target_dir: Path, head: str) -> PullRequest | None:
        """The open PR for a branch, if one already exists (e.g. the agent opened it)."""
        if not head:
            return None
        try:
            out = self._gh.run(
                ["pr", "list", "--head", head, "--json", "url,number", "--limit", "1"],
                cwd=target_dir,
            ).strip()
        except Exception:
            return None
        if not out:
            return None
        try:
            items = json.loads(out)
        except Exception:
            return None
        if not items:
            return None
        item = items[0]
        url = str(item.get("url") or "")
        return (
            PullRequest(number=str(item.get("number") or ""), url=url, branch=head) if url else None
        )

    def comment_pr(self, pr: str, body: str) -> None:
        """Leave a review comment on a PR (gh infers the repo from the URL)."""
        if not pr or not body.strip():
            return
        self._gh.run(["pr", "comment", pr, "--body", body])

    def merge_pr(self, pr: str) -> None:
        """Squash-merge a PR and delete its branch (QA's accept action)."""
        if not pr:
            return
        self._gh.run(["pr", "merge", pr, "--squash", "--delete-branch"])
