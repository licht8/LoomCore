# TZ_RU.md — conversation-memory-compaction

## 1. Goal

Today the planner prompt silently drops everything older than
`_MAX_HISTORY_CHARS = 6000` characters (`src/llm/prompt.py:6`,
`_trim_history` at `:145`). In a long conversation the assistant therefore
**forgets the beginning**: facts, numbers, decisions and preferences stated
early vanish without any trace.

Goal: keep long sessions coherent by compacting the older part of the history
into a short, persisted **memory summary** that is injected ahead of the
verbatim tail — so the assistant still knows what was discussed, without
growing the prompt or slowing the turn.

## 2. Non-goals

- No vector store / embeddings / RAG, no cross-session or user-level memory.
- No change to `_MAX_HISTORY_CHARS` (6000 stays the verbatim tail budget) and no
  change to the post-tool summary path (`summarize_plan`).
- No frontend change and no new WS event.
- No new plugin (this is core: prompt assembly + a DB column).
- No new env knob in this task: the thresholds are module constants in
  `src/core/memory.py` (an operator-facing switch, if ever needed, is a separate
  task).

## 3. Approved decisions (locked)

**D1 — Store the memory on the session.**
Add to `Session` (`src/db/models.py`) two nullable columns via a migration
(`down_revision` = the current head, `010` unless `persistent-text-approval`
already landed):
- `memory: Text | None` — the rolling summary of the older conversation;
- `memory_updated_at: String | None` — the `created_at` of the newest message
  already folded into `memory` (the coverage marker, naive-UTC ISO).

**D2 — One compaction module.**
New `src/core/memory.py` with:

```python
MEMORY_KEEP_CHARS = 6000        # verbatim tail kept as-is (matches the prompt)
MEMORY_COMPACT_AFTER_CHARS = 12000  # only compact when the tail exceeds this

async def load_memory(session_db, session_id) -> str | None
async def refresh_memory(session_db, session_id, *, config, client) -> str | None
def build_memory_messages(existing_memory: str | None, folded: list[dict]) -> list[dict]
def select_foldable(history: list[dict], *, keep_chars: int) -> tuple[list[dict], list[dict]]
```

`select_foldable` splits the (oldest-first) history into `folded` (everything
before the last `MEMORY_KEEP_CHARS` of the verbatim tail) and `kept`.

**D3 — Prompt injection.**
`build_planner_messages(user_text, history, tool_catalog, *, memory=None)` adds a
labelled block **before** the trimmed history, e.g.

```
Conversation so far (earlier messages, condensed — not verbatim):
<memory>
```

Rendered as an additional `system` message (soul + planner prompt + catalog stay
first). When `memory` is `None`/empty the prompt is byte-identical to today.

**D4 — Compaction runs off the hot path.**
- `AIRouter._begin_turn` also loads `Session.memory` and passes it into
  `build_planner_messages`; `route` exposes it via `self` for the current turn.
- At the **end** of `route` (a `finally`-guarded call) the router calls
  `refresh_memory(...)` only when both hold:
  1. the untrimmed history is longer than `MEMORY_COMPACT_AFTER_CHARS`, and
  2. there are `folded` messages newer than the stored `memory_updated_at`.
- The refresh must never affect the turn: wrap it in `try/except` and log a
  warning on failure, leaving the stored memory unchanged.
- One refresh per session at a time: a per-session `asyncio.Lock`/in-flight set;
  a concurrent turn skips the refresh (the next turn will pick it up).
- The new memory = the model's condensation of `existing_memory` + `folded`
  messages; store it with `memory_updated_at` = the newest folded message's
  `created_at` (idempotent: the same messages are never folded twice).

**D5 — Reuse the summary model, bounded and non-thinking.**
The compaction call uses `config.summary_model or None`,
`max_tokens=config.summary_max_tokens`, `thinking=False` when
`config.summary_disable_thinking`, `purpose="memory"` (so `llm_usage` separates
it), and honours `config.summary_timeout` the same way `summarize_plan` does.
New `MEMORY_INSTRUCTION` in `src/llm/prompt.py`: condense into a terse factual
brief (decisions, facts, numbers, open questions, user preferences), drop
pleasantries, never invent.

