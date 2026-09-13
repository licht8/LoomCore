# LoomOps plugin

Baseline system-operations plugin (`plugins/LoomOps`) enabled by default. It runs
as an MCP subprocess and talks JSON-RPC over stdio. It offers allow-listed
commands, confined file access, process control, systemd status and read-only
system/network facts — the operations a thin core deliberately does not carry
(RULES 1).

- Audit log: `LOOMOPS_AUDIT_LOG` (default `plugins/LoomOps/tmp/loomops-audit.log`)
- Allowed commands: `LOOMOPS_ALLOWED_COMMANDS` (comma/space separated, **empty
  denies all**)
- Allowed file roots: `LOOMOPS_ALLOWED_PATHS` (comma or `:` separated, **empty
  denies all**)
- Command timeout: `LOOMOPS_COMMAND_TIMEOUT` (default `30`, capped at `600`)

## Manifest & self-check

`plugins/LoomOps/manifest.json` declares the plugin id/version, the `ops_` tool
prefix, host networking and its environment variables (schema:
`docs/architecture/PLUGIN_MANIFEST.md`). It requests no Linux capabilities and no
mounts: every read-only query works inside the unprivileged container. The core
reads and validates it during discovery and warns on mismatch (never blocks).

## Layout

`plugins/LoomOps/main.py` is a thin launcher (`from LoomOps.server import run_server`).
The implementation lives in the `LoomOps` package:

- `server.py`, `protocol.py`, `results.py` — MCP wiring, JSON-RPC stdio, result helper.
- `schemas.py` — `TOOLS_SCHEMA`; `tools.py` — handlers + `HANDLERS` map.
- `ops/` — `commands.py` (allow-listed exec), `files.py` (confined read/write),
  `processes.py` (`/proc` scan + signals), `services.py` (systemd),
  `system_info.py` (disk/network). `ops/__init__.py` holds the audit helper.

## Tools

| Tool | Risk | Description |
|---|---|---|
| `ops_run_command` | dangerous | Run an allow-listed command (no shell). |
| `ops_read_file` | safe | Read a text file inside the allowed roots (≤256 KB). |
| `ops_write_file` | dangerous | Atomically write a text file inside the allowed roots. |
| `ops_list_processes` | safe | List container-visible processes from `/proc`. |
| `ops_kill_process` | dangerous | Send `TERM`/`KILL`/`INT`/`HUP` to one pid. |
| `ops_systemctl_status` | safe | `systemctl is-active` + `status` for one unit. |
| `ops_systemctl_restart` | dangerous | `systemctl restart` one unit. |
| `ops_disk_usage` | safe | Total/used/free space for a path. |
| `ops_network_status` | safe | Interfaces with counters and addresses. |

Risk is enforced by the core (`src/core/security.py:classify_tool_risk`); the four
`dangerous` tools (also listed in `explicit_dangerous`) require explicit admin
approval before running.

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
  at 256 KB; commands are killed after the timeout.
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

## Container reality

The `hub` container uses `network_mode: host` but has **no `pid: host` and is not
privileged**, so:

- `ops_list_processes` / `ops_kill_process` see the container's processes, not the
  host's, and cannot kill processes owned by other users.
- `ops_systemctl_status` / `ops_systemctl_restart` return an actionable error when
  no systemd is reachable (`systemctl` absent or `/run/systemd/system` missing) —
  this is expected on the default Compose stack.
- Reads/writes are confined to whatever is bind-mounted into the container.

These are deliberate: the plugin never gains host escape, and returns a clear error
instead of silently doing nothing.
