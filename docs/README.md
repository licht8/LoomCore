# Documentation map

Everything lives under `docs/`, by kind of activity (RULES 15). The project root
is closed; do not add files there.

| Path | What goes here |
|---|---|
| `docs/architecture/` | MCP protocol, plugin manifest, agent loop, plugin template |
| `docs/plugins/` | One page per plugin: `LoomWG.md`, `LoomOps.md`, ... |
| `docs/setup/` | Deployment (`DEPLOY.md`), VDS runbook (`VDS_RUNBOOK.md`), testing (`TESTING.md`) |
| `docs/tasks/<name>/` | A task spec and its report, paired: `TZ_RU.md` + `REPORT_RU.md` |
| `docs/tasks/_template/` | Blank `TZ_RU.md` / `REPORT_RU.md` to copy for a new task |

## How to use it

- New task → copy `docs/tasks/_template/TZ_RU.md` to
  `docs/tasks/<kebab-name>/TZ_RU.md` and fill it in before starting.
- Finished task → `REPORT_RU.md` in the same folder (RULES 16).
- New plugin → `docs/plugins/<PluginId>.md`, and register it in this map.
- Drafts, scratch notes, logs → `tmp/` (gitignored), never in `docs/`.
- Behaviour changed → update the affected page in the same commit.
- Unsure where something belongs → ask the owner; do not invent a new folder.

## Index

- [Plugin manifest](architecture/PLUGIN_MANIFEST.md)
- [Agent loop](architecture/AGENT.md)
- [LoomWG plugin](plugins/LoomWG.md)
- [LoomOps plugin](plugins/LoomOps.md)
- [Deploy](setup/DEPLOY.md) · [VDS runbook](setup/VDS_RUNBOOK.md) · [Testing](setup/TESTING.md)
