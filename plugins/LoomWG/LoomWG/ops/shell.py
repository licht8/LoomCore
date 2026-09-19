"""Run shell commands inside the plugin container.

The container holds the NET_ADMIN capability, so iptables/nft/sysctl commands
run directly (no nsenter/chroot).
"""

from __future__ import annotations

import subprocess


def _output_text(raw: str | bytes | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw


def run_shell(command: str, timeout: int = 300) -> str:
    """Run a command via /bin/sh -c and return its stdout.

    Raises RuntimeError with the captured stderr/stdout on a non-zero exit or a
    timeout.
    """
    try:
        result = subprocess.run(
            ["/bin/sh", "-c", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        details = _output_text(err.stderr).strip() or _output_text(err.stdout).strip()
        raise RuntimeError(
            f"Command timed out after {timeout}s: {details or 'no details'}"
        ) from err
    if result.returncode:
        details = result.stderr.strip() or result.stdout.strip() or "no details"
        raise RuntimeError(
            f"Command exited with code {result.returncode}: {details}"
        )
    return result.stdout.strip()
