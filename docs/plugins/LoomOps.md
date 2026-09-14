# LoomOps plugin

System-operations plugin (`plugins/LoomOps`) enabled by default. It runs as an MCP
subprocess and talks JSON-RPC over stdio. It offers allow-listed commands,
confined file access, process control, systemd status, read-only host
observability (metrics, logs, diagnostics) — the operations a thin core
deliberately does not carry (RULES 1).

- Audit log: `LOOMOPS_AUDIT_LOG` (default `plugins/LoomOps/tmp/loomops-audit.log`)
- Allowed commands: `LOOMOPS_ALLOWED_COMMANDS` (comma/space separated, **empty
  denies all**)
- Allowed file roots: `LOOMOPS_ALLOWED_PATHS` (comma or `:` separated, **empty
  denies all**)
- Command timeout: `LOOMOPS_COMMAND_TIMEOUT` (default `30`, capped at `600`)
- Log roots (file fallback): `LOOMOPS_LOG_ROOTS` (comma or `:` separated, default
  `/host/var/log`)
- Log line default: `LOOMOPS_MAX_LOG_LINES` (default `200`, capped at `5000`)
- Health-scan thresholds: `LOOMOPS_CPU_THRESHOLD` (default `80`),
  `LOOMOPS_DISK_THRESHOLD` (default `90`), `LOOMOPS_LOAD_THRESHOLD` (default
  `2.0`, load per CPU)

## Manifest & self-check

