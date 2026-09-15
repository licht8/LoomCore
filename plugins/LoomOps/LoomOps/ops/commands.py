"""Allow-listed command execution with timeouts and audit logging."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import audit_event

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 600
MAX_OUTPUT_CHARS = 20_000

# Basenames that can execute arbitrary code or otherwise escape the allowlist
# even with ``shell=False``: interpreters, wrappers and process/session
# launchers. They are refused even when an operator explicitly lists them in
# ``LOOMOPS_ALLOWED_COMMANDS`` — allowlisting one of these is equivalent to
# granting unrestricted command execution.
DENIED_COMMANDS = frozenset(
    {
        "sh", "bash", "dash", "zsh", "ksh", "csh", "tcsh", "fish", "ash",
        "mksh", "busybox",
        "python", "python2", "python3", "perl", "ruby", "node", "php", "lua",
        "tclsh", "awk", "gawk", "mawk", "nawk", "sed",
        "env", "xargs", "find", "nice", "ionice", "timeout", "stdbuf",
        "nohup", "setsid", "time", "watch",
        "sudo", "doas", "su",
        "ssh", "nsenter", "chroot", "unshare", "docker", "podman",
        "make", "git",
    }
)


def allowed_commands() -> set[str]:
    """Executable basenames the admin allowed (empty means deny all)."""
    raw = os.environ.get("LOOMOPS_ALLOWED_COMMANDS", "")
    return {part for part in (chunk.strip() for chunk in raw.replace(",", " ").split()) if part}


def command_timeout() -> int:
    try:
        value = int(os.environ.get("LOOMOPS_COMMAND_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS)))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return max(1, min(value, MAX_TIMEOUT_SECONDS))


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    dropped = len(text) - MAX_OUTPUT_CHARS
    return f"{text[:MAX_OUTPUT_CHARS]}\n... [truncated {dropped} chars]", True


def _split_command(command: Any) -> list[str]:
    if isinstance(command, str):
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise ValueError(f"Cannot parse command: {exc}") from exc
    elif isinstance(command, list) and all(isinstance(part, str) for part in command):
        argv = list(command)
    else:
        raise ValueError("command must be a string or a list of strings")
    if not argv:
        raise ValueError("command must not be empty")
    return argv


def run_command(arguments: dict[str, Any]) -> dict[str, Any]:
    argv = _split_command(arguments.get("command"))
    executable = Path(argv[0]).name
    allowed = allowed_commands()
    if not allowed:
        raise ValueError(
            "No commands are allowed; set LOOMOPS_ALLOWED_COMMANDS (comma separated)"
        )
    if executable not in allowed:
        raise ValueError(
            f"Command '{executable}' is not allowed. Allowed: {', '.join(sorted(allowed))}"
        )
    if executable in DENIED_COMMANDS:
        raise ValueError(
            f"Command '{executable}' can execute arbitrary code and is never "
            f"allowed, even when listed in LOOMOPS_ALLOWED_COMMANDS"
        )
    if shutil.which(executable) is None:
        raise ValueError(f"Command '{executable}' is not installed in this container")

    timeout = command_timeout()
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        audit_error = audit_event(
            "ops_run_command", argv=argv, timeout=timeout, timed_out=True
        )
        return {
            "output": f"Command timed out after {timeout}s: {' '.join(argv)}",
            "argv": argv,
            "timed_out": True,
            "audit_error": audit_error,
        }
    except OSError as exc:
        raise RuntimeError(f"Failed to run '{executable}': {exc}") from exc

    stdout, stdout_truncated = _truncate(completed.stdout or "")
    stderr, stderr_truncated = _truncate(completed.stderr or "")
    audit_error = audit_event(
        "ops_run_command", argv=argv, exit_code=completed.returncode
    )
    lines = [f"$ {' '.join(argv)}", f"exit code: {completed.returncode}"]
    if stdout:
        lines.append(stdout)
    if stderr:
        lines.append(f"stderr:\n{stderr}")
    return {
        "output": "\n".join(lines),
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": stdout_truncated or stderr_truncated,
        "audit_error": audit_error,
    }
