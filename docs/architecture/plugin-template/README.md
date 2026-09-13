# Plugin template

A minimal, runnable LoomCore MCP plugin skeleton.

## Use it

```bash
cp -r docs/architecture/plugin-template plugins/MyPlugin
# then edit plugins/MyPlugin/manifest.json and main.py
```

Keep the folder name and `manifest.json` `id` identical. The hub discovers any
`plugins/<dir>/` that contains `main.py`; this template stays outside `plugins/`
so it is not launched by accident.

## Files

- `manifest.json` — plugin metadata + declared permissions/env (see
  `docs/architecture/PLUGIN_MANIFEST.md`).
- `main.py` — MCP entry point: `initialize`, `tools/list`, `tools/call`.
- `manifest.py` — loader/validator copied from LoomWG; call `runtime_report()`
  at startup and log the warnings.

## Notes

- JSON-RPC 2.0 over stdin/stdout, one request per line, one response line.
- Keep tools read-only unless they must change state; dangerous tools must be
  listed in `explicit_dangerous` in `src/core/security.py` so the core asks for
  approval.
- Add tests under `tests/` and a spec in `docs/plugins/`.
