import asyncio
import logging
import time

import httpx

from src.config import settings

logger = logging.getLogger("loomcore.llm")


class LlmError(RuntimeError):
    """LLM request failed."""


class LlmTimeoutError(LlmError):
    """LLM did not respond within the configured timeout."""


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
    ) -> dict:
        """Send a request to the LLM and return the JSON response.

        Retries transport/timeout errors up to `HETZNER_MAX_RETRIES` times and
        raises `LlmTimeoutError`/`LlmError` with a clear message on failure.
        """
        payload: dict = {
            "model": self.MODEL,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        max_attempts = max(1, settings.hetzner_max_retries + 1)
        last_exc: Exception | None = None

        async with httpx.AsyncClient(timeout=self._timeout()) as client:
            for attempt in range(1, max_attempts + 1):
                started = time.monotonic()
                logger.info(
                    f"📡 LLM request: model={self.MODEL}, "
                    f"attempt={attempt}/{max_attempts}"
                )
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    elapsed = time.monotonic() - started
                    logger.info(
                        f"✅ LLM response: status={response.status_code}, "
                        f"size={len(response.content)}, elapsed={elapsed:.1f}s"
                    )
                    return response.json()
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
