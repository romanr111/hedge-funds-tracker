"""Thin subprocess wrapper around the `gog` CLI (Google Workspace account tools).

Only `--help`-verified surfaces are used (verified 2026-11-07 against
`/usr/local/bin/gog`, build 0.20.0):

- global flags: `--no-input` (never prompt; fail instead), `-y/--force`
  (skip confirmations on destructive commands), `-j/--json`, `-a/--account`.
- this module never performs live API writes by itself; callers decide what
  to invoke. Publishing paths must stay best-effort (warn, do not raise).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Final, Sequence

logger = logging.getLogger(__name__)

DEFAULT_MIN_INTERVAL_SECONDS: Final[float] = 0.3
DEFAULT_RETRIES: Final[int] = 3
MAX_RETRY_SLEEP_SECONDS: Final[float] = 4.0

# HTTP 429 / quota exhaustion markers that make a retry worthwhile.
RETRY_MARKERS: Final[tuple[str, ...]] = (
    "429",
    "quota",
    "resource has been exhausted",
)

# Environment variables that must reach the child process to allow
# non-interactive keyring access (mirrors the signals `_upload_json_to_gdrive`
# helper in signals/interfaces/cli/main.py).
GOG_ENV_PASSTHROUGH_KEYS: Final[tuple[str, ...]] = (
    "GOG_KEYRING_PASSWORD",
    "GOG_HOME",
    "GOG_ACCESS_TOKEN",
)


@dataclass(frozen=True)
class GogOutcome:
    """Result of one `gog` subprocess invocation."""

    returncode: int
    stdout: str
    stderr: str


def gog_available(binary: str = "gog") -> bool:
    """Return True when the gog binary is present on PATH."""
    return shutil.which(binary) is not None


class GogClient:
    """Rate-limited runner for gog subcommands with quota-aware retries."""

    def __init__(
        self,
        *,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
        binary: str = "gog",
    ) -> None:
        self._binary = binary
        self._min_interval_seconds = min_interval_seconds
        self._last_invocation_monotonic: float | None = None

    def _throttle(self) -> None:
        """Keep at least `min_interval_seconds` between consecutive runs."""
        if self._min_interval_seconds <= 0:
            return
        now = time.monotonic()
        if self._last_invocation_monotonic is not None:
            remaining = self._min_interval_seconds - (now - self._last_invocation_monotonic)
            if remaining > 0:
                time.sleep(remaining)
        self._last_invocation_monotonic = time.monotonic()

    def run(
        self,
        args: Sequence[str],
        *,
        force: bool = False,
        retries: int = DEFAULT_RETRIES,
    ) -> GogOutcome:
        """Run one gog command and return its outcome without raising.

        The `--no-input` global flag is appended right after the subcommand
        (gog accepts global flags there and refuses interactive prompts in CI).
        `-y` is appended only for destructive calls that must skip confirmation.
        Quota/backoff markers trigger retries with 1/2/4 second sleeps.
        """
        command: list[str] = [self._binary]
        if args:
            command.append(args[0])
            command.append("--no-input")
            command.extend(args[1:])
        else:
            command.append("--no-input")
        if force:
            command.append("-y")
        account = os.environ.get("GOG_ACCOUNT", "").strip()
        if account:
            command.extend(["--account", account])

        # Mirror the signals CLI: pass the parent environment through, keeping
        # the keyring/token variables the agent needs for non-interactive auth.
        env = os.environ.copy()
        for key in GOG_ENV_PASSTHROUGH_KEYS:
            if key in os.environ:
                env[key] = os.environ[key]

        attempts = retries + 1
        for attempt in range(attempts):
            self._throttle()
            try:
                proc = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
            except OSError as exc:
                logger.error(
                    "gog invocation failed to start",
                    extra={"command": command, "error": str(exc)},
                )
                return GogOutcome(returncode=-1, stdout="", stderr=str(exc))

            if proc.returncode == 0:
                return GogOutcome(
                    returncode=proc.returncode,
                    stdout=proc.stdout or "",
                    stderr=proc.stderr or "",
                )

            combined_output = f"{proc.stderr or ''} {proc.stdout or ''}".lower()
            retryable = any(marker in combined_output for marker in RETRY_MARKERS)
            if retryable and attempt < attempts - 1:
                sleep_seconds = min(2**attempt, MAX_RETRY_SLEEP_SECONDS)
                logger.warning(
                    "gog quota exhaustion; retrying",
                    extra={
                        "command": command,
                        "attempt": attempt + 1,
                        "sleep_seconds": sleep_seconds,
                    },
                )
                time.sleep(sleep_seconds)
                continue
            return GogOutcome(
                returncode=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
            )
        # Defensive: the loop always returns; keep a sane fallback.
        return GogOutcome(returncode=-1, stdout="", stderr="unreachable retry loop")

    def gog_available(self) -> bool:
        """Return True when this client's binary is present on PATH."""
        return gog_available(self._binary)
