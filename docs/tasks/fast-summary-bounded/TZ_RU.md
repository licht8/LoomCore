# TZ_RU.md — Bounded, fast post-tool summary

## 1. Goal

Make the assistant answer **quickly and in its own words** right after a tool
finishes, and hard-cap the worst case.

Reported symptoms (owner transcript): `wg_diagnostics` returned at 03:00, the
assistant then "thought" for ~2 minutes and finally printed the tool output
verbatim. The owner asked for the answer to be short, in its own words, and
fast — for every tool, not just this one.

## 2. Non-goals

- No change to the planner (model, budget, thinking, prompt).
- No change to the WS protocol or the frontend.
- No new "instant tool output" UI path.
- No LLM-free answer path when the model works: the LLM summary stays the
  primary mechanism.

## 3. Root cause

The summary runs on the **same thinking model** as the planner
(`LLM_SUMMARY_MODEL` unset → `model=None` → `config.model`). A reasoning model
spends its whole budget on reasoning, so:

1. The reasoning phase is slow — two attempts of the 30 s read timeout, plus the
   main-model retry added in `f56f531`, add up to ~2 minutes.
2. The visible answer is empty/truncated, so `_summarize` falls back to
   `_raw_summary` — exactly the tool text the owner saw.

Confirmed facts:

- `LlmConfig.read_timeout = 30.0`, `max_retries = 1` → `max_attempts = 2`.
- `AIRouter.summarize_plan` retries with the main model on exception/empty.
- `_summarize` returns `_raw_summary(...)` on exception, empty result, or a
  count mismatch.
- `chat_template_kwargs`/`enable_thinking` is only sent when
  `config.thinking_template` is true (opt-in), so `thinking=False` on the
  summary is a no-op unless the provider opts in.

## 4. Approved decisions (locked)

### D1 — Bound the summary call

- `src/config.py`: `llm_summary_timeout: float = 15.0`
  (`validation_alias="LLM_SUMMARY_TIMEOUT"`).
- `src/llm/config.py`: `LlmConfig.summary_timeout: float = 15.0`;
  `default_config()` maps `settings.llm_summary_timeout`; `load_config` uses
  `_as_float(data.get("summary_timeout"), base.summary_timeout)`.
- `src/llm/client.py`: both `chat_completion` and `stream_chat_completion` gain
  keyword-only `timeout: float | None = None` and `attempts: int | None = None`;
  `_timeout(config, read_override=None)` overrides the read timeout for the
  call; `max_attempts = max(1, attempts or (config.max_retries + 1))`.
- `src/llm/router.py` `summarize_plan`: every summary call passes
  `timeout=config.summary_timeout` and `attempts=1`, so the summary can never
  exceed `summary_timeout` per attempt.

### D2 — Never answer with the raw dump when the summary fails

- New deterministic `_digest_summary(plan, observations)` in
  `src/interfaces/websocket.py`: the goal line, then
  `- <tool>: <status> — <collapsed excerpt (<=240 chars)>` per step.
- `_summarize` uses `_digest_summary` (not `_raw_summary`) on exception, empty
  summary, or count mismatch. `_raw_summary` stays only for an explicit
  raw-output request (`_RAW_OUTPUT_RE`) and the read-only-command shortcut.

### D3 — Bounded retry policy

- `summarize_plan` retries with the main model only on a **non-timeout**
  failure of a configured `summary_model`; `LlmTimeoutError` propagates
  immediately so `_summarize` can build the digest without a second wait.

### D4 — Documentation

- `.env.example`: `LLM_SUMMARY_TIMEOUT=15`; note that a fast non-thinking
  `LLM_SUMMARY_MODEL` is the real fix and that `LLM_THINKING_TEMPLATE=1`
  disables thinking for the summary.
- `docker-compose.yml`: `LLM_SUMMARY_TIMEOUT: ${LLM_SUMMARY_TIMEOUT:-15}`.
- `docs/setup/DEPLOY.md`, `docs/architecture/AGENT.md`: the bounded summary, the
  digest fallback, and the model guidance.
- `CHANGELOG.md`: a dated bullet.

## 5. Parts

1. `src/config.py`, `src/llm/config.py` — `summary_timeout`.
2. `src/llm/client.py` — per-call timeout/attempts override.
3. `src/llm/router.py` — bounded, single-attempt summary + timeout-only-no-retry.
4. `src/interfaces/websocket.py` — `_digest_summary` + `_summarize` fallbacks.
5. Docs/config — `.env.example`, `docker-compose.yml`, `DEPLOY.md`, `AGENT.md`,
   `CHANGELOG.md`.

## 6. Tests

- `tests/test_llm_config.py`: `summary_timeout` from env; `load_config` falls
  back when the key is absent.
- `tests/test_llm_client.py`: `_timeout` honours the read override; a call with
  `attempts=1` does not retry a retryable failure.
- `tests/test_router.py`: the summary call receives
  `timeout=config.summary_timeout` and `attempts=1`; a timeout is not retried
  with the main model (asserts a single call), while a non-timeout failure still
  falls back to the main model.
- `tests/test_websocket.py`: a failing summary yields `_digest_summary` (goal +
  per-step lines), never the raw text; `_digest_summary` collapses long output.

Gates: `uv run ruff check .` and
`uv run pytest -m "not integration and not e2e" -q`.

## 7. Acceptance criteria

- A summary that stalls cannot block a turn for more than `LLM_SUMMARY_TIMEOUT`
  (plus the bounded main-model retry on a non-timeout error).
- When the summary fails, the assistant posts a short digest, never the raw tool
  dump.
- `LLM_SUMMARY_MODEL` set to a fast non-thinking model remains the recommended
  fix and is documented as such.
- `uv run ruff check .` clean; fast pytest green.

## 8. Commits

One commit: `perf(llm): bound and de-raw the post-tool summary`.

## 9. Risks

- 15 s may be too short for a slow-but-working model: the digest appears instead
  of the LLM summary. Mitigated by `LLM_SUMMARY_TIMEOUT` (env/settings file) and
  by recommending a fast `LLM_SUMMARY_MODEL`.
- Streaming partial text before a failure is replaced by the final
  `response` payload, so no duplicated text is left in the bubble.
- The digest still quotes short excerpts of tool text; it is intentionally not a
  wall of text.