`plugins/LoomOps/manifest.json` declares the plugin id/version, the `ops_` tool
prefix, host networking and its environment variables (schema:
`docs/architecture/PLUGIN_MANIFEST.md`). For host observability it requests the
`SYSLOG` capability and host PID namespace (`pid: "host"`) and read-only mounts of
`/var/log`, `/var/log/journal`, `/run/systemd` and `/etc` under `/host/...` (see
[Host observability](#host-observability)). Read-only tools work even when those
grants are absent; the tools then report what is missing. The core reads and
validates the manifest during discovery and warns on mismatch (never blocks).

## Layout

`plugins/LoomOps/main.py` is a thin launcher (`from LoomOps.server import run_server`).
The implementation lives in the `LoomOps` package:

- `server.py`, `protocol.py`, `results.py` — MCP wiring, JSON-RPC stdio, result helper.
- `schemas.py` — `TOOLS_SCHEMA`; `tools.py` — handlers + `HANDLERS` map.
- `ops/` — `commands.py` (allow-listed exec), `files.py` (confined read/write),
  `processes.py` (`/proc` scan + signals), `services.py` (systemd),
  `system_info.py` (disk/network), `monitoring.py` (metrics/dmesg/connections),
  `logs.py` (journald + file fallback), `diagnostics.py` (health scan).
  `ops/__init__.py` holds the audit helper.

## Tools

| Tool | Risk | Description |
|---|---|---|
| `ops_run_command` | dangerous | Run an allow-listed command (no shell). |
| `ops_read_file` | safe | Read a text file inside the allowed roots (≤256 KB). |
| `ops_write_file` | dangerous | Atomically write a text file inside the allowed roots. |
| `ops_list_processes` | safe | List processes from `/proc` (host-wide with `pid: host`). |
| `ops_kill_process` | dangerous | Send `TERM`/`KILL`/`INT`/`HUP` to one pid. |
| `ops_systemctl_status` | safe | `systemctl is-active` + `status` for one unit. |
| `ops_systemctl_restart` | dangerous | `systemctl restart` one unit. |
| `ops_disk_usage` | safe | Total/used/free space for a path. |
| `ops_network_status` | safe | Interfaces with counters and addresses. |
| `ops_system_metrics` | safe | Load average, CPU, memory and uptime. |
| `ops_log_search` | safe | Search journald (or log files) read-only. |
| `ops_dmesg` | safe | Read the kernel ring buffer. |
| `ops_failed_units` | safe | List failed systemd units. |
| `ops_network_connections` | safe | Active connections via `ss`. |
| `ops_scan_health` | safe | Structured host health/anomaly report. |

Risk is enforced by the core (`src/core/security.py:classify_tool_risk`); the four
`dangerous` tools (also listed in `explicit_dangerous`) require explicit admin
approval before running. Every other tool is read-only and runs without approval.

## Safety model

- **No shell.** `ops_run_command` splits the command with `shlex` and runs the argv
  directly (`shell=False`). The executable basename must be in
  `LOOMOPS_ALLOWED_COMMANDS` and resolvable via `shutil.which`; anything else is
  rejected before a process starts.
- **Deny by default.** With `LOOMOPS_ALLOWED_COMMANDS` unset, every
  `ops_run_command` is refused. Likewise `ops_read_file`/`ops_write_file` refuse
  all paths until `LOOMOPS_ALLOWED_PATHS` is set.
- **Path confinement.** Paths are resolved (symlink-safe) and rejected if they
  escape the allowed roots — `..` traversal is not possible.
- **Bounded output.** Command output is capped at 20 000 characters and file reads
  at 256 KB; commands are killed after the timeout. Log/dmesg output is bounded by
  line count.
- **Read-only observability.** Metrics, logs, diagnostics, processes and
  connections only read shared host state; they never shell out with a shell and
  never mutate anything.
- **Audit.** Every mutating call (`ops_run_command`, `ops_write_file`,
  `ops_kill_process`, `ops_systemctl_restart`) appends one JSON line (UTC
  timestamp) to the audit log. Audit failures never abort the action; the error is
  surfaced in the result as `audit_error`.

## Parameters

- `ops_run_command`: `command` (required; string like `df -h`, or an argv list).
- `ops_read_file`: `path`. `ops_write_file`: `path`, `content`.
- `ops_list_processes`: `limit` (default 200, max 2000).
- `ops_kill_process`: `pid`; `signal` (`TERM` default, `KILL`, `INT`, `HUP`).
- `ops_systemctl_status` / `ops_systemctl_restart`: `service`.
- `ops_disk_usage`: `path` (default `/`).
- `ops_log_search`: `source` (`auto`/`journal`/`files`), `query`, `since`
  (e.g. `1 hour ago`), `unit`, `limit` (default 200).
- `ops_dmesg`: `lines` (default 200, max 5000).
- `ops_network_connections`: `limit` (default 200).
- `ops_scan_health`: `hours` (default 24), `journal_window` (minutes, default 60),
  `cpu_threshold` (default 80), `disk_threshold` (default 90),
  `load_per_cpu` (default 2.0).

## Host observability

The `hub` container uses `network_mode: host` **and grants the plugin read-only
host visibility** so `ops_scan_health` and the log/metrics tools describe the
whole host, not just the container:

- `pid: host` — `/proc` is the host PID namespace, so `ops_list_processes` and the
  process findings in `ops_scan_health` see host processes, and `ops_kill_process`
  can target them (still approval-gated + audited).
- `cap_add: [SYSLOG]` — needed to read `/dev/kmsg` via `ops_dmesg`.
- Read-only mounts under `/host/...` (host paths are never shadowed):
  `/var/log → /host/var/log`, `/var/log/journal → /host/journal`,
  `/run/systemd → /run/systemd`, `/etc → /host/etc`. The `/etc` mount is optional
  and only used to read configuration.
- The `systemd` package plus the read-only `/run/systemd` mount expose
  systemd's private socket, which `systemctl` uses when running as root. The
  host D-Bus socket `/run/dbus/system_bus_socket` is **not** mounted; if your
  host's `systemctl` needs D-Bus, re-add that bind mount.

**Deliberately not granted:** `privileged`, the Docker socket, and the
`SYS_PTRACE`/`DAC_READ_SEARCH` capabilities. Observability is read-only; the only
host-mutating tools remain the existing approval-gated ones (`ops_kill_process`,
`ops_systemctl_restart`, `ops_run_command`, `ops_write_file`), and `hub`'s only
writable host mount is still `/etc/wireguard`.

### Missing-access behaviour

Host access is not silently assumed. If a grant is absent:

- At startup the core logs a permission warning (`check_permissions`), e.g. when
  `pid: host` is declared but `/proc` does not look like the host PID namespace.
- At runtime each tool degrades gracefully and says what is missing: `ops_dmesg`
  returns an actionable error when `dmesg` is unavailable/restricted;
  `ops_systemctl_*`/`ops_failed_units` report when systemd is unreachable; and
  `ops_scan_health` puts the reason in `data.host_access.missing` instead of
  returning an empty report.

The default Compose stack ships these grants. On a host where the mounts are
blocked (e.g. SELinux), use `chcon`/`ausearch` to diagnose — never relabel the
host's `/etc` or `/var/log` with the `z` mount flag.

## Container reality

Even with the grants above:

- `ops_kill_process` still cannot signal processes owned by other users unless the
  container has the privileges to do so; permission errors are returned verbatim.
- `ops_systemctl_status` / `ops_systemctl_restart` / `ops_failed_units` return an
  actionable error when no systemd is reachable (`systemctl` absent or
  `/run/systemd/system` missing).
- File reads/writes are confined to `LOOMOPS_ALLOWED_PATHS` (which default to
  deny-all), not to the `/host/...` mounts directly.

These are deliberate: the plugin stays read-only for observability and returns a
clear error instead of silently doing nothing.
