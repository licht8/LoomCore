# REPORT_RU.md — artifact download hardening

Task spec: `docs/tasks/artifact-download-hardening/TZ_RU.md` (decisions locked;
the "narrow the except tuple" decision was later withdrawn by the reviewer — see
Notes).

## Goal

Harden `GET /api/artifacts/{name}/{kind}` (`src/api/artifacts.py`): stop a
plugin-supplied `filename` (and `media_type`) from breaking out of the response
headers (injection/crash) and stop leaking raw exception text in the response
body — without turning the common plugin-side "not found" case into a `500`.

## Delivered

- `src/api/artifacts.py`
  - Added `import re`, `_ALLOWED_MEDIA_TYPES = {"text/plain", "image/png"}`,
    `_sanitize_filename(value) -> str` (replace every char outside
    `[A-Za-z0-9._-]` with `_`, `strip("._")`, empty -> `"artifact"`, cap 255;
    `None` is normalised to empty first) and `_safe_media_type(value) -> str`
    (type-guarded allow-list, else `"application/octet-stream"`).
  - `download_artifact` now uses `_sanitize_filename(data["filename"])` in
    `Content-Disposition` and `_safe_media_type(data.get("media_type"))`.
  - **Kept** the caught set `(KeyError, ValueError, OSError, RuntimeError)`:
    the LoomWG dispatcher wraps plugin errors and the manager re-raises them as
    `RuntimeError`, so narrowing the tuple would make not-found a `500`
    (`TimeoutError` and `FileNotFoundError` are `OSError` subclasses).
  - The `404` body is now `{"error": "Artifact not found or unavailable"}`
    (removed `"reason"`); the `logger.warning(...)` line is unchanged.
- `tests/test_artifacts.py`
  - Replaced `test_download_artifact_reports_reason` with
    `test_download_artifact_hides_internal_reason` (exact body, no `reason`).
  - Added `test_download_artifact_sanitizes_filename`,
    `test_download_artifact_returns_404_for_missing_file`,
    `test_download_artifact_maps_backend_failures_to_404` (parametrized:
    `RuntimeError`, `PermissionError`, `TimeoutError`, `OSError` -> `404`),
    `test_download_artifact_sanitizes_media_type`,
    `test_download_artifact_defaults_missing_media_type`,
    `test_sanitize_filename_handles_none`,
    `test_safe_media_type_rejects_non_allowlisted` (and keeps the allow-list).

## Review fixes (applied)

- `_safe_media_type`: added an `isinstance(value, str)` guard before the set
  membership test, so a non-scalar plugin value (list/dict) becomes
  `application/octet-stream` instead of raising `TypeError` -> `500`.
- `_sanitize_filename`: normalise `None` to `""` before `str(...)`, so it hits
  the documented `"artifact"` fallback instead of producing `filename="None"`.

## Verification

- `uv run ruff check .` — clean (`All checks passed!`).
- `uv run pytest tests/test_artifacts.py -q` — 21 passed.
- `uv run pytest -m "not integration and not e2e" -q` — 898 passed, 11 skipped,
  4 deselected (log: `tmp/tests_artifact_download_hardening.log`).
- Filename test: `alice";\r\nX-Evil: 1.conf` becomes
  `attachment; filename="alice____X-Evil__1.conf"`, no CR/LF, exactly two quotes.
- Media type: a disallowed/injected media type becomes
  `application/octet-stream`; a missing key also defaults to it.

## Changed files

- `src/api/artifacts.py` — filename/media-type hardening, no-leak 404 body.
- `tests/test_artifacts.py` — updated/added tests.
- `docs/tasks/artifact-download-hardening/REPORT_RU.md` — this report.

## Commits

none

## Notes and follow-ups

- Scope change: the earlier "narrow the except tuple to
  `(KeyError, ValueError, FileNotFoundError)`" decision (TZ §3/§5) was withdrawn
  after review — the plugin's JSON-RPC dispatcher
  (`plugins/LoomWG/LoomWG/protocol.py`) catches plugin-side
  `OSError`/`RuntimeError`/`ValueError` and `manager.execute_tool` re-raises the
  error response as `RuntimeError`. The tuple was therefore left at
  `(KeyError, ValueError, OSError, RuntimeError)`, which still returns `404` for
  all plugin-side failures and avoids new `500`s.
- `KeyError` remains in the 4xx tuple: a missing `filename`/`content_base64` key
  is arguably a plugin contract violation, but it keeps today's `404`;
  `media_type` is now read with `.get(...)`, so its absence no longer raises.
- Sanitising is ASCII-only, so a non-ASCII client name becomes underscores in
  the download filename; file content and chat label are unaffected.
- `kind` allow-list, auth guard, LoomWG internals and `tool_events.py` were left
  untouched, per the non-goals.
