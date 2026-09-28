# TZ_RU.md — tools cache for /api/tools and WS session-id validation

> Copy to `docs/tasks/<kebab-name>/TZ_RU.md` and fill in **before** starting
> (RULES 16). Content is English (RULES 0). Keep it specific and testable.

## 1. Goal

Fix two audit findings on the read/handshake path:

- **`GET /api/tools` forces a live JSON-RPC fan-out to every plugin** even though
  the catalog is already indexed at startup. `src/api/plugins.py:44-51` calls
  `plugin_manager.get_all_tools()` (`src/plugins/manager.py:929-975`), which
  calls `get_tools` on each enabled plugin with a 30s timeout and can even run
  restart backoff inside the request. The cache (`get_tools_for_routing`,
  `src/plugins/manager.py:1005-1009`) is already primed by the lifespan
  (`src/app.py:85-89`) and refreshed on enable/disable/reload/restart.
- **`GET /ws/{session_id}` accepts any string**, while the REST routes validate
  the same id with a length + pattern constraint
  (`src/api/sessions.py:17-19`: `^[A-Za-z0-9_-]{1,64}$`). `src/api/websocket.py:89-90`
  takes a bare `str` and `src/interfaces/websocket.py` calls `ensure_session`
  with it; an id over 64 chars overflows `sessions.id String(64)`
  (`src/db/models.py:40-42`) and can raise an unhandled `DataError` at
  accept-time.

## 2. Non-goals

- No change to the WS message protocol, event vocabulary or frontend.
- `get_all_tools()` keeps its current behavior (startup priming and internal
  tool lookup still use it).
- `/api/overview` per-tool timeouts / async status refresh are a separate future
  task.

## 3. Approved decisions (locked)

### D1 — serve `/api/tools` from the indexed cache

- `src/api/plugins.py` `list_plugins_tools`: replace
  `await plugin_manager.get_all_tools()` with
  `await plugin_manager.get_tools_for_routing()`. Keep
  `list(CORE_TOOLS_SCHEMA) + ...` and the existing `try/except` -> HTTP 500.
- `get_tools_for_routing()` returns cached tools when `_tools_indexed` is True
  and falls back to a live fetch otherwise, so a cold start still populates.

### D2 — validate the WS session id before accept

- `src/api/websocket.py`: add `import re` and
  `_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")` (mirrors
  `src/api/sessions.py` `_SESSION_ID`).
- In `ws_proxy`, after the TLS check and the credential check and before
  `await websocket_endpoint(websocket, session_id)`: if
  `_SESSION_ID_RE.fullmatch(session_id) is None`, log a security event
  (`log_security_event("ws_auth_failure", reason="invalid_session_id",
  client=client_ip(websocket), session_id=session_id)`), close with
  `code=1008, reason="Invalid session id"`, and return without calling the
  endpoint (no `accept`).
- Order rationale: keep the existing auth-failure close reasons for
  unauthenticated requests; validate the id only after auth succeeds.

## 4. Data model / interfaces

- No DB, model, WS-protocol or frontend change.
- `/api/tools` response shape is unchanged (`{"tools": [...]}`), now sourced
  from the cache.
- WS close code 1008 gains one new reason string: `"Invalid session id"`.

## 5. Parts

1. `src/api/plugins.py` — switch `list_plugins_tools` to
   `get_tools_for_routing()`.
2. `src/api/websocket.py` — `import re`, `_SESSION_ID_RE`, and the guard block in
   `ws_proxy`.

## 6. Tests

- `tests/test_api_plugins.py`: update the two tests that monkeypatch
  `plugins_api.plugin_manager.get_all_tools` (lines ~52 and ~70) to patch
  `get_tools_for_routing` instead; keep the success case and the 500 case.
- `tests/test_ws_proxy.py`: add `test_proxy_rejects_invalid_session_id` (e.g.
  `"bad id!"` and a 65-character id) asserting `captured == []` and
  `websocket.closed == (1008, "Invalid session id")`; existing valid-id tests
  must keep passing unchanged.
- Gates: `uv run ruff check .`; `uv run pytest -m "not integration and not e2e" -q`.

## 7. Acceptance criteria

- With the cache primed, `GET /api/tools` performs no plugin JSON-RPC call.
  Verified by monkeypatching `get_all_tools` to raise while
  `get_tools_for_routing` returns cached tools, then asserting the route still
  returns tools.
- `GET /ws/{session_id}` with a malformed or >64-char id is closed with
  1008 / `"Invalid session id"` before `accept()`; a valid id is unaffected.
- `uv run ruff check .` clean; `uv run pytest -m "not integration and not e2e" -q`
  green.

## 8. Commits

1. `perf(api): serve /api/tools from the indexed plugin cache`
2. `fix(api): validate the WebSocket session id before accept`

## 9. Risks

- Cache contents reflect the last successful fetch. A plugin that publishes new
  tools without a refetch would be stale, but enable/disable/reload/restart all
  call `_rebuild_tool_cache`, so the cache tracks lifecycle changes.
- If startup priming failed while `_tools_indexed` stayed False,
  `get_tools_for_routing()` performs a live fetch on the first call — same as
  today, with the same timeout exposure, but only once.
- The WS regex rejects ids outside `[A-Za-z0-9_-]{1,64}`. The frontend generates
  `sess_<32 hex>`, so it is unaffected; any external client using another id
  shape must be updated.
