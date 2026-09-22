"""Unit tests for LLM usage recording and response shaping (no DB)."""

import pytest

from src.llm import usage as usage_module


def test_shape_usage_summary_empty_window():
    result = usage_module.shape_usage_summary(
        days=7,
        since_iso="2026-09-15T00:00:00+00:00",
        until_iso="2026-09-22T00:00:00+00:00",
        totals={
            "calls": 0,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "avg_latency_ms": None,
        },
        by_model=[],
        by_day=[],
        budget={"day": "2026-09-22", "used": 0, "limit": 1000, "remaining": 1000},
    )

    assert result["days"] == 7
    assert result["totals"] == {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "avg_latency_ms": 0,
    }
    assert result["by_model"] == []
    assert result["by_day"] == []
    assert result["budget"]["limit"] == 1000


def test_shape_usage_summary_none_tokens_and_rounding():
    result = usage_module.shape_usage_summary(
        days=1,
        since_iso="a",
        until_iso="b",
        totals={
            "calls": 3,
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "total_tokens": 140,
            "avg_latency_ms": 12.6,
        },
        by_model=[
            {
                "provider": "openai",
                "model": "gpt",
                "calls": 2,
                "prompt_tokens": 80,
                "completion_tokens": 20,
                "total_tokens": 100,
                "avg_latency_ms": 10.4,
            },
            {
                "provider": "groq",
                "model": "llama",
                "calls": 1,
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "avg_latency_ms": None,
            },
        ],
        by_day=[
            {
                "day": "2026-09-22",
                "calls": 3,
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "total_tokens": 140,
            }
        ],
        budget={"day": "2026-09-22", "used": 3, "limit": 1000, "remaining": 997},
    )

    assert result["totals"]["avg_latency_ms"] == 13
    assert result["totals"]["total_tokens"] == 140
    assert result["by_model"][0]["avg_latency_ms"] == 10
    groq = result["by_model"][1]
    assert groq["prompt_tokens"] is None
    assert groq["total_tokens"] is None
    assert groq["avg_latency_ms"] is None
    assert result["by_day"][0]["day"] == "2026-09-22"


@pytest.mark.asyncio
async def test_record_llm_usage_swallows_errors(monkeypatch, caplog):
    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(usage_module, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(usage_module.db_session, "add_llm_usage", boom)

    with caplog.at_level("WARNING", logger="loomcore.llm.usage"):
        await usage_module.record_llm_usage(
            provider="openai",
            base_url="https://x/v1",
            model="m",
            purpose="chat",
            session_id="s1",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            latency_ms=5,
            attempts=1,
            status="success",
        )

    assert "Failed to record LLM usage" in caplog.text
