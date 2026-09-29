# TZ_RU.md — fast own-words answers

## 1. Goal

Make the assistant answer a tool turn **in its own words and fast**.

Today, after a tool runs, the post-tool summary call is often the slowest part of
the turn: when the provider serves a reasoning model, the model spends a long
time thinking *before* writing anything, the streamed `delta` events stay silent
for the whole thinking phase, and — if the visible answer comes back empty or the
call is bounded — the assistant falls back to a deterministic digest that reads
like a dumb truncation of the tool output.

Change the default so the summary call asks the provider to **skip thinking**, so
the own-words answer starts streaming almost immediately.

## 2. Non-goals

- No change to the planner call or the tool execution path.
- No new UI/protocol event.
- No change to the "every plan is summarized" contract, the digest fallback, or
  the unbounded-by-default `LLM_SUMMARY_TIMEOUT`.
- No deterministic intent → tool shortcut.

## 3. Approved decisions (locked)

- **D1 — `LLM_SUMMARY_DISABLE_THINKING` (default `1`).** New
  `Settings.llm_summary_disable_thinking: bool = True` (`src/config.py`) and
  `LlmConfig.summary_disable_thinking: bool = True` (`src/llm/config.py`), mapped
  in `default_config()` and read in `load_config()` with the base value as the
  fallback when the key is absent.
- **D2 — the client sends the thinking flag whenever it is explicit.** In
  `src/llm/client.py` `_build_payload`, the `chat_template_kwargs` block is sent
  when `config.thinking_template` **or** `thinking is not None`, so an explicit
  `thinking=False` reaches a provider that was never opted in via
  `LLM_THINKING_TEMPLATE`.
- **D3 — the summary uses the flag, with a self-healing retry.** In
  `src/llm/router.py` `summarize_plan`, `_call(model_name, *, disable_thinking)`
  passes `thinking=False if disable_thinking else None`. The first attempt uses
  `config.summary_disable_thinking`; a **non-timeout** failure is retried once
  **without the flag** (same summary model, or the main model when no summary
  model is set). `LlmTimeoutError` still propagates immediately, and an empty
  result still falls back to the main model.
- **D4 — docs.** Record the new default in `.env.example`,
  `docker-compose.yml`, `docs/setup/DEPLOY.md` and `docs/architecture/AGENT.md`
  (intended-defaults lists), and add a `CHANGELOG.md` entry.

## 4. Data model / interfaces

No schema or wire change. New config knob only:

| Where | Name | Default |
| --- | --- | --- |
| env | `LLM_SUMMARY_DISABLE_THINKING` | `1` |
| `Settings` | `llm_summary_disable_thinking` | `True` |
| `LlmConfig` | `summary_disable_thinking` | `True` |
| settings JSON (Settings tab) | `summary_disable_thinking` | absent → base value |

## 5. Parts

1. `src/config.py` — new field, placed after `llm_thinking_template`.
2. `src/llm/config.py` — field, `default_config()` mapping, `load_config()`
   key-present fallback.
3. `src/llm/client.py` — `_build_payload` condition.
4. `src/llm/router.py` — `_call(..., disable_thinking=...)` + the retry chain and
   the docstring.
5. Docs — `.env.example`, `docker-compose.yml`, `docs/setup/DEPLOY.md`,
   `docs/architecture/AGENT.md`, `CHANGELOG.md`.

## 6. Tests

- `tests/test_llm_config.py` — `summary_disable_thinking` from env, absent-key
  fallback, explicit value.
- `tests/test_llm_client.py` —
  `test_summary_disables_thinking_without_template` (an explicit
  `thinking=False` sends `chat_template_kwargs` even with
  `thinking_template=False`; `thinking=None` does not).
- `tests/test_router.py` —
  `test_summarize_plan_retries_without_thinking_flag_when_rejected` (first
  attempt `thinking=False`, retry `thinking=None`, same model).
  `test_summary_disables_thinking` (`thinking is False`) must keep passing.

## 7. Acceptance criteria

- `uv run ruff check .` clean.
- `uv run pytest -m "not integration and not e2e" -q` green.
- With a reasoning provider, a tool turn streams the own-words answer without a
  long silent thinking pause; a provider that rejects `chat_template_kwargs`
  still answers (one retry without the flag).
- No frontend change required (`npx tsc --noEmit` / `npm run test` /
  `npm run build` unaffected).

## 8. Commits

One commit: `perf(llm): skip thinking for the post-tool summary`.

## 9. Risks

- A provider that rejects unknown fields fails the first summary attempt; the
  retry without the flag restores the old behavior (one wasted call).
- If the provider silently ignores `enable_thinking`, nothing changes — a fast
  `LLM_SUMMARY_MODEL` remains the strongest lever.
- Turning thinking off can shorten a deep explanation; `LLM_SUMMARY_DISABLE_THINKING=0`
  restores the old behavior.
