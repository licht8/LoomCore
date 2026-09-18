"""Unit tests for the LLM client retry/timeout behavior."""

import json

import httpx
import pytest

from src.config import settings
from src.llm import budget
from src.llm import client as client_module
from src.llm.client import HetznerClient, LlmError, LlmTimeoutError
from src.llm.exceptions import LlmBudgetExceeded


class _FakeResponse:
    status_code = 200
    content = b"{}"
    text = "{}"

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}


class _ErrorResponse:
    status_code = 500
    content = b"oops"
    text = "server error"

    def raise_for_status(self):
        raise httpx.HTTPStatusError(
            "server error",
            request=httpx.Request("POST", "http://llm.test"),
            response=self,
        )

    def json(self):
        return {}


class _BadJsonResponse:
    status_code = 200
    content = b"<html>not json</html>"
    text = "<html>not json</html>"

    def raise_for_status(self):
        return None

    def json(self):
        raise json.JSONDecodeError("Expecting value", "<html>", 0)


class _FakeClient:
    """Async context manager whose post() returns/raises scripted outcomes."""

    def __init__(self, outcomes):
        self._outcomes = outcomes
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, *args, **kwargs):
        outcome = self._outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _install(monkeypatch, outcomes):
    fake = _FakeClient(outcomes)
    monkeypatch.setattr(client_module.httpx, "AsyncClient", lambda **kwargs: fake)
    return fake


@pytest.mark.asyncio
async def test_timeout_raises_after_retries(monkeypatch):
    monkeypatch.setattr(settings, "hetzner_max_retries", 0)
    monkeypatch.setattr(settings, "hetzner_read_timeout", 300.0)
    fake = _install(monkeypatch, [httpx.ReadTimeout("t")])

    with pytest.raises(LlmTimeoutError):
        await HetznerClient(api_key="k").chat_completion(
            [{"role": "user", "content": "hi"}]
        )

    assert fake.calls == 1


@pytest.mark.asyncio
async def test_retries_transport_error_then_succeeds(monkeypatch):
    monkeypatch.setattr(settings, "hetzner_max_retries", 1)
    fake = _install(monkeypatch, [httpx.ConnectError("boom"), _FakeResponse()])

    result = await HetznerClient(api_key="k").chat_completion(
        [{"role": "user", "content": "hi"}]
    )

    assert result["choices"][0]["message"]["content"] == "ok"
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_http_status_error_is_not_retried(monkeypatch):
    monkeypatch.setattr(settings, "hetzner_max_retries", 3)
    fake = _install(monkeypatch, [_ErrorResponse()])

    with pytest.raises(LlmError):
        await HetznerClient(api_key="k").chat_completion(
            [{"role": "user", "content": "hi"}]
        )

    assert fake.calls == 1


@pytest.mark.asyncio
async def test_invalid_json_body_raises_llm_error(monkeypatch):
    monkeypatch.setattr(settings, "hetzner_max_retries", 3)
    fake = _install(monkeypatch, [_BadJsonResponse()])

    with pytest.raises(LlmError, match="invalid JSON"):
        await HetznerClient(api_key="k").chat_completion(
            [{"role": "user", "content": "hi"}]
        )

    assert fake.calls == 1


@pytest.mark.asyncio
async def test_daily_budget_blocks_and_logs_security_event(monkeypatch, caplog):
    monkeypatch.setattr(settings, "hetzner_max_retries", 0)
    monkeypatch.setattr(settings, "llm_daily_call_limit", 1)
    budget.reset_llm_budget()
    try:
        _install(monkeypatch, [_FakeResponse(), _FakeResponse()])
        client = HetznerClient(api_key="k")
        await client.chat_completion([{"role": "user", "content": "hi"}])

        with (
            caplog.at_level("WARNING", logger="loomcore.security"),
            pytest.raises(LlmBudgetExceeded),
        ):
            await client.chat_completion([{"role": "user", "content": "again"}])
    finally:
        budget.reset_llm_budget()

    assert "llm_budget_exceeded" in caplog.text
