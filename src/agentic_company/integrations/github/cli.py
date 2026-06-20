"""Host-side ``gh`` CLI wrapper.

The GitHub token lives in the host operator environment and is used only by this
wrapper. It is NEVER injected into a Codex worker subprocess (the Phase 0 env
allowlist already drops it), so untrusted LLM code can't read it.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Protocol

LOGGER = logging.getLogger("agentic_company.github.cli")

# GitHub's GraphQL/Projects API answers concurrent mutations (e.g. several
# addProjectV2ItemById in quick succession) with a transient error that EXPLICITLY
# asks the caller to retry. These are safe to re-run — match them and back off.
_TRANSIENT_GH_SIGNALS = (
    "temporary conflict",
    "please try again",
    "try again later",
    "was submitted too quickly",
    "secondary rate limit",
    "please wait a few minutes",
)


def _is_transient_gh_error(stderr: str) -> bool:
    low = (stderr or "").lower()
    return any(signal in low for signal in _TRANSIENT_GH_SIGNALS)


class GhError(RuntimeError):
    """A ``gh`` CLI invocation failed."""


class GhLike(Protocol):
    """Anything that can run a gh subcommand and return stdout (for injection)."""

    def run(self, args: list[str], *, cwd: Path | None = None) -> str:
        """Run ``gh <args>`` (optionally in ``cwd``) and return stdout."""


class GhRunner:
    """Runs the real ``gh`` CLI on the host.

    Transient GitHub conflicts ("temporary conflict. Please try again.") are retried
    with exponential backoff — they are exactly the errors GitHub tells us to re-run,
    and the board mirror fires several rapid mutations that can race server-side.
    """

    def __init__(
        self,
        *,
        gh_binary: str = "gh",
        timeout_seconds: int = 60,
        max_retries: int = 4,
        backoff_seconds: float = 0.6,
        github_token: str = "",
    ) -> None:
        self._gh = gh_binary
        self._timeout = timeout_seconds
        self._max_retries = max(0, max_retries)
        self._backoff = backoff_seconds
        self._github_token = (github_token or "").strip()

    def _env(self) -> dict[str, str] | None:
        """When a per-user OAuth token is bound, authenticate gh with it (instead of the
        host's stored auth) so runs act under the connected user's GitHub account."""
        if not self._github_token:
            return None
        return {**os.environ, "GH_TOKEN": self._github_token, "GITHUB_TOKEN": self._github_token}

    def run(self, args: list[str], *, cwd: Path | None = None) -> str:
        last_stderr = ""
        env = self._env()
        for attempt in range(self._max_retries + 1):
            try:
                proc = subprocess.run(
                    [self._gh, *args],
                    cwd=str(cwd) if cwd is not None else None,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    env=env,
                )
            except FileNotFoundError as exc:  # gh not installed
                raise GhError("gh CLI is not installed on the host") from exc
            except subprocess.TimeoutExpired as exc:
                raise GhError(f"gh {' '.join(args)} timed out") from exc
            if proc.returncode == 0:
                return proc.stdout
            last_stderr = proc.stderr.strip()
            # retry only GitHub's own "try again" signals, and only while attempts remain
            if attempt < self._max_retries and _is_transient_gh_error(last_stderr):
                delay = self._backoff * (2**attempt)
                LOGGER.info(
                    "gh %s hit a transient GitHub conflict (attempt %d/%d); retrying in %.1fs",
                    " ".join(args[:2]),
                    attempt + 1,
                    self._max_retries,
                    delay,
                )
                time.sleep(delay)
                continue
            raise GhError(f"gh {' '.join(args)} failed ({proc.returncode}): {last_stderr}")
        raise GhError(f"gh {' '.join(args)} failed after retries: {last_stderr}")
