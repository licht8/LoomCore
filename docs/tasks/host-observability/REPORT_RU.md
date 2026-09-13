# Report — host observability for LoomOps

Task spec: `docs/tasks/host-observability/TZ_RU.md`.

## Goal

Give LoomOps read-only visibility of the whole host so the assistant can reason
about problems/anomalies (`ops_scan_health`), without weakening the deny-by-default
and approval-gated model for mutations. The trust boundary was expanded
deliberately and documented.

## Delivered

### Part 1 — core manifest support
- `src/plugins/manifest.py`: `CAPABILITIES` gained `SYSLOG` (34); new
  `host_pid_looks_active()`; `_validate_permissions` validates `permissions.pid`
  (`"host"`/`"container"`); `check_permissions` warns when `pid: host` is declared
  but `/proc` does not look like the host PID namespace.
- `docs/architecture/PLUGIN_MANIFEST.md` documents `pid` + `SYSLOG` and the
  intentionally unsupported `SYS_PTRACE`/`DAC_READ_SEARCH`.

### Part 2 — host grants
- `docker-compose.yml` `hub`: `pid: host`, `cap_add: [SYSLOG]`, D-Bus socket, and
  read-only mounts of `/var/log`, `/var/log/journal`, `/run/systemd`, `/etc` under
  `/host/...` (plus the existing `/etc/wireguard` and `/lib/modules`). All LoomOps
  env vars are passed through from `.env` (`${VAR:-...}`).
- `Dockerfile`: `systemd` package (provides `journalctl`/`systemctl`).
- `.env.vds.example`: documented the allowlists, audit log, timeout, log roots,
  log-line cap and scan thresholds.
- No `privileged`, no Docker socket, no `SYS_PTRACE`/`DAC_READ_SEARCH`.

### Part 3 — LoomOps tools (manifest v0.2.0, 15 tools)
- New `ops_system_metrics`, `ops_log_search` (journald or file fallback),
  `ops_dmesg`, `ops_failed_units`, `ops_network_connections`, `ops_scan_health`
  (structured report: failed units, OOM, disk, load, CPU, journal errors;
  `host_access.missing` describes absent grants).
- `ops_list_processes` is now host-wide with `pid: host`.
- New modules `ops/monitoring.py`, `ops/logs.py`, `ops/diagnostics.py`; extended
  `ops/services.py`. All new tools are read-only and classified `safe`; the four
  mutating tools remain approval-gated and audited.

### Part 4 — docs
- `docs/plugins/LoomOps.md`: new "Host observability" section (grants, `/host/...`
  convention, missing-access behaviour, SELinux guidance, why
  `SYS_PTRACE`/`DAC_READ_SEARCH` are omitted); 15-tool risk table.
- `AGENTS.md`: updated the trust-boundary fact.
- `docs/setup/VDS_RUNBOOK.md`: host-observability subsection + env.

## Verification

- `python -m ruff check .` — clean.
- `python -m pytest -m "not integration and not e2e" -q` — 368 passed, 2 deselected.
- `npx tsc --noEmit` — clean; `npm run build` — compiled, 3/3 static pages.

## Commits

1. `feat(core): support pid namespace and SYSLOG in plugin manifests`
2. `feat(ops): grant host observability to the hub container`
3. `feat(loomops): add host monitoring and health scan tools`
4. `docs: document LoomOps host observability and the trust boundary change`

## Notes / follow-ups

- On SELinux hosts the read-only mounts may need `chcon`; never relabel host
  `/etc` or `/var/log` with `:z`.
- `dmesg` may be blocked by `kernel.dmesg_restrict`; `ops_dmesg` reports this.
- Host-wide `ops_kill_process`/`ops_systemctl_restart` increase blast radius; they
  stay admin-approved and audited.
