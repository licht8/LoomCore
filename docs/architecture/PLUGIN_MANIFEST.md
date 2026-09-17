# Plugin manifest — LoomCore

Every plugin may ship a `manifest.json` next to its `main.py`:

```
plugins/<PluginId>/
├── manifest.json   # this contract
├── main.py         # MCP entry point (required by discovery)
└── ...
```

The core reads and validates the manifest (identity, version, permissions and
`default_enabled`) during discovery and **warns** on mismatch — a manifest issue
never blocks startup, except that a structurally invalid manifest causes the
plugin to be skipped. The plugin additionally validates it against its own
runtime (see `manifest.py`) and logs warnings so a misconfigured deployment is
visible immediately. This keeps the core thin (RULES 1) while giving every plugin
one place to declare identity, permissions and environment.

`requires_core` and `protocol` are enforced, not decorative: a plugin that
requires a newer core or an unsupported MCP protocol version is **not launched**.
It is still registered as a stopped, disabled plugin carrying the reason, so it
shows up in `core_list_plugins`/`core_plugin_status` and `/api/plugins/status`
instead of disappearing silently. An incompatible plugin never crashes startup.

The persisted enable/disable state lives outside the plugin: by default
`<plugins_dir>/../.loomcore/plugins.json` (`{"disabled": ["<id>", ...]}`),
overridable with the `PLUGINS_STATE_FILE` setting.

## Schema

| Field | Required | Type | Meaning |
|---|---|---|---|
| `schema_version` | no | integer | Manifest schema version (currently `1`); a newer value warns. |
| `id` | yes | string | Plugin id. **Must equal the folder name.** |
| `name` | yes | string | Human-readable name. |
| `version` | yes | string | `MAJOR.MINOR.PATCH`. |
| `description` | yes | string | One line, shown to users. |
| `entry` | yes | string | Entry file launched by the hub (`main.py`). |
| `protocol` | yes | string | MCP protocol version (e.g. `2024-11-05`); the core launches the plugin only if it is in `SUPPORTED_PROTOCOLS`. |
| `tools_prefix` | no | string | Tool name prefix (e.g. `wg_`). |
| `requires_core` | no | string | PEP 440 version range of the core (e.g. `>=0.0.1`); unsatisfied means the plugin is not launched. |
| `platforms` | no | array | OSes the plugin supports (e.g. `["linux"]`). |
| `permissions` | no | object | See below. |
| `env` | no | array | Environment variables the plugin reads. |
| `internal_tools` | no | array | Tool names available only to the core. |
| `default_enabled` | no | boolean | Whether the core launches the plugin on startup (default `true`). |

### `permissions`

```json
{
  "cap_add": ["NET_ADMIN", "NET_RAW"],
  "cap_add_optional": ["SYS_MODULE"],
  "network_mode": "host",
  "pid": "host",
  "mounts": [
    {"source": "/etc/wireguard", "target": "/etc/wireguard", "mode": "rw"},
    {"source": "/lib/modules", "target": "/lib/modules", "mode": "ro"}
  ]
}
```

The plugin checks these against its runtime at startup:

- `cap_add` — required effective Linux capabilities (`/proc/self/status`
  `CapEff`); a missing one is a warning. Known names: `NET_ADMIN` (12),
  `NET_RAW` (13), `SYS_MODULE` (16), `SYSLOG` (34, needed to read the kernel
  ring buffer with `dmesg`). `SYS_PTRACE` and `DAC_READ_SEARCH` are intentionally
  **not** recognised — a plugin must not request them.
- `cap_add_optional` — capabilities the plugin can run without; a missing one is
  only an informational note. (LoomWG loads the WireGuard module on demand, so it
  works without `SYS_MODULE` when the module is built-in or already loaded.)
- `mounts` — target path exists and is writable (`rw`) / readable (`ro`).
- `network_mode` — best-effort host-network heuristic (a 12-hex hostname means
  a bridge network).
- `pid` — `"host"` or `"container"`; `"host"` warns when `/proc/1` is not
  `systemd`/`init` (i.e. the host PID namespace is not actually mounted).

Mismatches are logged and returned by diagnostics; they never block startup.

### `env`

```json
{"name": "LOOMWG_ENDPOINT", "required": false, "default": "/etc/wireguard", "description": "..."}
```

### Tool declarations

Each entry in the plugin's `TOOLS_SCHEMA` (returned by `tools/list`) may carry two
core-facing keys alongside `name`/`description`/`inputSchema`:

- `risk` — `"safe"`, `"moderate"` or `"dangerous"`. The core uses it verbatim to
  decide whether a step needs approval; a declared risk **overrides** the core's
  name heuristic. A plugin declaring a tool `safe` bypasses approval by design —
  plugins are trusted local subprocesses, only the LLM is untrusted, and the
  declaration is visible in plan steps and audit records. Omitting `risk` falls
  back to the heuristic. The bundled `LoomWG` and `LoomOps` plugins are stricter:
  they fail to import unless every advertised tool has an explicit entry (and no
  entry is stale), so a new tool can never be advertised as safe by accident.
- `internal` — `true` marks a tool the LLM must never see; it is callable only by
  the core. `internal_tools` in the manifest is the declarative counterpart.

## Adding a plugin

1. Copy the template: `docs/architecture/plugin-template/` → `plugins/<PluginId>/`.
2. Edit `manifest.json`: set `id` to the folder name, plus name/version/description.
3. Implement tools in `main.py` (`TOOLS_SCHEMA` + `HANDLERS`).
4. Declare the capabilities, mounts and env you actually use in `permissions`/`env`.
5. Keep `manifest.py` and call its self-check at startup.
6. Add tests under `tests/` and update `docs/plugins/<PluginId>.md`.
7. Declare `risk` (`safe`/`moderate`/`dangerous`) on every tool in
   `TOOLS_SCHEMA` and mark core-only tools `internal: true`. The core reads these
   from the schema — it does **not** need to be edited to add a plugin tool.

The hub discovers any `plugins/<dir>/` that contains `main.py`; the template
lives outside `plugins/` so it is never launched by accident.
