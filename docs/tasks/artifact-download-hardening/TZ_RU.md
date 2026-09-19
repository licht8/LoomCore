# TZ_RU.md — artifact download hardening (header injection, info leak, media_type)

## 1. Goal

`GET /api/artifacts/{name}/{kind}` in `src/api/artifacts.py` has these defects:

1. **Header injection / crash (filename).** The plugin-supplied `filename` is
   interpolated verbatim into `Content-Disposition`:
   `f'attachment; filename="{data["filename"]}"'`. The value originates from a
   plugin and is **not** a trusted constant at this layer. A `"` breaks out of
   the quoted filename; a CR/LF can inject extra headers; depending on the ASGI
   server it may instead raise and surface as an unhandled `500`.
2. **Unvalidated header (media_type).** `media_type=data["media_type"]` is
   plugin-supplied and written straight into `Content-Type` — the same
   plugin-controlled-header vector as (1). It must be allow-listed before use.
3. **Information leak.** The failure body returns `"reason": str(exc)`, leaking
   plugin internals, client names and filesystem paths. The warning log already
   records the detail for operators.

> **Withdrawn from the previous draft: `except (KeyError, ValueError, FileNotFoundError)`.**
> Narrowing the tuple is a **contract regression** and must NOT be done. Plugins
> are invoked over JSON-RPC: `plugins/LoomWG/LoomWG/protocol.py:73`
> (`dispatch`) catches the handler's `(OSError, RuntimeError, ValueError)` and
> returns them as a JSON-RPC `error`; `src/plugins/manager.py:995-996`
> (`execute_tool`) re-raises any error response as `RuntimeError`. Therefore the
> ordinary "Client not found" / "artifact not found" paths (raised as
> `ValueError`/`FileNotFoundError` inside LoomWG's `get_artifact`) reach this
> handler as `RuntimeError`, and plugin `OSError`/`TimeoutError` likewise. The
> caught set must stay `(KeyError, ValueError, OSError, RuntimeError)` so those
> keep returning `404`.

## 2. Non-goals

- Do NOT narrow the caught exception set (see §1 withdrawal). Only the `reason`
  payload changes.
- No change to the `kind` allow-list check or its separate `{"error": "Artifact
  not found"}` 404.
- No change to authentication (`require_admin_token_from_request`).
- No change to the LoomWG plugin (`plugins/LoomWG/**`) — the core must not rely
  on the plugin sanitising its own values.
- No change to `src/interfaces/tool_events.py` link building.
- No RFC 6266 `filename*=UTF-8''…` support; a safe ASCII subset is enough.
- No dependency changes.

## 3. Approved decisions (locked)

- Add two private helpers in `src/api/artifacts.py` (same module — RULES 10) and
  apply them before values reach response headers.
- **Keep** the exception tuple as `(KeyError, ValueError, OSError, RuntimeError)`;
  `TimeoutError` is covered because it subclasses `OSError` (requires-python
  `>=3.11`). Do not remove `RuntimeError`/`OSError`.
- Drop `reason` from the response body; keep the existing `logger.warning` with
  the full `exc`.
- `media_type` is allow-listed; anything outside the allow-list falls back to
  `application/octet-stream` (never rejected, never echoed verbatim).

## 4. Data model / interfaces

- New private helper:
  `_sanitize_filename(value: object) -> str` — replace every character outside
  `[A-Za-z0-9._-]` with `_`, then `strip("._")`; if the result is empty use the
  literal `"artifact"`; cap the result at 255 characters.
- New private constant + helper:
  `_ALLOWED_MEDIA_TYPES = {"text/plain", "image/png"}` and
  `_safe_media_type(value: object) -> str` — return `str(value)` when it is in
  `_ALLOWED_MEDIA_TYPES`, otherwise `"application/octet-stream"`.
- `GET /api/artifacts/{name}/{kind}` — success body unchanged; `Content-Disposition`
  filename and `Content-Type` are sanitised. Failure body becomes
  `{"error": "Artifact not found or unavailable"}` (no `reason`). Status stays
  `404` for the unchanged caught set.
- No schema, migration or config changes.

## 5. Parts

1. `src/api/artifacts.py`:
   - `import re`; add `_ALLOWED_MEDIA_TYPES`, `_sanitize_filename` and
     `_safe_media_type` (§4), each with a short docstring.
   - In `download_artifact`, compute
     `filename = _sanitize_filename(data["filename"])` and
     `media_type = _safe_media_type(data.get("media_type"))`; use them in the
     `Response` (`media_type=media_type`,
     `f'attachment; filename="{filename}"'`).
   - **Leave** `except (KeyError, ValueError, RuntimeError, OSError) as exc:`
     (order may be normalised to `(KeyError, ValueError, OSError, RuntimeError)`,
     but the set must not shrink).
   - Change the 404 body to
     `content={"error": "Artifact not found or unavailable"}` (remove
     `"reason": str(exc)`); keep the `logger.warning(...)` line as-is.

2. `tests/test_artifacts.py` — update/add tests (see §6).

## 6. Tests

Update `tests/test_artifacts.py`:

- Add `test_download_artifact_sanitizes_filename`: a fake manager returning
  `filename='alice";\r\nX-Evil: 1.conf'` yields a `Content-Disposition` header
  equal to `attachment; filename="alice____X-Evil__1.conf"`; assert the header
  value contains no `\r`, no `\n`, and exactly two `"`.
- Add `test_download_artifact_sanitizes_media_type`: a manager returning an
  unsafe `media_type` (e.g. `'text/plain\r\nX-Evil: 1'`) yields
  `response.media_type == "application/octet-stream"`; a safe one
  (`image/png`) is preserved.
- Replace `test_download_artifact_reports_reason` with
  `test_download_artifact_hides_internal_reason`: a manager raising
  `RuntimeError("Client 'alice' not found")` (how a plugin-side not-found
  actually arrives) returns `404` with body exactly
  `{"error": "Artifact not found or unavailable"}` (assert no `"reason"` key).
- Add `test_download_artifact_maps_plugin_errors_to_404` parametrised over
  `ValueError("Client 'alice' not found")`, `FileNotFoundError("...")`,
  `OSError("...")`, `RuntimeError("Plugin 'LoomWG' is unavailable")`,
  `TimeoutError("timed out")` — each returns `404` (do **not** assert
  propagation).

Keep `test_download_artifact_requires_admin_token` and
`test_download_artifact_returns_file` unchanged (the latter may gain an assertion
that `Content-Type`/`media_type` is `text/plain`).

Gates (run from the repository root):

```bash
uv run ruff check .
uv run pytest tests/test_artifacts.py -q
uv run pytest -m "not integration and not e2e" -q
```

## 7. Acceptance criteria

- A plugin filename containing `"`, CR, LF, spaces or path separators cannot
  break out of `filename="..."` and cannot inject a header; only `[A-Za-z0-9._-]`
  survives.
- A plugin `media_type` outside `{text/plain, image/png}` cannot reach
  `Content-Type`; it becomes `application/octet-stream`.
- Plugin-side failures (`RuntimeError`, including the tunnelled "Client not
  found", plus `OSError`/`TimeoutError`/`FileNotFoundError`/`ValueError`) still
  return `404` — no new `500`s.
- The failure body no longer contains `reason` and never echoes the raw
  exception text.
- `ruff check .` is clean.
- `pytest -m "not integration and not e2e"` passes.

## 8. Commits

- `fix(api): sanitize artifact download headers and stop leaking error detail`

## 9. Risks

- `KeyError` remains in the caught set, so malformed plugin output (missing
  `content_base64`/`filename`) still returns `404` rather than `500`. This is
  the pre-existing behaviour; out of scope to change here.
- Sanitising is ASCII-only, so a non-ASCII client name becomes underscores in
  the download filename (the file content and chat label are unaffected).
- The ASGI `Response` may normalise headers; assert on
  `response.headers["content-disposition"]` and `response.media_type`, not raw
  bytes.
