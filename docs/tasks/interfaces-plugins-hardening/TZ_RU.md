# TZ_RU.md — interfaces/plugins hardening

Task id: `interfaces-plugins-hardening`. Follow RULES.md (English only, no comments
unless existing style has them, ruff clean, tests mandatory).

## 1. Goal

Fix four review findings:

1. `src/interfaces/events.py` — `exclude_none=True` conflates "unset" with
   "explicitly `None`", and `ToolResponseEvent.result` (`Any = None`) makes a
   legitimate `null` result indistinguishable on the wire from an absent one.
2. `src/interfaces/tool_events.py` — `extract_text` assumes `raw_result["content"]`
   is a list of dicts; a non-list `content`, a non-dict element, or a `None`
   argument breaks or corrupts the tool card/history.
3. `src/plugins/health.py` — the loop logs "restarted successfully" even when
   `PluginManager.restart_plugin()` returned `False`.
4. `src/plugins/__init__.py` — the manager singleton is built at import time,
   performing filesystem I/O and freezing `settings` paths.

## 2. Non-goals

- No change to the event `type` set, `WS_SCHEMA_VERSION`, or the frontend.
- No switch to `exclude_unset` (see D1).
- No new `PluginManager` public API beyond the lazy factory.
- No change to plugin subprocess handling, risk, or approval logic.

## 3. Approved decisions

### D1 — drop the ambiguous `result` field
`content` already carries the tool value (`tool_success_payload` sets
`result=content`, `tool_error_payload` sets `result=message`), the frontend
declares no `result`, and no test asserts it. So:

- Remove `result: Any = None` from `ToolResponseEvent` (`events.py:63`).
- Remove `result=content` from `tool_success_payload` and `result=message` from
  `tool_error_payload` (`tool_events.py`).
- Correct the `event_payload` docstring: `exclude_none` omits **every** field
  whose value is `None` (whether the caller passed it or the model defaulted
  it); the registry has no field where an explicit `null` is meaningful, so this
  is safe. Remove `result` from the module docstring's extra-fields list.
- **Keep `exclude_none=True`.** `exclude_unset=True` is wrong: `type` and
  `schema_version` are class defaults that are never passed, so they would be
  omitted and `payload["type"]` would disappear.

### D2 — guard `extract_text`
Annotate `raw_result: object` and implement:

```python
def extract_text(raw_result: object) -> str:
    if isinstance(raw_result, dict) and "content" in raw_result:
        content = raw_result["content"]
        if isinstance(content, list):
            return "".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        return str(content)
    return "" if raw_result is None else str(raw_result)
```

### D3 — report restart failures
In `src/plugins/health.py`:

```python
if await plugin_manager.restart_plugin(name):
    logger.info("Plugin '%s' restarted successfully", name)
else:
    logger.warning("Plugin '%s' could not be restarted", name)
```

### D4 — lazy plugin manager (PEP 562)
`src/plugins/__init__.py` must not construct `PluginManager` at import time.
Keep the `plugin_manager` name resolving for existing call sites and tests:

```python
"""Shared plugin manager accessor (lazy)."""
from functools import lru_cache

from src.config import settings
from src.plugins.manager import PluginManager
from src.plugins.state import resolve_state_path


@lru_cache(maxsize=1)
def get_plugin_manager() -> PluginManager:
    return PluginManager(
        state_path=resolve_state_path(settings.plugins_dir, settings.plugins_state_file)
    )


def __getattr__(name: str):
    if name == "plugin_manager":
        return get_plugin_manager()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

Notes: `resolve_state_path` already returns `Path`, so drop the redundant
`Path(...)`; the `from pathlib import Path` import goes away.

## 4. Contract / behaviour impact

- Wire: `tool_response` payloads no longer carry `result`; otherwise unchanged.
- `extract_text(None)` -> `""` (was `"None"`); non-list `content` -> `str(content)`;
  malformed elements ignored instead of raising.
- `import src.plugins` and `from src.plugins import manifest` perform no I/O.
- `src.plugins.plugin_manager` still binds via `__getattr__`, so
  `from src.plugins import plugin_manager` and test monkeypatches keep working.

## 5. Parts

1. `src/interfaces/events.py` — D1.
2. `src/interfaces/tool_events.py` — D1 (two builders) + D2.
3. `src/plugins/health.py` — D3.
4. `src/plugins/__init__.py` — D4.

## 6. Tests

- `tests/test_events.py`: assert `"result" not in tool_success_payload(...)`; add a
  test that an explicit `None` is omitted:
  `event_payload(ResponseEvent(content="x", text=None))` has no `"text"` key.
- New `tests/test_tool_events.py` for `extract_text`:
  - list of text dicts -> joined text;
  - non-list `str` content -> `str(content)`;
  - non-list `dict` content -> `str(content)`;
  - list containing a non-dict element -> ignored, no raise;
  - `None` -> `""`;
  - non-dict raw -> `str(raw)`.
- `tests/test_plugins_health.py`: make the `_FakeManager.restart_plugin` return
  `True`; add `test_healthcheck_loop_logs_warning_when_restart_fails` (returns
  `False`; assert caplog WARNING contains "could not be restarted" and
  "restarted successfully" is absent).
- New `tests/test_plugins_init.py`:
  - `get_plugin_manager()` returns a `PluginManager` and is cached (same object);
  - `src.plugins.plugin_manager is get_plugin_manager()`;
  - after `get_plugin_manager.cache_clear()` with monkeypatched
    `settings.plugins_dir` / `settings.plugins_state_file`, the manager's
    `_state_path` equals `resolve_state_path(...)` for those settings;
  - unknown attribute raises `AttributeError`.

## 7. Acceptance criteria

- `tool_response` payloads have no `result`; `exclude_none` behaviour documented
  accurately.
- `extract_text` never raises for non-dict/list content and returns `""` for
  `None`.
- Health loop logs a warning, not success, when a restart fails.
- `import src.plugins` does no filesystem I/O; settings paths resolved lazily;
  all production call sites and test monkeypatches unchanged.

## 8. Commits (coordinator commits after acceptance — RULES 0a)

1. `fix(interfaces): drop the ambiguous result field and guard tool-result text extraction`
2. `fix(plugins): report restart failures and build the manager lazily`

## 9. Risks / notes

- Dropping `result` is a wire change, but no frontend or test consumer reads it.
- `__getattr__` runs on every miss; acceptable (only for the one managed name).
- `lru_cache` keeps a strong reference to the manager for the process lifetime,
  which matches the previous singleton semantics; tests must `cache_clear()`
  when they need a fresh settings-derived manager.
