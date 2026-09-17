# LoomWG plugin

WireGuard management plugin (`plugins/LoomWG`). It runs as an MCP subprocess and
talks JSON-RPC over stdio. It manages the host's WireGuard via `wg`/`wg-quick`
(the `hub` container uses `network_mode: host`).

- Server configs: `LOOMWG_CONFIG_DIR` (default `/etc/wireguard`)
- Client configs: `LOOMWG_CONFIG_DIR/clients/conf/<name>.conf`
- Client QR codes: `LOOMWG_CONFIG_DIR/clients/qr_code/<name>.png` (mode `600`,
  set before `qrencode` writes the embedded private key and re-applied after it;
  a failed run removes the file so the next request regenerates it)
- Client/user store: `LOOMWG_CONFIG_DIR/.loomwg/clients.json`
- Autostart state: `LOOMWG_CONFIG_DIR/.loomwg/autostart.json`
- Private keys are never returned in chat; configs/QR are served as durable,
  admin-only links (`/api/artifacts/<name>/{config,qr}`) and rebuilt from the
  stored key when missing.

## Manifest & self-check

`plugins/LoomWG/manifest.json` declares the plugin id/version, tool prefix, the
capabilities/mounts/network mode it needs, and its environment variables (schema:
`docs/architecture/PLUGIN_MANIFEST.md`). At startup `LoomWG/manifest.py` validates
it against the folder name and the actual container runtime (effective caps,
mounted paths, host networking) and logs a warning on every mismatch — startup is
never blocked. `wg_diagnostics` reports the manifest version and the same
warnings.

## Layout

`plugins/LoomWG/main.py` is a thin launcher (`from LoomWG.server import run_server`).
The implementation lives in the `LoomWG` package:

- `server.py`, `protocol.py`, `results.py` — MCP wiring, JSON-RPC stdio, result helper.
- `tools.py` — tool handlers and `TOOLS_SCHEMA`.
- `wg/` — `device.py` (wg/wg-quick commands + parsing), `keys.py`, `validate.py`.
- `peers/` — `config.py` (server peer blocks), `view.py` (config display).
- `clients/` — `store.py` (JSON store), `client.py` (provisioning + artifacts).
- `ops/` — `install.py`, `lifecycle.py`, `service.py` (autostart), `stats.py`.

## Client store (`clients.json`)

Plugin-owned JSON, keyed by **name**, mode `600`, atomic writes:

```json
{
  "phone1": {
    "public_key": "...", "private_key": "...", "preshared_key": "...",
    "addresses": ["10.66.66.4/32", "fd42:42:42::4/128"],
    "endpoint": "91.90.160.72:51820",
    "created_at": "2026-09-12T10:00:00Z"
  }
}
```

- `private_key`/`preshared_key`/`endpoint` are present only for clients created
  by the plugin; external/backfilled peers have only `public_key` + `addresses`.
- The store holds secrets (same as the `.conf`); keep it mode `600` and never
  expose it through a tool.
- The WireGuard configs remain the source of truth for live state; `wg_list_peers`
  reconciles the store against live peers and **reports** untracked/stale entries
  without changing anything.

## Prerequisites & installation

The plugin runs in the `hub` container, which already ships the userspace tools
(`wireguard-tools`, `iproute2`, `qrencode`, `iptables`, `nftables`). The
`wg_install_dependencies` tool (dangerous — admin approval) re-checks them and:

1. installs anything missing **inside the container** with its own package
   manager (`apt`/`dnf`/`yum`/`apk`);
2. loads the WireGuard kernel module via `modprobe` and verifies support with a
   throwaway `ip link add ... type wireguard` probe.

The plugin cannot install packages or kernel modules on the **host** by design
(no privileged/host escape). WireGuard is in-tree on Linux 5.6+; on older kernels
the tool returns the host-side commands to run (e.g.
`dnf install -y kmod-wireguard`) and no changes are made. `deploy/install.sh`
does not install WireGuard at all.

