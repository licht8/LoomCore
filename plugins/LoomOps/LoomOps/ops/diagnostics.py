"""Structured host health scan aggregating the read-only sources.

`ops_scan_health` is the priority tool: it collects failed systemd units, OOM
events, CPU, disk, load and recent journal errors and returns a report the
assistant can reason over. Every source is wrapped: a broken source adds a note
to ``host_access.missing`` instead of producing an empty report.
"""

from __future__ import annotations

import math
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import bounded_int, logs, monitoring
from .services import failed_units

PROC_MOUNTS = Path("/proc/mounts")

# Module-local alias so tests can patch stat without touching the stdlib module.
_stat = os.stat

# Filesystems that do not represent real storage and would only add noise.
PSEUDO_FILESYSTEMS = {
    "autofs",
    "bpf",
    "binfmt_misc",
    "cgroup",
    "cgroup2",
    "configfs",
    "debugfs",
    "devpts",
    "devtmpfs",
    "fusectl",
    "hugetlbfs",
    "mqueue",
    "nsfs",
    "proc",
    "pstore",
    "ramfs",
    "rpc_pipefs",
    "securityfs",
    "squashfs",
    "sysfs",
    "tmpfs",
    "tracefs",
}

DEFAULT_HOURS = 24
MAX_HOURS = 24 * 30
DEFAULT_JOURNAL_WINDOW_MINUTES = 60
MAX_JOURNAL_WINDOW_MINUTES = 24 * 60
DEFAULT_CPU_THRESHOLD = 80.0
DEFAULT_DISK_THRESHOLD = 90.0
DEFAULT_LOAD_THRESHOLD = 2.0


def _number(
    arguments: dict[str, Any], key: str, env_name: str, default: float
) -> float:
    raw = arguments.get(key)
    if raw is None:
        raw = os.environ.get(env_name)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    # NaN and infinity would silently disable or distort detection: reject them.
    if not math.isfinite(value):
        return default
    return value


