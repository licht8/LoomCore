# TZ_RU.md — explain tool output (unbounded summary)

## 1. Goal

After a tool runs, the assistant must explain the tool output in its own words —
a real explanation, not a one-liner and not the raw dump. The 15-second hard
bound introduced by `fast-summary-bounded` is too aggressive with a thinking
model: the summary is cut off, and the user gets the deterministic digest
(`- ops_system_metrics: done — ...`) instead of an explanation.

Owner request (verbatim): "нужно чтобы после вывода инструментов ассистент сам
обьяснял вывод, убери этот лимит на 15 секунд и сделай чтобы ассистент отвечал как
я скинул после сообщения обьяснить".

## 2. Non-goals

- No change to the planner, the WebSocket protocol, or the frontend.
- No removal of the digest fallback (it stays for genuine failures).
- No change to the read-only-command raw shortcut (`LOOMCORE_REPORT_COMMANDS`).

## 3. Approved decisions (locked)

- **D1** `LLM_SUMMARY_TIMEOUT` default `15.0` -> `0.0`, where `0` means "no extra
  bound": the summary then uses the normal `LLM_READ_TIMEOUT` and normal retries.
  `src/config.py` `llm_summary_timeout` and `src/llm/config.py`
  `summary_timeout`; `load_config` uses a new `_as_nonnegative_float` (0 allowed,
  negatives fall back to the base).
- **D2** `src/llm/router.py` `summarize_plan._call` only passes
  `read_timeout=config.summary_timeout, attempts=1` when `config.summary_timeout > 0`.
- **D3** `LLM_SUMMARY_MAX_TOKENS` default `256` -> `512` so an explanation fits.
- **D4** `src/llm/prompt.py` `SUMMARY_INSTRUCTION`: ask for a clear own-words
  explanation (a short paragraph or a tight bullet list) instead of "at most two
  short sentences".
- **D5** Docs/defaults updated: `.env.example`, `docker-compose.yml`,
  `docs/setup/DEPLOY.md`, `docs/architecture/AGENT.md`, `CHANGELOG.md`.

## 4. Data model / interfaces

No new fields; `LLM_SUMMARY_TIMEOUT` now accepts `0` (disabled).

## 5. Parts

1. `src/config.py`, `src/llm/config.py` — defaults + `_as_nonnegative_float`.
2. `src/llm/router.py` — conditional bound.
3. `src/llm/prompt.py` — summary instruction.
4. Docs + `CHANGELOG.md`.
5. Tests.

## 6. Tests

- `tests/test_llm_config.py`: `summary_timeout: 0` disables the bound; a negative
  value falls back to the base.
- `tests/test_router.py`: with `summary_timeout=0` the summary call receives no
  `read_timeout`/`attempts` override.

## 7. Acceptance criteria

- A tool turn is summarized in the assistant's own words; the digest appears only
  when a positive bound is configured and the summary fails, or on a genuine
  error.
- `uv run ruff check .` clean and `uv run pytest -m "not integration and not e2e"` green.

## 8. Commits

One commit: `fix(llm): explain tool output by default (unbounded summary)`.

## 9. Risks

- An unbounded summary can wait longer (the previous complaint). Mitigated by
  pointing `LLM_SUMMARY_MODEL` at a fast non-thinking model and
  `LLM_THINKING_TEMPLATE=1`.
