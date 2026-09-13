# TS: LoomOps host observability and health scan

> Filename follows the `docs/tasks/<name>/TZ_RU.md` convention (RULES 16); the
> content is English per RULES 0.

## Goal

Give LoomOps **read-only visibility of the whole host** and add the priority tool
`ops_scan_health` — a structured anomaly report (failed units, OOM events, high
CPU, nearly-full disks, high load, recent journal errors) that the assistant can
reason over. Deny-by-default command/path allowlists and approval-gated mutations
are preserved; the trust boundary is expanded consciously and documented.

## Non-goals

- No `privileged: true`, no `docker.sock`.
- No `SYS_PTRACE`, no `DAC_READ_SEARCH`.
- No writable host mounts beyond the existing `/etc/wireguard`.
- No persistent anomaly database or daemon: `ops_scan_health` is on-demand.
- No changes to `frontend/`, `src/api/`, `src/llm/`, `src/interfaces/`,
  `plugins/LoomWG/`.
- No new third-party dependency.

## Part 1 — Core manifest (`src/plugins/manifest.py`)

- Add `CAPABILITIES["SYSLOG"] = 34` (do **not** add `SYS_PTRACE`/`DAC_READ_SEARCH`).
- `_validate_permissions`: accept an optional `pid` key whose value must be
  `"host"` or `"container"`; otherwise add
  `"permissions.pid must be 'host' or 'container'"`.
- Add `host_pid_looks_active() -> bool`: read `/proc/1/comm` (fallback
  `/proc/1/status` `Name:`); True only when init is `systemd`/`init`; `OSError`
  → False. Use a module-level `PROC_ROOT = Path("/proc")`.
- `check_permissions`: when `permissions.get("pid") == "host"` and the host PID
  namespace does not look active, append
  `"pid namespace 'host' is declared but /proc does not look like the host PID namespace"`.
- `PluginManifest` is unchanged.
- Document `pid` + `SYSLOG` in `docs/architecture/PLUGIN_MANIFEST.md`.

## Part 2 — Grant host observability (compose / image / env)

- `docker-compose.yml` `hub`:
  - `pid: host`;
  - `cap_add` gains `SYSLOG`;
  - read-only host mounts: `/var/log:/host/var/log:ro`,
    `/var/log/journal:/host/journal:ro`, `/run/systemd:/run/systemd:ro`,
    `/run/dbus/system_bus_socket:/run/dbus/system_bus_socket`,
    `/etc:/host/etc:ro`;
  - keep `/etc/wireguard:/etc/wireguard:z` and `/lib/modules:/lib/modules:ro`;
  - `environment` gains `LOOMOPS_ALLOWED_COMMANDS`, `LOOMOPS_ALLOWED_PATHS`,
    `LOOMOPS_AUDIT_LOG`, `LOOMOPS_COMMAND_TIMEOUT:-30`,
    `LOOMOPS_LOG_ROOTS:-/host/var/log`, `LOOMOPS_MAX_LOG_LINES:-200`,
    `LOOMOPS_CPU_THRESHOLD:-80`, `LOOMOPS_DISK_THRESHOLD:-90`,
    `LOOMOPS_LOAD_THRESHOLD:-2.0`, `PLUGINS_STATE_FILE:-`.
  - Host paths are mounted under `/host/...` so container `/etc` and `/var` are
    not shadowed.
  - SELinux: use a `chcon`/`ausearch` fallback, never relabel host `/etc` or
    `/var/log` with `z`.
- `Dockerfile`: add the `systemd` apt package (`journalctl`/`systemctl`);
  `dmesg` already ships in `util-linux`.
- `.env.vds.example`: add the same keys with comments.

## Part 3 — LoomOps tools

`plugins/LoomOps/manifest.json`: `permissions.pid = "host"`,
`cap_add = ["SYSLOG"]`, matching read-only mounts, plus the new env vars.

New modules:

- `LoomOps/ops/monitoring.py` — `host_pid_active()`, `system_metrics()`,
  `dmesg()`, `network_connections()` and helpers (`load_average`, `cpu_percent`).
- `LoomOps/ops/logs.py` — `journal_available()`, `log_search()` (journald when
  available, plain `/var/log` files otherwise), `journal_errors()`.
