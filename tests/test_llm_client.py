"""Unit tests for the LLM client retry/timeout behavior."""

import json

import httpx
import pytest

from src.config import settings
from src.llm import budget
from src.llm import client as client_module
from src.llm.client import OpenAICompatClient
from src.llm.config import LlmConfig
from src.llm.exceptions import (
    LlmBudgetExceeded,
    LlmError,
    LlmNotConfigured,
    LlmTimeoutError,
)


class _FakeResponse:
    status_code = 200
    content = b"{}"
    text = "{}"

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}


class _ErrorResponse:
    def __init__(self, status_code=500, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = b"oops"
        self.text = "server error"

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
    """Client whose post() returns/raises scripted outcomes."""

    def __init__(self, outcomes):
        self._outcomes = outcomes
        self.calls = 0
        self.closed = 0
        self.requests = []

    async def post(self, url, **kwargs):
        self.requests.append(
            {
                "url": url,
                "headers": kwargs.get("headers") or {},
                "json": kwargs.get("json"),
            }
        )
        outcome = self._outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def aclose(self):
        self.closed += 1


def _config(**overrides):
    base = {
        "provider": "custom",
        "base_url": "https://llm.test/v1",
        "model": "test-model",
        "api_key": "k",
    }
    base.update(overrides)
    return LlmConfig(**base)


def _install(monkeypatch, outcomes):
    fake = _FakeClient(outcomes)
    monkeypatch.setattr(client_module.httpx, "AsyncClient", lambda **kwargs: fake)
    return fake


def _patch_active(monkeypatch, config):
    monkeypatch.setattr(client_module, "get_active", lambda: config)


def _record_sleeps(monkeypatch):
    sleeps = []

    async def _sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(client_module.asyncio, "sleep", _sleep)
    return sleeps


@pytest.mark.asyncio
async def test_timeout_raises_after_retries(monkeypatch):
    fake = _install(monkeypatch, [httpx.ReadTimeout("t")])

    with pytest.raises(LlmTimeoutError):
        await OpenAICompatClient(
            config=_config(max_retries=0, read_timeout=300.0)
        ).chat_completion([{"role": "user", "content": "hi"}])

    assert fake.calls == 1


@pytest.mark.asyncio
async def test_retries_transport_error_then_succeeds(monkeypatch):
    fake = _install(monkeypatch, [httpx.ConnectError("boom"), _FakeResponse()])

    result = await OpenAICompatClient(config=_config(max_retries=1)).chat_completion(
        [{"role": "user", "content": "hi"}]
    )

    assert result["choices"][0]["message"]["content"] == "ok"
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_non_retryable_status_fails_fast(monkeypatch):
    fake = _install(monkeypatch, [_ErrorResponse(status_code=400)])

    with pytest.raises(LlmError):
        await OpenAICompatClient(config=_config(max_retries=3)).chat_completion(
            [{"role": "user", "content": "hi"}]
        )

    assert fake.calls == 1


@pytest.mark.asyncio
async def test_retries_transient_status_then_succeeds(monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    fake = _install(monkeypatch, [_ErrorResponse(status_code=503), _FakeResponse()])

    result = await OpenAICompatClient(config=_config(max_retries=3)).chat_completion(
        [{"role": "user", "content": "hi"}]
    )

    assert result["choices"][0]["message"]["content"] == "ok"
    assert fake.calls == 2
    assert sleeps == [1]


@pytest.mark.asyncio
async def test_retries_transient_status_then_exhausts(monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    fake = _install(
        monkeypatch,
        [
            _ErrorResponse(status_code=503),
            _ErrorResponse(status_code=503),
            _ErrorResponse(status_code=503),
        ],
    )

    with pytest.raises(LlmError):
        await OpenAICompatClient(config=_config(max_retries=2)).chat_completion(
            [{"role": "user", "content": "hi"}]
        )

    assert fake.calls == 3
    assert sleeps == [1, 2]


@pytest.mark.asyncio
async def test_retry_after_header_sets_delay(monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    fake = _install(
        monkeypatch,
        [
            _ErrorResponse(status_code=429, headers={"retry-after": "0.5"}),
            _FakeResponse(),
        ],
    )

    await OpenAICompatClient(config=_config(max_retries=1)).chat_completion(
        [{"role": "user", "content": "hi"}]
    )

    assert fake.calls == 2
    assert sleeps == [0.5]


@pytest.mark.asyncio
async def test_client_is_reused_across_calls(monkeypatch):
    created = []
    fake = _FakeClient([_FakeResponse(), _FakeResponse()])

    def _factory(**kwargs):
        created.append(kwargs)
        return fake

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _factory)
    client = OpenAICompatClient(config=_config())

    await client.chat_completion([{"role": "user", "content": "hi"}])
    await client.chat_completion([{"role": "user", "content": "hi"}])

    assert len(created) == 1
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_aclose_closes_and_rebuilds_client(monkeypatch):
    created = []
    first = _FakeClient([_FakeResponse()])
    second = _FakeClient([_FakeResponse()])

    def _factory(**kwargs):
        created.append(kwargs)
        return first if len(created) == 1 else second

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _factory)
    client = OpenAICompatClient(config=_config())

    await client.chat_completion([{"role": "user", "content": "hi"}])
    assert client._client is first

    await client.aclose()
    assert first.closed == 1
    assert client._client is None

    await client.chat_completion([{"role": "user", "content": "hi"}])
    assert len(created) == 2
    assert client._client is second


@pytest.mark.asyncio
async def test_invalid_json_body_raises_llm_error(monkeypatch):
    fake = _install(monkeypatch, [_BadJsonResponse()])

    with pytest.raises(LlmError, match="invalid JSON"):
        await OpenAICompatClient(config=_config(max_retries=3)).chat_completion(
            [{"role": "user", "content": "hi"}]
        )

    assert fake.calls == 1


@pytest.mark.asyncio
async def test_daily_budget_blocks_and_logs_security_event(monkeypatch, caplog):
    monkeypatch.setattr(settings, "llm_daily_call_limit", 1)
    budget.reset_llm_budget()
    try:
        _install(monkeypatch, [_FakeResponse(), _FakeResponse()])
        client = OpenAICompatClient(config=_config(max_retries=0))
        await client.chat_completion([{"role": "user", "content": "hi"}])

        with (
            caplog.at_level("WARNING", logger="loomcore.security"),
            pytest.raises(LlmBudgetExceeded),
        ):
            await client.chat_completion([{"role": "user", "content": "again"}])
    finally:
        budget.reset_llm_budget()

    assert "llm_budget_exceeded" in caplog.text


@pytest.mark.asyncio
async def test_request_uses_active_config(monkeypatch):
    fake = _install(monkeypatch, [_FakeResponse()])
    _patch_active(
        monkeypatch,
        _config(
            base_url="https://api.example.com/v1",
            model="m1",
            api_key="secret",
        ),
    )

    await OpenAICompatClient().chat_completion([{"role": "user", "content": "hi"}])

    request = fake.requests[0]
    assert request["url"] == "https://api.example.com/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer secret"
    assert request["json"]["model"] == "m1"


@pytest.mark.asyncio
async def test_active_config_switch_changes_payload(monkeypatch):
    fake = _install(monkeypatch, [_FakeResponse(), _FakeResponse()])
    holder = {"config": _config()}
    monkeypatch.setattr(client_module, "get_active", lambda: holder["config"])

    client = OpenAICompatClient()
    await client.chat_completion([{"role": "user", "content": "hi"}])
    holder["config"] = _config(
        base_url="https://second.test/v1", model="m2", api_key="other"
    )
    await client.chat_completion([{"role": "user", "content": "hi"}])

    assert fake.requests[0]["url"] == "https://llm.test/v1/chat/completions"
    assert fake.requests[0]["json"]["model"] == "test-model"
    assert fake.requests[1]["url"] == "https://second.test/v1/chat/completions"
    assert fake.requests[1]["json"]["model"] == "m2"
    assert fake.requests[1]["headers"]["Authorization"] == "Bearer other"


@pytest.mark.asyncio
async def test_model_argument_overrides_config(monkeypatch):
    fake = _install(monkeypatch, [_FakeResponse()])

    await OpenAICompatClient(config=_config(model="base-model")).chat_completion(
        [{"role": "user", "content": "hi"}], model="summary-model"
    )

    assert fake.requests[0]["json"]["model"] == "summary-model"


@pytest.mark.asyncio
async def test_thinking_template_is_opt_in(monkeypatch):
    fake = _install(monkeypatch, [_FakeResponse(), _FakeResponse()])

    await OpenAICompatClient(
        config=_config(thinking_template=False, enable_thinking=True)
    ).chat_completion([{"role": "user", "content": "hi"}])
    await OpenAICompatClient(
        config=_config(thinking_template=True, enable_thinking=True)
    ).chat_completion([{"role": "user", "content": "hi"}])

    assert "chat_template_kwargs" not in fake.requests[0]["json"]
    assert fake.requests[1]["json"]["chat_template_kwargs"] == {
        "enable_thinking": True
    }


@pytest.mark.asyncio
async def test_unconfigured_raises_not_configured(monkeypatch):
    created = []

    def _factory(**kwargs):
        created.append(kwargs)
        return _FakeClient([_FakeResponse()])

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _factory)

    with pytest.raises(LlmNotConfigured):
        await OpenAICompatClient(
            config=_config(base_url="", model="")
        ).chat_completion([{"role": "user", "content": "hi"}])

    assert created == []
