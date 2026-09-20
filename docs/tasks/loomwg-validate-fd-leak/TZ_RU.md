# TZ_RU.md — LoomWG `validate_config_text` file-descriptor leak

> Spec-first per RULES 16 (content in English, RULES 0). Report goes to
> `docs/tasks/loomwg-validate-fd-leak/REPORT_RU.md` (executor; RULES 0a).

## 1. Goal

Close a file-descriptor leak in
`plugins/LoomWG/LoomWG/wg/validate.py` → `validate_config_text`.

`tempfile.mkstemp` returns an already-open descriptor, but
`os.chmod(tmp_path, 0o600)` runs **before** `os.fdopen(descriptor, ...)`. If
`os.chmod` raises (e.g. `EPERM` under a restrictive umask or on a filesystem
that refuses the change), the `finally` block unlinks the path but never closes
the raw descriptor, leaking one fd for the lifetime of the plugin process.
`mkstemp` already creates the file with mode `0600`, so the `chmod` is
redundant.

## 2. Non-goals

- No change to `check_config`, `_read_config_text`, `_config_path`,
  `run_command`, or any other module/function.
- No behavior change on the success path: the temp `.conf` is still written,
  `wg-quick strip` is still run against it, and the file is still removed in
  `finally`.
- No new dependency and no change to the temp-file naming/mode contract beyond
  dropping the redundant `chmod`.

## 3. Approved decisions (locked)

- **Drop `os.chmod`.** Rely on `tempfile.mkstemp`'s default mode `0600`
  (documented and platform-guaranteed on POSIX).
- **Wrap immediately.** Make
  `with os.fdopen(descriptor, "w", encoding="utf-8") as tmp_file:` the first
  statement inside the `try`, so the descriptor is owned (and closed) by the
  context manager with no intervening fallible call.
- **Keep the `finally` cleanup** (`tmp_path.unlink()` under
  `contextlib.suppress(OSError)`).
- **Do not remove imports.** `os` is still used elsewhere in the module
  (`os.lstat`, `os.open`, `os.fstat`, `os.close`).

## 4. Data model / interfaces

None. Public signature unchanged:
`validate_config_text(text: str) -> None` (raises `CommandError` when
`wg-quick strip` fails).

## 5. Parts

1. **`plugins/LoomWG/LoomWG/wg/validate.py` — `validate_config_text`**
   (currently lines 126-137): delete `os.chmod(tmp_path, 0o600)` and open the
   descriptor as the first `try` statement:

   ```python
   descriptor, tmp_name = tempfile.mkstemp(suffix=".conf")
   tmp_path = Path(tmp_name)
   try:
       with os.fdopen(descriptor, "w", encoding="utf-8") as tmp_file:
           tmp_file.write(text)
       run_command(["wg-quick", "strip", str(tmp_path)], sensitive=True)
   finally:
       with contextlib.suppress(OSError):
           tmp_path.unlink()
   ```

2. **Tests** — see §6.

## 6. Tests

- `tests/test_LoomWG_unit.py`: add a regression test
  `test_validate_config_text_does_not_chmod_temp`:
  - `load_loomwg("validate")["validate"]`.
  - monkeypatch `validate.os.chmod` with a recorder that appends calls.
  - monkeypatch `validate.run_command` to return a value (no real `wg-quick`).
  - call `validate.validate_config_text("[Interface]\nPrivateKey = x\n")`.
  - assert `os.chmod` was **not** called (the file is created `0600` by
    `mkstemp`) and the temp file is gone.
- Keep existing tests passing:
  `test_validate_config_text_cleans_temp_and_raises` and
  `test_validate_config_text_passes_and_cleans_temp`.

Gate commands:

```
uv run ruff check .
uv run pytest tests/test_LoomWG_unit.py -q
uv run pytest -m "not integration and not e2e" -q
```

## 7. Acceptance criteria

- `validate_config_text` no longer calls `os.chmod`.
- The `mkstemp` descriptor is passed to `os.fdopen` with no other call in
  between, so no failure can leak it.
- Success and failure paths still write the temp file, run `wg-quick strip`,
  and unlink the temp file (existing two tests stay green).
- The new regression test passes; `ruff check .` is clean; the full fast suite
  passes.

## 8. Commits

1. `fix(LoomWG): drop redundant chmod that could leak the temp fd`

(Coordinator commits only after acceptance — RULES 0a; no commits by the
executor.)

## 9. Risks

- On platforms where `mkstemp` does not honor mode `0600` (Windows), the file
  was already effectively unprotected by `chmod` semantics; dropping it changes
  nothing practical for the plugin, which targets Linux.
- The residual window where `os.fdopen` itself raises is not separately guarded;
  it is a pure-Python call with a valid fd/mode and is not a realistic failure
  point (the previous leak required a syscall — `chmod` — that no longer runs).
  If the reviewer prefers belt-and-braces, mirror `_read_config_text`
  (lines 90-95) and close the descriptor on an `fdopen` failure.