**D6 — Degrade to today's behaviour.**
If memory is missing, compaction fails, or the model returns empty, the prompt
is exactly today's trimmed history. No user-visible error.

## 4. Data model / interfaces

- **DB**: `sessions.memory` TEXT NULL, `sessions.memory_updated_at` STRING NULL
  (new migration, `src/db/models.py`, plus a `test_db_models.py` assertion).
- **Helpers** in `src/db/session.py`: `set_memory(session_db, session_id, text,
  updated_at)` and the read is done through the existing session row (or a small
  `get_memory(session_db, session_id) -> tuple[str | None, str | None]`).
- **Prompt**: `build_planner_messages(..., *, memory: str | None = None)`
  (signature is additive; existing callers keep working).
- No WS event, no REST change, no `WS_SCHEMA_VERSION` bump.

## 5. Parts

1. **DB + migration** — `src/db/models.py`, `alembic/versions/0xx_session_memory.py`,
   `src/db/session.py` helpers.
2. **Compaction module** — `src/core/memory.py` (select/load/refresh/build).
3. **Prompt** — `src/llm/prompt.py` (`build_planner_messages` memory block +
   `MEMORY_INSTRUCTION`).
4. **Router wiring** — `src/llm/router.py`: `_begin_turn` loads memory, passes
   it to the prompt, and `route` schedules the guarded refresh.
5. **Docs** — `docs/architecture/AGENT.md` (how long-conversation memory works
   and its bounds) + `CHANGELOG.md` (`## 2026-09-29`).

## 6. Tests

- `tests/test_memory.py` (new): `select_foldable` keeps the last
  `MEMORY_KEEP_CHARS` and folds the rest; `refresh_memory` folds
  `existing_memory` + new messages once and stores the coverage marker; a
  second call folds nothing; a failing/empty model leaves the stored memory
  unchanged; `load_memory` returns `None` for a fresh session.
- `tests/test_prompt.py`: the memory block appears before the history when set;
  the prompt is unchanged when `memory=None`; `MEMORY_INSTRUCTION` is rendered.
- `tests/test_router.py`: `route` passes the session memory into the prompt
  (spy on `build_planner_messages`/fake DB); refresh is attempted only when the
  history exceeds `MEMORY_COMPACT_AFTER_CHARS`; a refresh failure does not fail
  the turn.
- `tests/test_db_models.py` / `tests/test_db_session.py`: the columns and the
  `set_memory` helper.
- **Gates**: `uv run ruff check .`; `uv run pytest -m "not integration and not
  e2e" -q`; `alembic upgrade head` applies the migration (manual/integration).

## 7. Acceptance criteria

- In a session whose history exceeds `MEMORY_COMPACT_AFTER_CHARS`, a fact stated
  at the very beginning is still available to the planner on a later turn (via
  the memory block), where today it would have been dropped.
- A short session behaves exactly as before (no memory call, identical prompt).
- The reply is never slowed by compaction (it runs after the result; failures
  are swallowed and logged).
- `sessions.memory`/`memory_updated_at` are session-scoped and advance
  monotonically; re-running a turn never double-folds.
- Gates green; migration applies cleanly.

## 8. Commits

1. `feat(db): store a per-session conversation memory` (models + migration +
   session helpers + tests).
2. `feat(core): compact older history into a memory summary` (`src/core/memory.py`,
   prompt block, router wiring, tests, docs, CHANGELOG).

## 9. Risks

- **Quality of the condensed memory** — mitigated by the terse factual
  instruction and by keeping the last 6000 chars verbatim; a bad memory can only
  paraphrase what was already said.
- **Extra model calls / cost** — bounded: only when the history exceeds the
  threshold, deduplicated by the coverage marker, one per session at a time.
- **Prompt regression** — the memory block is additive and skipped when absent;
  a dedicated test pins the no-memory prompt.
- **Concurrent turns** — the per-session guard skips duplicate refreshes; a
  skipped refresh is retried on the next turn.
- **Migration ordering** — set `down_revision` to the current head at
  implementation time (coordinate with `persistent-text-approval`, which also
  adds a migration).
