# TZ_RU.md — faster summary replies

## 1. Goal

Cut the post-tool wait. After a tool runs, the assistant still owes the user one
short "own words" line, produced by a separate `summary` LLM call
(`AIRouter.summarize_plan`, invoked from `_run_plan` -> `_summarize`). That call
was measured at ~10s for a single mutation and made the turn feel stuck even
though the tool itself had already returned.

Keep the own-words contract; make that one call faster.

## 2. Non-goals

- No change to *when* the summary runs (every plan is still summarized; only an
  explicit raw-output request or an allow-listed read-only command skips it).
- No protocol/frontend change.
- No new LLM call removed: the summary is one generation and stays one.

## 3. Approved decisions (locked)

- **D1 — short output.** `SUMMARY_INSTRUCTION` (`src/llm/prompt.py`) demands at
  most two short sentences (or a short bullet list) and forbids padding. The
  default `summary_max_tokens` drops from `512` to `256`
  (`src/config.py`, `src/llm/config.py`, `.env.example`,
  `docker-compose.yml`).
- **D2 — no thinking on the summary.** `OpenAICompatClient._build_payload`
  gains a per-call `thinking: bool | None` (threaded through
  `chat_completion`/`stream_chat_completion`); `AIRouter.summarize_plan` passes
  `thinking=False`, so with `LLM_THINKING_TEMPLATE=1` the summary always sends
  `enable_thinking=false` while the planner still honors `LLM_ENABLE_THINKING`.
  Providers that did not opt into `chat_template_kwargs` are unaffected.
- **D3 — document the lever.** A fast `LLM_SUMMARY_MODEL` is the main lever for
  the post-tool wait (`.env.example`, `docs/setup/DEPLOY.md`,
  `docs/architecture/AGENT.md`).

## 4. Data model / interfaces

No schema change. The client payload may carry
`chat_template_kwargs.enable_thinking = false` for summary calls only.

## 5. Parts

1. `src/llm/client.py` — `thinking` override in `_build_payload` + both call
   methods.
2. `src/llm/router.py` — pass `thinking=False` in `summarize_plan`.
3. `src/llm/prompt.py` — terse `SUMMARY_INSTRUCTION`.
4. `src/config.py`, `src/llm/config.py` — `summary_max_tokens` default 256.
5. `.env.example`, `docker-compose.yml`, `docs/setup/DEPLOY.md`,
   `docs/architecture/AGENT.md`, `CHANGELOG.md`.

## 6. Tests

- `tests/test_llm_client.py::test_summary_can_disable_thinking` — payload
  override.
- `tests/test_router.py::test_summary_disables_thinking` — the summary call gets
  `thinking=False`.
- Gates: `uv run ruff check .`, `uv run pytest -m "not integration and not e2e"`.

## 7. Acceptance criteria

- A summary request sends `enable_thinking=false` when
  `LLM_THINKING_TEMPLATE=1`.
- The summary cap defaults to 256 tokens.
- Full fast suite + ruff pass.

## 8. Commits

Suggested: `perf(llm): shorten and de-think the post-tool summary`.

## 9. Risks

- `256` tokens may truncate a genuinely long list summary; raise
  `LLM_SUMMARY_MAX_TOKENS`.
- Disabling thinking can lower quality on complex multi-tool turns; the planner
  is unaffected and `LLM_THINKING_TEMPLATE=0` opts out entirely.
- Restoring an old `.env` that pins `LLM_SUMMARY_MAX_TOKENS=512` overrides the
  new default (intended).
