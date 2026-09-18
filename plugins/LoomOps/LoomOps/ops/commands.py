"""Allow-listed command execution with timeouts and audit logging."""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from . import audit_event

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 600
MAX_OUTPUT_CHARS = 20_000

# Read the pipes in bounded chunks so a chatty command cannot exhaust memory,
# and give a killed process group a moment to release its pipes before we stop
# waiting for the reader threads.
_READ_CHUNK_CHARS = 8192
_REAP_GRACE_SECONDS = 5.0

# SIGKILL does not exist on Windows; fall back to SIGTERM there.
_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)

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


def _read_limited(stream: Any, sink: list[str], dropped: list[int]) -> None:
    """Read a pipe in bounded chunks, keeping at most MAX_OUTPUT_CHARS chars.

    Every char past the cap is counted in ``dropped`` so the caller can report
    truncation even when the kept prefix lands exactly on the cap.
    """
    kept = 0
    try:
        while True:
            chunk = stream.read(_READ_CHUNK_CHARS)
            if not chunk:
                break
            if kept < MAX_OUTPUT_CHARS:
                sink.append(chunk)
                kept += len(chunk)
            else:
                dropped[0] += len(chunk)
    except (OSError, ValueError):
        # The stream can be closed out from under us when a timed-out process
        # group is killed; whatever was read so far is still reported.
        pass
    finally:
        with suppress(OSError):
            stream.close()


def _truncate_limited(chunks: list[str], dropped: int) -> tuple[str, bool]:
    """Join capped chunks and report truncation from the drained dropped count."""
    text = "".join(chunks)
    if len(text) > MAX_OUTPUT_CHARS:
        # The final read may have overshot the cap; drop the excess and fold it
        # into the count so the marker reflects everything that was discarded.
        dropped += len(text) - MAX_OUTPUT_CHARS
        text = text[:MAX_OUTPUT_CHARS]
    if dropped <= 0:
        return text, False
    return f"{text}\n... [truncated {dropped} chars]", True


def _kill_process_group(proc: subprocess.Popen[Any]) -> None:
    """Kill the child and every process in its group, then reap it."""
    killpg = getattr(os, "killpg", None)
    getpgid = getattr(os, "getpgid", None)
    if killpg is not None and getpgid is not None:
        try:
            killpg(getpgid(proc.pid), _KILL_SIGNAL)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
    else:
        # Windows has no process groups in this sense.
        proc.kill()
    with suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=_REAP_GRACE_SECONDS)


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
    if argv[0] != executable:
        raise ValueError(
            f"Command must be given by name, not by path: '{argv[0]}'"
        )
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
    resolved = shutil.which(executable)
    if resolved is None:
        raise ValueError(f"Command '{executable}' is not installed in this container")
    # Execute the absolute path resolved above, never the caller-supplied
    # argv[0]: a PATH change between the check and the spawn cannot swap the
    # binary (TOCTOU).
    run_argv = [resolved, *argv[1:]]

    timeout = command_timeout()
    try:
        # start_new_session puts the child in its own process group so a
        # timeout can kill the whole tree: killing only the direct child
        # leaves grandchildren holding the pipes open, which makes the
        # parent block while reaping past the deadline.
        proc = subprocess.Popen(
            run_argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            start_new_session=True,
        )
    except OSError as exc:
        raise RuntimeError(f"Failed to run '{executable}': {exc}") from exc

    # Drain both pipes in bounded chunks on background threads: this keeps
    # memory flat (we stop appending at MAX_OUTPUT_CHARS) and avoids the
    # deadlock of reading one pipe while the other fills up.
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    stdout_dropped = [0]
    stderr_dropped = [0]
    readers = [
        threading.Thread(
            target=_read_limited,
            args=(proc.stdout, stdout_chunks, stdout_dropped),
            daemon=True,
        ),
        threading.Thread(
            target=_read_limited,
            args=(proc.stderr, stderr_chunks, stderr_dropped),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    timed_out = False
    try:
        return_code: int | None = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(proc)
        return_code = None

    deadline = time.monotonic() + _REAP_GRACE_SECONDS
    for reader in readers:
        reader.join(max(0.0, deadline - time.monotonic()))
    # A reader still alive here means a descendant is holding the pipe open, so
    # the output below is incomplete rather than a clean, complete result.
    output_incomplete = any(reader.is_alive() for reader in readers)

    stdout, stdout_truncated = _truncate_limited(stdout_chunks, stdout_dropped[0])
    stderr, stderr_truncated = _truncate_limited(stderr_chunks, stderr_dropped[0])
    truncated = stdout_truncated or stderr_truncated

    if timed_out:
        audit_error = audit_event(
            "ops_run_command", argv=run_argv, timeout=timeout, timed_out=True
        )
        return {
            "output": f"Command timed out after {timeout}s: {' '.join(run_argv)}",
            "argv": run_argv,
            "exit_code": None,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": truncated,
            "timed_out": True,
            "audit_error": audit_error,
        }

    audit_error = audit_event(
        "ops_run_command", argv=run_argv, exit_code=return_code
    )
    lines = [f"$ {' '.join(run_argv)}", f"exit code: {return_code}"]
    if stdout:
        lines.append(stdout)
    if stderr:
        lines.append(f"stderr:\n{stderr}")
    if output_incomplete:
        lines.append(
            "note: a descendant process kept the output pipes open; "
            "captured output may be incomplete"
        )
    return {
        "output": "\n".join(lines),
        "argv": run_argv,
        "exit_code": return_code,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": truncated,
        "timed_out": False,
        "incomplete": output_incomplete,
        "audit_error": audit_error,
    }