- `LoomOps/ops/diagnostics.py` — `scan_health()`.
- extend `LoomOps/ops/services.py` with `failed_units()`.

Register everything in `tools.py` / `schemas.py`. New tools (all read-only, so
`safe`): `ops_system_metrics`, `ops_log_search`, `ops_dmesg`, `ops_failed_units`,
`ops_network_connections`, `ops_scan_health`. `ops_list_processes` is host-wide
automatically once `pid: host` is granted.

`ops_scan_health` data shape:

```json
{
  "generated_at": "<UTC ISO-8601>",
  "host_access": {"pid_namespace_host": true, "journal_available": true,
                   "log_root_readable": true, "missing": []},
  "thresholds": {"hours": 24, "cpu_percent": 80, "disk_percent": 90,
                  "load_per_cpu": 2.0, "journal_window_minutes": 60},
  "findings": [{"severity": "critical", "category": "disk",
                 "detail": "...", "evidence": {}}],
  "summary": {"critical": 0, "warning": 0, "info": 0, "total": 0, "status": "healthy"}
}
```

Sources: `systemctl --failed`; OOM (journald `-k`/`dmesg`); a short `/proc/stat`
CPU sample; `shutil.disk_usage` over real mounts from `/proc/mounts`; `/proc/loadavg`
normalized per CPU; `journalctl -p err`. Every source is wrapped: a broken source
adds a `host_access.missing` note instead of returning an empty report. Results use
`text_result(output, **data)` (text summary in `content`, structure in `data`).

## Part 4 — Security, docs, tests

- No new `explicit_dangerous` entries (the new tools are read-only); the existing
  mutating tools stay dangerous and audited.
- Docs: rewrite "Container reality" in `docs/plugins/LoomOps.md` as "Host
  observability" (tools, env, `/host/...` convention, SELinux, the missing-access
  warning, and why `SYS_PTRACE`/`DAC_READ_SEARCH` are omitted); update the
  `AGENTS.md` "no `pid: host`" fact to the approved compromise and mention
  `ops_scan_health`; add a host-observability section to
  `docs/setup/VDS_RUNBOOK.md`.
- Tests: `test_plugin_manifest.py` (pid validation, `host_pid_looks_active`,
  `SYSLOG`, `check_permissions` warning); `test_LoomOps_manifest.py`
  (pid/SYSLOG/mounts/env); `test_LoomOps_tools.py` (monkeypatch `/proc/1/comm`,
  `/proc/loadavg`, `/proc/mounts`, `shutil.which`, `LOOMOPS_LOG_ROOTS` →
  `tmp_path`; graceful degradation; MCP shape); `test_LoomOps_plugin.py`
  `EXPECTED_TOOLS` → 15; `tests/conftest.py` adds `monitoring`, `logs`,
  `diagnostics` to `_LOOMOPS_MODULE_PATHS`.

## Acceptance criteria

1. Manifest supports `pid` + `SYSLOG`; `check_permissions` warns, never errors.
2. `hub` gets `pid: host`, `SYSLOG` and read-only mounts — no `privileged`,
   `docker.sock`, `SYS_PTRACE` or `DAC_READ_SEARCH`.
3. `ops_scan_health` returns the structured report and degrades gracefully.
4. All new tools return MCP shape and classify as `safe`.
5. Missing host access is surfaced at startup (core warning) and in tool results.
6. With `pid: host`, `ops_list_processes` shows host processes.
7. `AGENTS.md` and `docs/plugins/LoomOps.md` document the trust boundary.
8. `ruff check .` clean and `pytest -m "not integration and not e2e"` green.

## Commits

1. `feat(core): support pid namespace and SYSLOG in plugin manifests`
2. `feat(ops): grant host observability to the hub container`
3. `feat(loomops): add host monitoring and health scan tools`
4. `docs: document LoomOps host observability and the trust boundary change`

## Risks

- SELinux may block read-only log/journal mounts (use `chcon`, never relabel host
  `/etc` or `/var/log`).
- `journalctl`/`systemctl` need the `systemd` package and the D-Bus socket mount.
- `dmesg` may be restricted by `kernel.dmesg_restrict`.
- Host-wide `ops_kill_process` / `ops_systemctl_restart` raise the blast radius.
- `/etc:ro` is optional; the `systemd` package grows the image.
