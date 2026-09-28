# TZ_RU.md — planner argument grounding and log-source aliases

> Copy to `docs/tasks/<kebab-name>/TZ_RU.md` and fill in **before** starting
> (RULES 16). Content is English (RULES 0). Keep it specific and testable.

## 1. Goal

Fix two failures seen in a real session:

- **The planner invented an invalid enum value.** It called
  `ops_log_search(source="journald")`; the tool schema allows only
  `["auto", "journal", "files"]`, so JSON-schema validation rejected the plan and
  the turn ended with the raw
  `I could not form a valid plan: ops_log_search: source: 'journald' is not one of ['auto', 'journal', 'files']`
  (twice: the initial attempt and the single re-ask). Root cause:
  `AIRouter._catalog_text` (`src/llm/router.py:697-711`) renders only
  `- name(arg, arg): description` and omits each argument's `enum`/`default`
  constraints, so the planner has no way to know the allowed values and guesses.
- **`ops_log_search` is intolerant of the obvious alias.** "journald" is the
  natural word for the user/model, but only `journal` is accepted.

## 2. Non-goals

- No change to the report-tool / summary policy or the `reply`/`agent_plan`
  protocol.
- No frontend change.
- Do not change how `validate_tool_args` reports errors (raw contract stays).

## 3. Approved decisions (locked)

### D1 — the tool catalog shows enum/default constraints

- `src/llm/router.py` `_catalog_text` renders each property with its constraints
  in brackets when present, e.g.
  `- ops_log_search(source [auto|journal|journald|files, default auto], query, …): …`.
  Render rules:
  - enum -> the values joined with `|`;
  - `default` -> `default <value>`;
  - join the bracketed pieces with `, `; a plain property keeps just its name.
- Keeps the existing `_MAX_TOOL_DESC_CHARS` truncation of the description.

### D2 — `ops_log_search` accepts `journald` as a journal alias

- `plugins/LoomOps/LoomOps/schemas.py` `ops_log_search` `source` enum becomes
  `["auto", "journal", "journald", "files"]` and the description mentions the
  alias.
- `plugins/LoomOps/LoomOps/ops/logs.py` `log_search` normalizes the value through
  a small `_SOURCE_ALIASES = {"journald": "journal"}` map before the existing
  `auto`/`journal`/`files` branching, so validation passes and the journal path
  runs.

## 4. Data model / interfaces

- Planner catalog text format gains optional `[enum, default]` annotations (no
  protocol change; the catalog is prompt context only).
- `ops_log_search` accepts one extra enum value (additive).

## 5. Parts

1. `src/llm/router.py` — `_catalog_text`.
2. `plugins/LoomOps/LoomOps/schemas.py` + `plugins/LoomOps/LoomOps/ops/logs.py`.
3. Tests + `CHANGELOG.md`.

## 6. Tests

- `tests/test_router.py`: add `test_catalog_renders_enum_and_default` — a tool
  whose property declares `enum` and `default` yields `a|b|c` and `default` in
  the catalog; keep `test_catalog_truncates_long_descriptions` green.
- `tests/test_LoomOps_tools.py`: add `test_log_search_accepts_journald_alias`
  (monkeypatch `journal_available` true; `log_search({"source": "journald"})`
  behaves like `"journal"`).
- Gates: `uv run ruff check .`; `uv run pytest -m "not integration and not e2e" -q`.

## 7. Acceptance criteria

- The catalog line for `ops_log_search` lists the allowed `source` values, so the
  planner no longer needs to guess (`journald` appears once the alias ships).
- `ops_log_search(source="journald")` runs the journal path instead of failing
  validation.
- `uv run ruff check .` clean; `uv run pytest -m "not integration and not e2e" -q`
  green.

## 8. Commits

1. `feat(llm): show enum and default constraints in the planner tool catalog`
2. `fix(loomops): accept journald as an alias for the journal log source`

## 9. Risks

- Longer catalog lines: bounded (the arg part is small; only the description is
  capped today).
- The `ops_systemctl_status` **verbatim echo** is not addressed by this TZ: on
  the current HEAD that tool is not a report tool, so the turn summarizes it
  instead of returning raw output. If it persists, inspect `llm_usage`
  (`purpose='summary'`, `status`) and the logs for `Plan summary failed`, and
  confirm the deployment runs a build newer than the all-report summary change
  (redeploy + `alembic upgrade head`).
