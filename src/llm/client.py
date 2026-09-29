import asyncio
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
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
from src.llm.security import validate_provider_url
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


def _token_count(value: object) -> int | None:
    """Coerce a provider token count to ``int``; ignore non-numeric values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return int(value)


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
    def _timeout(config: LlmConfig, read_timeout: float | None = None) -> httpx.Timeout:
        return httpx.Timeout(
            connect=config.connect_timeout,
            read=config.read_timeout if read_timeout is None else read_timeout,
            write=config.write_timeout,
            pool=config.pool_timeout,
        )

    def _get_client(self) -> httpx.AsyncClient:
        """Return the shared HTTP client, creating it on first use."""
        if self._client is None:
            self._client = httpx.AsyncClient(follow_redirects=False)
        return self._client

    async def aclose(self) -> None:
        """Close the shared HTTP client and reset it for a later rebuild."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _build_payload(
        config: LlmConfig,
        messages: list[dict],
        *,
        model: str | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        thinking: bool | None = None,
    ) -> dict:
        """Build the Chat Completions payload shared by both call paths.

        ``thinking`` overrides ``config.enable_thinking`` for this call only,
        so the short summary can skip a reasoning model's thinking step.
        """
        payload: dict = {
            "model": model or config.model,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if config.thinking_template or thinking is not None:
            # Sent for providers that opt in via ``thinking_template``, and for
            # any call that explicitly overrides ``thinking`` (the summary).
            payload["chat_template_kwargs"] = {
                "enable_thinking": (
                    config.enable_thinking if thinking is None else thinking
                )
            }
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def _record_usage_error(
        self,
        config: LlmConfig,
        model: str,
        purpose: str,
        session_id: str | None,
        attempts: int,
        error: str,
    ) -> None:
        """Record a failed call (no token counts) in the usage table."""
        await record_llm_usage(
            provider=config.provider,
            base_url=config.base_url,
            model=model,
            purpose=purpose,
            session_id=session_id,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            latency_ms=None,
            attempts=attempts,
            status="error",
            error=error,
        )

    async def chat_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        temperature: float | None = None,
        model: str | None = None,
        *,
        max_tokens: int | None = None,
        thinking: bool | None = None,
        read_timeout: float | None = None,
        attempts: int | None = None,
        purpose: str = "chat",
        session_id: str | None = None,
    ) -> dict:
        """Send a request to the active provider and return the JSON response.

        ``model`` overrides the configured model for this call (used for the
        summary model). ``tool_choice``, ``temperature`` and ``max_tokens`` are
        forwarded when set (``None`` leaves the provider default). Retries
        transport/timeout
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

        # Literal/hostname guard only: no DNS on the request path. The resolving
        # check runs when a provider is saved or probed (see the settings API).
        try:
            validate_provider_url(config.base_url, resolve=False)
        except ValueError as exc:
            raise LlmError(str(exc)) from exc

        active_model = model or config.model
        payload = self._build_payload(
            config,
            messages,
            model=model,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
        )

        prompt_chars = sum(
            len(str(message.get("content") or "")) for message in messages
        )
        url = f"{config.base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        max_attempts = max(1, attempts or (config.max_retries + 1))
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
                    timeout=self._timeout(config, read_timeout),
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
                if not isinstance(data, dict):
                    raise LlmError("LLM returned a non-object JSON body")
                usage = data.get("usage")
                if not isinstance(usage, dict):
                    usage = {}
                prompt_tokens = _token_count(usage.get("prompt_tokens"))
                completion_tokens = _token_count(usage.get("completion_tokens"))
                total_tokens = _token_count(usage.get("total_tokens"))
                log_stage(
                    "llm_call",
                    elapsed,
                    model=active_model,
                    attempt=f"{attempt}/{max_attempts}",
                    messages=len(messages),
                    prompt_chars=prompt_chars,
                    approx_prompt_tokens=prompt_chars // 4,
                    tools=len(tools) if tools else 0,
                    prompt_tokens=prompt_tokens,
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
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
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

    @staticmethod
    def _parse_non_sse_body(plain_lines: list[str]) -> dict | None:
        """Parse a gateway body that ignored ``stream: true`` (a JSON response)."""
        raw = "".join(plain_lines).strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict) and data.get("choices"):
            return data
        return None

    async def _log_and_record_success(
        self,
        config: LlmConfig,
        active_model: str,
        purpose: str,
        session_id: str | None,
        *,
        attempt: int,
        max_attempts: int,
        elapsed: float,
        prompt_chars: int,
        message_count: int,
        tool_count: int,
        usage: dict,
    ) -> None:
        """Log the timing and record a successful streamed call's usage."""
        prompt_tokens = _token_count(usage.get("prompt_tokens"))
        completion_tokens = _token_count(usage.get("completion_tokens"))
        total_tokens = _token_count(usage.get("total_tokens"))
        log_stage(
            "llm_call",
            elapsed,
            model=active_model,
            attempt=f"{attempt}/{max_attempts}",
            messages=message_count,
            prompt_chars=prompt_chars,
            approx_prompt_tokens=prompt_chars // 4,
            tools=tool_count,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            out_tok_per_s=(
                round(completion_tokens / elapsed, 1)
                if completion_tokens and elapsed > 0
                else None
            ),
            stream=True,
        )
        await record_llm_usage(
            provider=config.provider,
            base_url=config.base_url,
            model=active_model,
            purpose=purpose,
            session_id=session_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=int(elapsed * 1000),
            attempts=attempt,
            status="success",
        )

    async def stream_chat_completion(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        thinking: bool | None = None,
        read_timeout: float | None = None,
        attempts: int | None = None,
        purpose: str = "chat",
        session_id: str | None = None,
        on_content: Callable[[str], Awaitable[None]] | None = None,
        on_tool_delta: Callable[[str | None, str], Awaitable[bool | None]] | None = None,
    ) -> dict:
        """Stream a Chat Completions request, forwarding incremental deltas.

        Emits content fragments via ``on_content`` and per-tool-call
        ``(name_so_far, arguments_so_far)`` via ``on_tool_delta`` as SSE chunks
        arrive, and returns a response shaped like :meth:`chat_completion`
        (``{"choices": [{"message": {...}}], "usage": {...}}``). ``on_tool_delta``
        returns truthy when it forwarded user-visible text. Retries only before
        the first *forwarded* delta: once output has reached the user a retry
        would duplicate it, so the error is recorded and raised instead (a
        streamed ``agent_plan`` that never surfaces text can still retry). A
        gateway that ignores ``stream: true`` and returns a single JSON body is
        handled transparently. ``read_timeout`` applies per chunk while
        streaming, so the wall-clock bound rests on ``max_tokens``.
        """
        config = self._resolve_config()
        if not config.configured:
            raise LlmNotConfigured(NOT_CONFIGURED_MESSAGE)

        try:
            validate_provider_url(config.base_url, resolve=False)
        except ValueError as exc:
            raise LlmError(str(exc)) from exc

        active_model = model or config.model
        payload = self._build_payload(
            config,
            messages,
            model=model,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            thinking=thinking,
        )
        prompt_chars = sum(
            len(str(message.get("content") or "")) for message in messages
        )
        url = f"{config.base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        max_attempts = max(1, attempts or (config.max_retries + 1))

        emitted = False
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
                f"📡 LLM stream request: model={active_model}, "
                f"attempt={attempt}/{max_attempts}, budget_used={budget['used']}"
            )
            content_parts: list[str] = []
            tool_calls: dict[int, dict] = {}
            usage: dict = {}
            parsed_sse = False
            plain_lines: list[str] = []
            try:
                async with client.stream(
                    "POST",
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self._timeout(config, read_timeout),
                ) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", "replace")[:500]
                        status = response.status_code
                        if (
                            status in _RETRYABLE_STATUSES
                            and attempt < max_attempts
                            and not emitted
                        ):
                            delay = _retry_delay(response, attempt)
                            logger.warning(
                                f"⚠️ LLM stream HTTP error {status} on attempt "
                                f"{attempt}/{max_attempts}; retrying in {delay:.1f}s"
                            )
                            await asyncio.sleep(delay)
                            continue
                        logger.error(f"❌ LLM stream HTTP error {status}: {body}")
                        await self._record_usage_error(
                            config,
                            active_model,
                            purpose,
                            session_id,
                            attempt,
                            f"LLM API error {status}",
                        )
                        raise LlmError(f"LLM API error {status}")

                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            plain_lines.append(line)
                            continue
                        parsed_sse = True
                        data_str = line[5:].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(chunk, dict):
                            continue
                        if chunk.get("error"):
                            error = f"LLM stream error: {chunk['error']}"
                            await self._record_usage_error(
                                config,
                                active_model,
                                purpose,
                                session_id,
                                attempt,
                                error,
                            )
                            raise LlmError(error)
                        chunk_usage = chunk.get("usage")
                        if isinstance(chunk_usage, dict) and chunk_usage:
                            usage = chunk_usage
                        choices = chunk.get("choices") or []
                        if not choices or not isinstance(choices[0], dict):
                            continue
                        delta = choices[0].get("delta") or {}
                        fragment = delta.get("content")
                        if fragment:
                            content_parts.append(fragment)
                            if on_content is not None:
                                await on_content(fragment)
                                emitted = True
                        for tool_delta in delta.get("tool_calls") or []:
                            if not isinstance(tool_delta, dict):
                                continue
                            index = tool_delta.get("index") or 0
                            slot = tool_calls.setdefault(
                                index,
                                {
                                    "id": None,
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                },
                            )
                            if tool_delta.get("id"):
                                slot["id"] = tool_delta["id"]
                            function = tool_delta.get("function") or {}
                            if function.get("name"):
                                slot["function"]["name"] += function["name"]
                            if function.get("arguments"):
                                slot["function"]["arguments"] += function["arguments"]
                            if on_tool_delta is not None and await on_tool_delta(
                                slot["function"]["name"] or None,
                                slot["function"]["arguments"],
                            ):
                                emitted = True
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                elapsed = time.monotonic() - started
                logger.warning(
                    f"⚠️ LLM stream transport error on attempt "
                    f"{attempt}/{max_attempts} after {elapsed:.1f}s: "
                    f"{type(exc).__name__}: {exc}"
                )
                if emitted:
                    error = (
                        "LLM stream failed after partial output: "
                        f"{type(exc).__name__}"
                    )
                    await self._record_usage_error(
                        config, active_model, purpose, session_id, attempt, error
                    )
                    raise LlmError(error) from exc
                if attempt < max_attempts:
                    await asyncio.sleep(min(2 ** (attempt - 1), 5))
                continue

            elapsed = time.monotonic() - started
            if not parsed_sse:
                result = self._parse_non_sse_body(plain_lines)
                if result is None:
                    error = "LLM stream returned no SSE data or JSON body"
                    await self._record_usage_error(
                        config, active_model, purpose, session_id, attempt, error
                    )
                    raise LlmError(error)
            else:
                message: dict = {
                    "role": "assistant",
                    "content": "".join(content_parts),
                }
                if tool_calls:
                    message["tool_calls"] = [
                        tool_calls[i] for i in sorted(tool_calls)
                    ]
                usage = usage if isinstance(usage, dict) else {}
                result = {"choices": [{"message": message}], "usage": usage}

            await self._log_and_record_success(
                config,
                active_model,
                purpose,
                session_id,
                attempt=attempt,
                max_attempts=max_attempts,
                elapsed=elapsed,
                prompt_chars=prompt_chars,
                message_count=len(messages),
                tool_count=len(tools) if tools else 0,
                usage=result.get("usage") or {},
            )
            return result

        if isinstance(last_exc, httpx.TimeoutException):
            await self._record_usage_error(
                config,
                active_model,
                purpose,
                session_id,
                max_attempts,
                f"LLM request timed out: {type(last_exc).__name__}",
            )
            raise LlmTimeoutError(
                f"LLM did not respond within {config.read_timeout:.0f}s"
            ) from last_exc
        await self._record_usage_error(
            config,
            active_model,
            purpose,
            session_id,
            max_attempts,
            f"LLM request failed: {last_exc}",
        )
        raise LlmError(f"LLM request failed: {last_exc}") from last_exc
