import asyncio
import logging
import time
from dataclasses import replace

import httpx

from src.config import settings
from src.core.security_log import log_security_event
from src.core.timing import log_stage
from src.llm.budget import consume_llm_budget
from src.llm.config import NOT_CONFIGURED_MESSAGE, LlmConfig, get_active
from src.llm.exceptions import (
    LlmBudgetExceeded,
    LlmError,
    LlmNotConfigured,
    LlmTimeoutError,
)
from src.llm.usage import record_llm_usage

logger = logging.getLogger("loomcore.llm")

# Transient provider statuses worth retrying; everything else fails immediately.
_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying a transient HTTP failure.

    Honours a numeric ``Retry-After`` header (capped at 5s) and otherwise backs
    off exponentially. HTTP-date values are ignored and fall back to the backoff.
    """
    header = response.headers.get("retry-after")
    if header:
        try:
            return min(float(header), 5.0)
        except ValueError:
            pass
    return min(2 ** (attempt - 1), 5)


class OpenAICompatClient:
    """Client for any OpenAI-compatible Chat Completions API.

    The active provider (base URL, model, API key, timeouts and retries) is read
    from the runtime config at the start of every call, so a Settings change
    takes effect on the next turn without restarting the process.
    """

    def __init__(self, api_key: str | None = None, config: LlmConfig | None = None):
        self._api_key_override = api_key
        self._config_override = config
        self._client: httpx.AsyncClient | None = None

    def _resolve_config(self) -> LlmConfig:
        """Return the config to use, honouring an explicit override."""
        if self._config_override is not None:
            base = self._config_override
        else:
            base = get_active()
        if self._api_key_override is None:
            return base
        return replace(base, api_key=self._api_key_override)

    @staticmethod
    def _timeout(config: LlmConfig) -> httpx.Timeout:
        return httpx.Timeout(
            connect=config.connect_timeout,
            read=config.read_timeout,
            write=config.write_timeout,
            pool=config.pool_timeout,
        )

    def _get_client(self) -> httpx.AsyncClient:
        """Return the shared HTTP client, creating it on first use."""
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    async def aclose(self) -> None:
        """Close the shared HTTP client and reset it for a later rebuild."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def chat_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        temperature: float | None = None,
        model: str | None = None,
        *,
        purpose: str = "chat",
        session_id: str | None = None,
    ) -> dict:
        """Send a request to the active provider and return the JSON response.

        ``model`` overrides the configured model for this call (used for the
        summary model). ``tool_choice`` and ``temperature`` are forwarded when
        set (``None`` leaves the provider default). Retries transport/timeout
        errors and transient HTTP statuses (408/429/5xx) up to the configured
        retry count with backoff, then raises `LlmTimeoutError`/`LlmError` with a
        clear message. Raises `LlmNotConfigured` when no provider is set. Each
        attempt consumes the process-wide daily call budget and raises
        `LlmBudgetExceeded` when it is exhausted. The underlying HTTP client is
        shared across calls; close it with :meth:`aclose`.
        """
        config = self._resolve_config()
        if not config.configured:
            raise LlmNotConfigured(NOT_CONFIGURED_MESSAGE)

        active_model = model or config.model
        payload: dict = {
            "model": active_model,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
        if temperature is not None:
            payload["temperature"] = temperature
        if config.thinking_template:
            # Only sent for providers that opt in to ``chat_template_kwargs``.
            payload["chat_template_kwargs"] = {
                "enable_thinking": config.enable_thinking
            }

        prompt_chars = sum(
            len(str(message.get("content") or "")) for message in messages
        )
        url = f"{config.base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        max_attempts = max(1, config.max_retries + 1)
        last_exc: Exception | None = None

        client = self._get_client()
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
                f"📡 LLM request: model={active_model}, "
                f"attempt={attempt}/{max_attempts}, "
                f"budget_used={budget['used']}"
            )
            try:
                response = await client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self._timeout(config),
                )
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
                    model=active_model,
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
                await record_llm_usage(
                    provider=config.provider,
                    base_url=config.base_url,
                    model=active_model,
                    purpose=purpose,
                    session_id=session_id,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=completion_tokens,
                    total_tokens=usage.get("total_tokens"),
                    latency_ms=int(elapsed * 1000),
                    attempts=attempt,
                    status="success",
                )
                return data
            except httpx.HTTPStatusError as exc:
                status = (
                    exc.response.status_code if exc.response is not None else "?"
                )
                body = exc.response.text[:500] if exc.response is not None else ""
                if (
                    isinstance(status, int)
                    and status in _RETRYABLE_STATUSES
                    and attempt < max_attempts
                ):
                    delay = _retry_delay(exc.response, attempt)
                    logger.warning(
                        f"⚠️ LLM HTTP error {status} on attempt "
                        f"{attempt}/{max_attempts}; retrying in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(f"❌ LLM HTTP error {status}: {body}")
                await record_llm_usage(
                    provider=config.provider,
                    base_url=config.base_url,
                    model=active_model,
                    purpose=purpose,
                    session_id=session_id,
                    prompt_tokens=None,
                    completion_tokens=None,
                    total_tokens=None,
                    latency_ms=None,
                    attempts=attempt,
                    status="error",
                    error=f"LLM API error {status}",
                )
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
            await record_llm_usage(
                provider=config.provider,
                base_url=config.base_url,
                model=active_model,
                purpose=purpose,
                session_id=session_id,
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                latency_ms=None,
                attempts=max_attempts,
                status="error",
                error=f"LLM request timed out: {type(last_exc).__name__}",
            )
            raise LlmTimeoutError(
                f"LLM did not respond within {config.read_timeout:.0f}s"
            ) from last_exc
        await record_llm_usage(
            provider=config.provider,
            base_url=config.base_url,
            model=active_model,
            purpose=purpose,
            session_id=session_id,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            latency_ms=None,
            attempts=max_attempts,
            status="error",
            error=f"LLM request failed: {last_exc}",
        )
        raise LlmError(f"LLM request failed: {last_exc}") from last_exc
