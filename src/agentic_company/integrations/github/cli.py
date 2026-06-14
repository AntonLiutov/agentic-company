"""Host-side ``gh`` CLI wrapper.

The GitHub token lives in the host operator environment and is used only by this
wrapper. It is NEVER injected into a Codex worker subprocess (the Phase 0 env
allowlist already drops it), so untrusted LLM code can't read it.
"""

from __future__ import annotations

import subprocess
from typing import Protocol


class GhError(RuntimeError):
    """A ``gh`` CLI invocation failed."""


class GhLike(Protocol):
    """Anything that can run a gh subcommand and return stdout (for injection)."""

    def run(self, args: list[str]) -> str:
        """Run ``gh <args>`` and return stdout; raise GhError on failure."""


class GhRunner:
    """Runs the real ``gh`` CLI on the host."""

    def __init__(self, *, gh_binary: str = "gh", timeout_seconds: int = 60) -> None:
        self._gh = gh_binary
        self._timeout = timeout_seconds

    def run(self, args: list[str]) -> str:
        try:
            proc = subprocess.run(
                [self._gh, *args],
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except FileNotFoundError as exc:  # gh not installed
            raise GhError("gh CLI is not installed on the host") from exc
        except subprocess.TimeoutExpired as exc:
            raise GhError(f"gh {' '.join(args)} timed out") from exc
        if proc.returncode != 0:
            raise GhError(f"gh {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
        return proc.stdout