def _read_mounts() -> list[tuple[str, str]] | None:
    try:
        text = PROC_MOUNTS.read_text(encoding="utf-8")
    except OSError:
        return None
    mounts: list[tuple[str, str]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            mounts.append((parts[1], parts[2]))
    return mounts


def _service_findings() -> tuple[list[dict[str, Any]], list[str]]:
    try:
        units = failed_units()
    except RuntimeError as exc:
        return [], [f"systemd: {exc}"]
    return (
        [
            {
                "severity": "critical",
                "category": "service",
                "detail": f"systemd unit {unit} is failed",
                "evidence": {"unit": unit},
            }
            for unit in units
        ],
        [],
    )


def _oom_findings(hours: int) -> tuple[list[dict[str, Any]], list[str]]:
    missing: list[str] = []
    text = ""
    source = "journal"
    if logs.journal_available():
        try:
            text = logs._run_journal(
                [
                    "--since",
                    f"{hours} hours ago",
                    "--grep",
                    "Out of memory|oom-kill|Killed process",
                ]
            )
        except RuntimeError as exc:
            missing.append(f"OOM scan (journal): {exc}")
    # Fall back to dmesg whenever the journal produced no matches: a journald
    # that does not carry kernel messages (or a rotated/subset mount) would
    # otherwise hide OOM-kill records that only dmesg surfaces. journalctl
    # prints a non-empty "-- No entries --" marker on a successful empty query,
    # so gate on real lines rather than the raw text.
    if not logs._journal_lines(text):
        source = "dmesg"
        try:
            text = "\n".join(monitoring.dmesg({"lines": 2000})["entries"])
        except RuntimeError as exc:
            missing.append(f"OOM scan (dmesg): {exc}")
    findings: list[dict[str, Any]] = []
    for line in text.splitlines():
        lowered = line.lower()
        if "out of memory" in lowered or "oom-kill" in lowered or "killed process" in lowered:
            findings.append(
                {
                    "severity": "critical",
                    "category": "oom",
                    "detail": line.strip()[:300],
                    "evidence": {"source": source},
                }
            )
    return findings, missing


def _disk_findings(threshold: float) -> tuple[list[dict[str, Any]], list[str]]:
    mounts = _read_mounts()
    if mounts is None:
        return [], ["/proc/mounts is not readable"]
    findings: list[dict[str, Any]] = []
    seen: set[Any] = set()
    unmeasured = 0
    for target, filesystem in mounts:
        if filesystem in PSEUDO_FILESYSTEMS:
            continue
        try:
            device = _stat(target).st_dev
            usage = shutil.disk_usage(target)
        except OSError:
            unmeasured += 1
            continue
        if not usage.total:
            unmeasured += 1
            continue
        # Deduplicate by backing device, not mount point: one device mounted at
        # several paths (e.g. container overlay layers) must report usage once.
        # Mark it seen only after a successful read, so an unreadable mount does
        # not hide a later readable mount on the same device.
        if device in seen:
            continue
        seen.add(device)
        percent = usage.used / usage.total * 100
        if percent >= threshold:
            findings.append(
                {
                    "severity": "critical" if percent >= 95 else "warning",
                    "category": "disk",
                    "detail": f"{target} is {percent:.1f}% full ({filesystem})",
                    "evidence": {
                        "mount": target,
                        "filesystem": filesystem,
                        "used_percent": round(percent, 1),
                    },
                }
            )
    missing = (
        [f"{unmeasured} mount(s) could not be measured"] if unmeasured else []
    )
    return findings, missing


def _load_finding(threshold: float) -> dict[str, Any] | None:
    load = monitoring.load_average()
    if load is None:
        return None
    cpu_count = os.cpu_count() or 1
    per_cpu = load[0] / cpu_count
    if per_cpu <= threshold:
        return None
    return {
        "severity": "warning",
        "category": "load",
        "detail": f"load average {load[0]:.2f} over {cpu_count} CPUs ({per_cpu:.2f}/CPU)",
        "evidence": {
            "load1": load[0],
            "cpu_count": cpu_count,
            "load_per_cpu": round(per_cpu, 2),
        },
    }


def _cpu_finding(threshold: float) -> dict[str, Any] | None:
    percent = monitoring.cpu_percent()
    if percent is None or percent < threshold:
        return None
    return {
        "severity": "warning",
        "category": "cpu",
        "detail": f"CPU usage {percent:.1f}% is above {threshold:.0f}%",
        "evidence": {"cpu_percent": percent, "threshold": threshold},
    }


def _journal_findings(minutes: int) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        result = logs.journal_errors(
            {"since": f"{minutes} minutes ago", "limit": 100}
        )
    except RuntimeError as exc:
        return [], [f"journal scan: {exc}"]
    return (
        [
            {
                "severity": "warning",
                "category": "journal",
                "detail": line.strip()[:300],
                "evidence": {"source": result["source"]},
            }
            for line in result["entries"]
        ],
        [],
    )


def _summarize(findings: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"critical": 0, "warning": 0, "info": 0}
    for finding in findings:
        severity = str(finding.get("severity") or "info")
        counts[severity] = counts.get(severity, 0) + 1
    counts["total"] = len(findings)
    if not findings:
        counts["status"] = "healthy"
    elif counts["critical"]:
        counts["status"] = "critical"
    else:
        counts["status"] = "degraded"
    return counts


def _report_text(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        f"Health scan: {summary['status']} "
        f"({summary['critical']} critical, {summary['warning']} warning, "
        f"{summary['info']} info)"
    ]
    for finding in report["findings"]:
        lines.append(f"[{finding['severity']}] {finding['category']}: {finding['detail']}")
    for note in report["host_access"]["missing"]:
        lines.append(f"[access] {note}")
    if not report["findings"]:
        lines.append("No anomalies detected.")
    return "\n".join(lines)


def scan_health(arguments: dict[str, Any]) -> dict[str, Any]:
    hours = bounded_int(arguments.get("hours"), DEFAULT_HOURS, MAX_HOURS)
    minutes = bounded_int(
        arguments.get("journal_window"),
        DEFAULT_JOURNAL_WINDOW_MINUTES,
        MAX_JOURNAL_WINDOW_MINUTES,
    )
    cpu_threshold = _number(arguments, "cpu_threshold", "LOOMOPS_CPU_THRESHOLD", DEFAULT_CPU_THRESHOLD)
    disk_threshold = _number(
        arguments, "disk_threshold", "LOOMOPS_DISK_THRESHOLD", DEFAULT_DISK_THRESHOLD
    )
    load_threshold = _number(
        arguments, "load_per_cpu", "LOOMOPS_LOAD_THRESHOLD", DEFAULT_LOAD_THRESHOLD
    )

    findings: list[dict[str, Any]] = []
    missing: list[str] = []

    for source_findings, source_missing in (
        _service_findings(),
        _oom_findings(hours),
        _disk_findings(disk_threshold),
        _journal_findings(minutes),
    ):
        findings.extend(source_findings)
        missing.extend(source_missing)

    for single in (_load_finding(load_threshold), _cpu_finding(cpu_threshold)):
        if single is not None:
            findings.append(single)

    pid_host = monitoring.host_pid_active()
    journal_ok = logs.journal_available()
    log_roots = logs.log_roots()
    log_root_readable = any(
        root.is_dir() and os.access(root, os.R_OK) for root in log_roots
    )
    if not pid_host:
        missing.append(
            "host PID namespace is not mounted (pid: host); process and service "
            "data is container-scoped"
        )
    if not journal_ok:
        missing.append(
            "journald is not available (no journal files under "
            f"{[str(path) for path in logs.journal_dirs()]}); using plain log "
            "files. Enable persistent journald on the host or mount /run/log/journal"
        )
    if not log_root_readable:
        missing.append(
            f"no readable log root among {[str(root) for root in log_roots]}"
        )

    report: dict[str, Any] = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "host_access": {
            "pid_namespace_host": pid_host,
            "journal_available": journal_ok,
            "log_root_readable": log_root_readable,
            "missing": missing,
        },
        "thresholds": {
            "hours": hours,
            "cpu_percent": cpu_threshold,
            "disk_percent": disk_threshold,
            "load_per_cpu": load_threshold,
            "journal_window_minutes": minutes,
        },
        "findings": findings,
        "summary": _summarize(findings),
    }
    return {"output": _report_text(report), **report}