## Tools

| Tool | Risk | Description |
|---|---|---|
| `wg_list_interfaces` | safe | Overview of all interfaces: active/config present. |
| `wg_setup_server` | dangerous | Create the server config, enable forwarding, firewall/NAT. |
| `wg_show_interface_config` | safe | Show the live config, or the saved `.conf` when the interface is down; the header states `source=live` or `source=file`. Private/preshared keys are always masked. |
| `wg_check_interface_config` | safe | Validate a config without starting it (keys, CIDRs, duplicates, overlaps). |
| `wg_interface_status` | safe | Interface status and peer online counts. |
| `wg_diagnostics` | safe | In-depth check: wg installed, prerequisites, active, link, config present. |
| `wg_install_dependencies` | dangerous | Install missing container tools + load the kernel module; returns host commands if the kernel lacks WireGuard. |
| `wg_stats` | safe | Snapshot totals, per-peer share, top peers (no history). |
| `wg_create_peer` | moderate | Create/track a client (generates keys when no `public_key`); idempotent. |
| `wg_remove_peer` | dangerous | Remove by `name` (store) or `public_key`; deletes peer, files, record. |
| `wg_list_peers` | safe | Clients from the store merged with live peers (unmanaged/stale reported). |
| `wg_interface_start` / `wg_interface_stop` / `wg_interface_restart` | dangerous | Interface lifecycle. |
| `wg_service_enable` / `wg_service_disable` | dangerous | App-managed autostart flag. |
| `wg_export_existing_clients` | dangerous | Import untracked peers and (re)issue config + QR links for all clients. |
| `wg_get_client_artifact` | internal | Artifact download by client name (not exposed to the LLM). |

Risk is enforced by the core (`src/core/security.py:classify_tool_risk`);
`dangerous` tools require explicit admin approval. `wg_create_client` no longer
exists — use `wg_create_peer` + `wg_export_existing_clients`. The plugin refuses
to start if any advertised tool lacks an explicit risk entry, so no tool can be
advertised as safe by accident.

## Parameters

- `wg_create_peer`: `name` (required), `interface`, `public_key` (optional → keys are
  generated), `addresses` (optional → auto-allocated), `preshared_key`, `allowed_ips`.
- `wg_remove_peer`: `name` or `public_key`.
- `wg_list_peers`: `interface`.
- `wg_export_existing_clients`: `interface`, `rotate` (default `true`),
  `kind` (`config`/`qr`/`both`), `names` (optional subset).
- `wg_show_interface_config`: `interface`.
- `wg_check_interface_config`: `interface` or `path` or `content`.
- `wg_stats`: `interface`, `top`.
- `wg_service_enable` / `wg_service_disable`: `interface`.

## Flows

- **New client:** `wg_create_peer {name}` → generates keys, adds the peer, and writes
  `clients/conf/<name>.conf` + `clients/qr_code/<name>.png`, returning links.
  `wg_export_existing_clients` re-issues links later.
- **Lost config/QR:** `wg_export_existing_clients` rebuilds files from the stored
  private key. Peers without a stored key (external/backfilled) are rekeyed when
  `rotate=true` (the default); with `rotate=false` they are reported as failed.
  Re-running is safe: clients with stored keys are only reissued.
- **First run:** existing peers in `wg0.conf` are imported by
  `wg_export_existing_clients`, which names them `peer-<hex>` and, since no
  private key is known, recreates each with new keys. Names are resolved leniently
  (exact key, a doubled `peer-` prefix, canonical hex, or a unique public-key
  prefix); legacy `peer-<base64>` records are renamed to canonical hex on the next
  run.

## Autostart semantics

The plugin runs in a container and cannot drive host systemd. `wg_service_enable`
writes the interface to `autostart.json` and brings it up immediately;
`wg_service_disable` clears the flag (does not stop a running interface). On
plugin startup `apply_autostart()` brings up every flagged interface that has a
config and is not already active.
