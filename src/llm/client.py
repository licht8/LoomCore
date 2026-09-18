import asyncio
import logging
import time

import httpx

from src.config import settings
from src.core.security_log import log_security_event
from src.core.timing import log_stage
from src.llm.budget import consume_llm_budget
from src.llm.exceptions import LlmBudgetExceeded, LlmError, LlmTimeoutError

logger = logging.getLogger("loomcore.llm")


class HetznerClient:
    """Client for the Hetzner Inference API."""

    MODEL = settings.hetzner_model

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.hetzner_api_key
        self.base_url = settings.hetzner_base_url

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=settings.hetzner_connect_timeout,
            read=settings.hetzner_read_timeout,
            write=settings.hetzner_write_timeout,
            pool=settings.hetzner_pool_timeout,
        )

    async def chat_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        temperature: float | None = None,
    ) -> dict:
        """Send a request to the LLM and return the JSON response.

        ``tool_choice`` and ``temperature`` are forwarded when set (``None``
        leaves the provider default).         Retries transport/timeout errors up to
        `HETZNER_MAX_RETRIES` times and raises `LlmTimeoutError`/`LlmError` with
        a clear message on failure. Each attempt consumes the process-wide daily
        call budget and raises `LlmBudgetExceeded` when it is exhausted.
        """
        payload: dict = {
            "model": self.MODEL,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
        if temperature is not None:
            payload["temperature"] = temperature
        if not settings.hetzner_enable_thinking:
            # Hybrid reasoning models spend most of the turn emitting hidden
            # reasoning tokens; for chat planning/summaries that is pure
            # latency. Disable it unless the operator opts back in.
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        prompt_chars = sum(
            len(str(message.get("content") or "")) for message in messages
        )
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        max_attempts = max(1, settings.hetzner_max_retries + 1)
        last_exc: Exception | None = None

        async with httpx.AsyncClient(timeout=self._timeout()) as client:
            for attempt in range(1, max_attempts + 1):
                try:
                    budget = consume_llm_budget()
                except LlmBudgetExceeded as exc:
                    log_security_event(
                        "llm_budget_exceeded",
                        limit=settings.llm_daily_call_limit,
                        detail=str(exc),
                    )
                    raise
                started = time.monotonic()
                logger.info(
                    f"📡 LLM request: model={self.MODEL}, "
                    f"attempt={attempt}/{max_attempts}, "
                    f"budget_used={budget['used']}"
                )
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    elapsed = time.monotonic() - started
                    logger.info(
                        f"✅ LLM response: status={response.status_code}, "
                        f"size={len(response.content)}, elapsed={elapsed:.1f}s"
                    )
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise LlmError(f"LLM returned invalid JSON: {exc}") from exc
                    usage = data.get("usage") or {}
                    completion_tokens = usage.get("completion_tokens")
                    log_stage(
                        "llm_call",
                        elapsed,
                        model=self.MODEL,
                        attempt=f"{attempt}/{max_attempts}",
                        messages=len(messages),
                        prompt_chars=prompt_chars,
                        approx_prompt_tokens=prompt_chars // 4,
                        tools=len(tools) if tools else 0,
                        prompt_tokens=usage.get("prompt_tokens"),
                        completion_tokens=completion_tokens,
                        out_tok_per_s=(
                            round(completion_tokens / elapsed, 1)
                            if completion_tokens and elapsed > 0
                            else None
                        ),
                    )
                    return data
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code if exc.response is not None else "?"
                    body = exc.response.text[:500] if exc.response is not None else ""
                    logger.error(f"❌ LLM HTTP error {status}: {body}")
                    raise LlmError(f"LLM API error {status}") from exc
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_exc = exc
                    elapsed = time.monotonic() - started
                    logger.warning(
                        f"⚠️ LLM transport error on attempt {attempt}/{max_attempts} "
                        f"after {elapsed:.1f}s: {type(exc).__name__}: {exc}"
                    )
                    if attempt < max_attempts:
                        await asyncio.sleep(min(2 ** (attempt - 1), 5))

        if isinstance(last_exc, httpx.TimeoutException):
            raise LlmTimeoutError(
                f"LLM did not respond within {settings.hetzner_read_timeout:.0f}s"
            ) from last_exc
        raise LlmError(f"LLM request failed: {last_exc}") from last_exc
