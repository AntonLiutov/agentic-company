"""Provider-neutral source-repository port.

A RepoPort prepares the working repository for a run (new project or support on
an existing repo), branches, commits, and opens a pull request. Concrete
adapters shell out to git / the host ``gh`` CLI; the provider token stays
host-side and never enters the worker environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RepoSpec:
    """How to obtain the working repo for a run."""

    mode: str  # "new" | "support"
    target_dir: Path
    repository: str = ""  # "owner/name" for support, or the new repo name
    base_branch: str = "main"
    work_branch: str = ""
    private: bool = True


@dataclass(frozen=True, slots=True)
class PullRequest:
    """A pull request opened for a run."""

    number: str = ""
    url: str = ""
    branch: str = ""


@dataclass(frozen=True, slots=True)
class RepoCapabilities:
    """What a repo host supports; the orchestrator degrades gracefully on the rest.

    A host that can branch+commit but has no PR API (or none ADL drives) simply
    pushes a branch and links it on the card — "do what's possible, skip the rest".
    """

    branch: bool = False
    pull_request: bool = False
    merge: bool = False
    review_comment: bool = False


class RepoPort(Protocol):
    """Swappable source-repo boundary (independent of the BoardPort host)."""

    system: str
    capabilities: RepoCapabilities

    def ensure_repo(self, spec: RepoSpec) -> None:
        """Make ``spec.target_dir`` a ready git working tree (clone or init)."""

    def create_branch(self, target_dir: Path, branch: str, *, base: str = "") -> None:
        """Create and check out ``branch`` (off ``base`` when given)."""

    def commit_push(self, target_dir: Path, message: str, *, branch: str = "") -> None:
        """Stage all, commit, and push ``branch`` to the remote."""

    def open_pr(
        self,
        target_dir: Path,
        *,
        title: str,
        body: str = "",
        base: str = "",
        head: str = "",
    ) -> PullRequest:
        """Open a pull request and return its number/url/branch."""

    def comment_pr(self, pr: str, body: str) -> None:
        """Leave a review comment on a PR (when ``capabilities.review_comment``)."""

    def merge_pr(self, pr: str) -> None:
        """Merge a PR (when ``capabilities.merge``)."""
