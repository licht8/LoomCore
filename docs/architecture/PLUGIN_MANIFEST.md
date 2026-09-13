# Plugin manifest — LoomCore

Every plugin may ship a `manifest.json` next to its `main.py`:

```
plugins/<PluginId>/
├── manifest.json   # this contract
├── main.py         # MCP entry point (required by discovery)
└── ...
```

The manifest is **plugin-local metadata**. The core does not read or enforce it;
the plugin validates it against its own runtime (see `manifest.py`) and logs
warnings so a misconfigured deployment is visible immediately. This keeps the
core thin (RULES 1) while giving every plugin one place to declare identity,
permissions and environment.

## Schema

| Field | Required | Type | Meaning |
|---|---|---|---|
| `schema_version` | no | integer | Manifest schema version (currently `1`); a newer value warns. |
| `id` | yes | string | Plugin id. **Must equal the folder name.** |
| `name` | yes | string | Human-readable name. |
| `version` | yes | string | `MAJOR.MINOR.PATCH`. |
| `description` | yes | string | One line, shown to users. |
| `entry` | yes | string | Entry file launched by the hub (`main.py`). |
| `protocol` | yes | string | MCP protocol version (e.g. `2024-11-05`). |
| `tools_prefix` | no | string | Tool name prefix (e.g. `wg_`). |
| `requires_core` | no | string | Semver range of the core. |
| `platforms` | no | array | OSes the plugin supports (e.g. `["linux"]`). |
| `permissions` | no | object | See below. |
| `env` | no | array | Environment variables the plugin reads. |
| `internal_tools` | no | array | Tool names available only to the core. |

### `permissions`

```json
{
  "cap_add": ["NET_ADMIN", "NET_RAW"],
  "cap_add_optional": ["SYS_MODULE"],
  "network_mode": "host",
  "mounts": [
    {"source": "/etc/wireguard", "target": "/etc/wireguard", "mode": "rw"},
    {"source": "/lib/modules", "target": "/lib/modules", "mode": "ro"}
  ]
}
```

The plugin checks these against its runtime at startup:

- `cap_add` — required effective Linux capabilities (`/proc/self/status`
  `CapEff`); a missing one is a warning.
- `cap_add_optional` — capabilities the plugin can run without; a missing one is
  only an informational note. (LoomWG loads the WireGuard module on demand, so it
  works without `SYS_MODULE` when the module is built-in or already loaded.)
- `mounts` — target path exists and is writable (`rw`) / readable (`ro`).
- `network_mode` — best-effort host-network heuristic (a 12-hex hostname means
  a bridge network).

Mismatches are logged and returned by diagnostics; they never block startup.

### `env`

```json
{"name": "LOOMWG_ENDPOINT", "required": false, "default": "/etc/wireguard", "description": "..."}
```

## Adding a plugin

1. Copy the template: `docs/architecture/plugin-template/` → `plugins/<PluginId>/`.
2. Edit `manifest.json`: set `id` to the folder name, plus name/version/description.
3. Implement tools in `main.py` (`TOOLS_SCHEMA` + `HANDLERS`).
4. Declare the capabilities, mounts and env you actually use in `permissions`/`env`.
5. Keep `manifest.py` and call its self-check at startup.
6. Add tests under `tests/` and update `docs/plugins/<PluginId>.md`.
7. Dangerous tools must be added to `explicit_dangerous` in
   `src/core/security.py` so the core asks for approval.

The hub discovers any `plugins/<dir>/` that contains `main.py`; the template
lives outside `plugins/` so it is never launched by accident.
