# LoomWG plugin

WireGuard management plugin (`plugins/loomwg`). It runs as an MCP subprocess and
talks JSON-RPC over stdio. It manages the host's WireGuard via `wg`/`wg-quick`
(the `hub` container uses `network_mode: host`).

- Server configs: `LOOMWG_CONFIG_DIR` (default `/etc/wireguard`)
- Client configs / QR: `LOOMWG_CONFIG_DIR/clients`
- Autostart state: `LOOMWG_CONFIG_DIR/.loomwg/autostart.json`
- Private keys are never returned in chat; configs/QR are served as one-time
  artifact links (`/api/artifacts/<token>/{config,qr}`).

## Tools

| Tool | Risk | Description |
|---|---|---|
| `wg_list_interfaces` | safe | Overview of all interfaces: active/config present. |
| `wg_setup_server` | dangerous | Create the server config, enable forwarding, firewall/NAT. |
| `wg_show_config` | safe | Show the live (or saved) config; keys masked unless `show_secrets`. |
| `wg_check_config` | safe | Validate a config without starting it (keys, CIDRs, duplicates, overlaps, `wg-quick strip`). |
| `wg_status` | safe | Interface status and peer online counts. |
| `wg_diagnostics` | safe | In-depth check: wg installed, active, link, config present. |
| `wg_stats` | safe | Snapshot totals, per-peer share, top peers (no history). |
| `wg_add_peer` | moderate | Add or update a peer (idempotent on identical allowed IPs). |
| `wg_remove_peer` | dangerous | Remove a peer and its client files. |
| `wg_list_peers` | safe | Peers with handshake and traffic. |
| `wg_start` / `wg_stop` / `wg_restart` | dangerous | Interface lifecycle. |
| `wg_service_enable` / `wg_service_disable` | dangerous | App-managed autostart flag. |
| `wg_create_client` | dangerous | Generate keys, add peer, save config + QR (errors if the name exists). |
| `wg_export_client` | dangerous | Re-issue config/QR links; `rotate=true` recreates if the config is lost. |
| `wg_get_client_artifact` | internal | One-time artifact download (not exposed to the LLM). |

Risk is enforced by the core (`src/core/security.py:classify_tool_risk`);
`dangerous` tools require explicit admin approval.

## Parameters (new tools)

- `wg_show_config`: `interface` (default `wg0`), `show_secrets` (default `false`).
- `wg_check_config`: `interface` (default `wg0`) **or** `path` **or** `content`.
- `wg_stats`: `interface` (default `wg0`), `top` (default `5`).
- `wg_export_client`: `name` (required), `interface`, `kind` (`config`/`qr`/`both`), `rotate`.
- `wg_service_enable` / `wg_service_disable`: `interface` (default `wg0`).

## Autostart semantics

The plugin runs in a container and cannot drive host systemd. `wg_service_enable`
writes the interface to `autostart.json` and brings it up immediately;
`wg_service_disable` clears the flag (it does not stop a running interface —
use `wg_stop`). On plugin startup `apply_autostart()` (in `server.py`) brings up
every flagged interface that has a config and is not already active.

## Client files

`wg_create_client` and `wg_export_client` use these names under
`<config-dir>/clients`: `<name>.conf` (mode `600`) and `<name>.png` (mode `600`).
`wg_export_client` regenerates a missing QR from the saved config; if the config
file is gone, the private key is unrecoverable and the call errors unless
`rotate=true`, which recreates the client with a new keypair.
