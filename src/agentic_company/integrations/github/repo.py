"""GitHub repo adapter — prepares the working repo, branches, commits, opens PRs.

git runs in the run's ``target_dir``; gh (clone/create/pr) is addressed by
``--repo`` so it needs no cwd. The provider token stays host-side in the gh env.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

from agentic_company.integrations.github.cli import GhLike
from agentic_company.ports.repo import PullRequest, RepoSpec


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

    def __init__(self, *, gh: GhLike, git: GitLike) -> None:
        self._gh = gh
        self._git = git

    def ensure_repo(self, spec: RepoSpec) -> None:
        target = spec.target_dir
        if spec.mode == "support":
            self._gh.run(["repo", "clone", spec.repository, str(target)])
            return
        # new: commit the generated project, create the remote, push.
        self._git.run(["init", "-b", spec.base_branch or "main"], cwd=target)
        self._git.run(["add", "-A"], cwd=target)
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

    def create_branch(self, target_dir: Path, branch: str, *, base: str = "") -> None:
        args = ["checkout", "-b", branch]
        if base:
            args.append(base)
        self._git.run(args, cwd=target_dir)

    def commit_push(self, target_dir: Path, message: str, *, branch: str = "") -> None:
        self._git.run(["add", "-A"], cwd=target_dir)
        self._git.run(["commit", "-m", message], cwd=target_dir)
        self._git.run(["push", "-u", "origin", branch or "HEAD"], cwd=target_dir)

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
