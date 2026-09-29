"""Unit tests for long-conversation memory compaction."""

from types import SimpleNamespace

import pytest

from src.core import memory as memory_module
from src.core.memory import (
    build_memory_messages,
    load_memory,
    refresh_memory,
    select_foldable,
)
from src.llm.config import LlmConfig
from src.llm.prompt import MEMORY_INSTRUCTION


def _row(role: str, content: str, created_at: str) -> SimpleNamespace:
    return SimpleNamespace(role=role, content=content, created_at=created_at)


def _config(**overrides) -> LlmConfig:
    base = {
        "provider": "custom",
        "base_url": "https://llm.test/v1",
        "model": "test-model",
        "summary_model": "small-model",
        "summary_disable_thinking": True,
    }
    base.update(overrides)
    return LlmConfig(**base)


class _FakeClient:
    """Records compaction calls and returns a canned brief."""

    def __init__(self, text: str = "brief", error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls: list[dict] = []

    async def chat_completion(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self.error is not None:
            raise self.error
        return {"choices": [{"message": {"content": self.text}}]}


def _patch_db(monkeypatch, *, memory=None, covered_at=None, rows=()):
    """Patch the DB helpers memory.py uses; return the recorded store."""
    stored: dict = {"memory": memory, "updated_at": covered_at, "sets": []}

    async def fake_get_memory(db, session_id):
        return (stored["memory"], stored["updated_at"])

    async def fake_history(db, session_id, limit=50):
        return list(rows)

    async def fake_set_memory(db, session_id, text, updated_at):
        stored["memory"] = text
        stored["updated_at"] = updated_at
        stored["sets"].append((text, updated_at))

    monkeypatch.setattr(memory_module.db_session, "get_memory", fake_get_memory)
    monkeypatch.setattr(memory_module.db_session, "get_session_history", fake_history)
    monkeypatch.setattr(memory_module.db_session, "set_memory", fake_set_memory)
    return stored


def test_select_foldable_keeps_the_tail_and_folds_the_rest():
    history = [
        {"role": "user", "content": "a" * 100},
        {"role": "assistant", "content": "b" * 100},
        {"role": "user", "content": "c" * 100},
    ]

    folded, kept = select_foldable(history, keep_chars=150)

    assert folded == history[:2]
    assert kept == history[2:]


def test_select_foldable_keeps_everything_when_it_fits():
    history = [{"role": "user", "content": "small"}]

    folded, kept = select_foldable(history, keep_chars=6000)

    assert folded == []
    assert kept == history


def test_select_foldable_handles_an_empty_history():
    assert select_foldable([], keep_chars=6000) == ([], [])


def test_select_foldable_never_splits_the_newest_message():
    history = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "x" * 9000}]

    folded, kept = select_foldable(history, keep_chars=100)

    assert folded == history[:1]
    assert kept == history[1:]


@pytest.mark.asyncio
async def test_refresh_memory_folds_the_older_history_once(monkeypatch):
    rows = [
        _row("user", "u" * 5000, "2026-09-29T10:00:00+00:00"),
        _row("assistant", "a" * 5000, "2026-09-29T10:01:00+00:00"),
        _row("user", "n" * 5000, "2026-09-29T10:02:00+00:00"),
    ]
    stored = _patch_db(monkeypatch, rows=rows)
    client = _FakeClient(text="  brief text  ")

    result = await refresh_memory(object(), "sess", config=_config(), client=client)

    assert result == "brief text"
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["purpose"] == "memory"
    assert call["model"] == "small-model"
    assert call["thinking"] is False
    assert call["session_id"] == "sess"
    # Two oldest messages folded, coverage marker = the newest folded one.
    assert stored["sets"] == [("brief text", "2026-09-29T10:01:00+00:00")]


@pytest.mark.asyncio
async def test_refresh_memory_does_not_fold_the_same_messages_twice(monkeypatch):
    rows = [
        _row("user", "u" * 5000, "2026-09-29T10:00:00+00:00"),
        _row("assistant", "a" * 5000, "2026-09-29T10:01:00+00:00"),
        _row("user", "n" * 5000, "2026-09-29T10:02:00+00:00"),
    ]
    stored = _patch_db(
        monkeypatch,
        memory="existing brief",
        covered_at="2026-09-29T10:01:00+00:00",
        rows=rows,
    )
    client = _FakeClient()

    result = await refresh_memory(object(), "sess", config=_config(), client=client)

    assert result == "existing brief"
    assert client.calls == []
    assert stored["sets"] == []


@pytest.mark.asyncio
async def test_refresh_memory_skips_when_there_is_nothing_to_fold(monkeypatch):
    rows = [_row("user", "short", "2026-09-29T10:00:00+00:00")]
    stored = _patch_db(monkeypatch, rows=rows)
    client = _FakeClient()

    result = await refresh_memory(object(), "sess", config=_config(), client=client)

    assert result is None
    assert client.calls == []
    assert stored["sets"] == []


@pytest.mark.asyncio
async def test_refresh_memory_keeps_the_stored_memory_when_the_model_fails(monkeypatch):
    rows = [
        _row("user", "u" * 7000, "2026-09-29T10:00:00+00:00"),
        _row("assistant", "a" * 7000, "2026-09-29T10:01:00+00:00"),
    ]
    stored = _patch_db(
        monkeypatch, memory="old brief", covered_at=None, rows=rows
    )
    client = _FakeClient(error=RuntimeError("provider down"))

    result = await refresh_memory(object(), "sess", config=_config(), client=client)

    assert result == "old brief"
    assert stored["sets"] == []
    assert stored["memory"] == "old brief"


@pytest.mark.asyncio
async def test_refresh_memory_keeps_the_stored_memory_on_an_empty_result(monkeypatch):
    rows = [
        _row("user", "u" * 7000, "2026-09-29T10:00:00+00:00"),
        _row("assistant", "a" * 7000, "2026-09-29T10:01:00+00:00"),
    ]
    stored = _patch_db(monkeypatch, rows=rows)
    client = _FakeClient(text="   ")

    result = await refresh_memory(object(), "sess", config=_config(), client=client)

    assert result is None
    assert stored["sets"] == []


@pytest.mark.asyncio
async def test_refresh_memory_bounds_the_call_when_a_timeout_is_set(monkeypatch):
    rows = [
        _row("user", "u" * 7000, "2026-09-29T10:00:00+00:00"),
        _row("assistant", "a" * 7000, "2026-09-29T10:01:00+00:00"),
    ]
    _patch_db(monkeypatch, rows=rows)
    client = _FakeClient()
    config = _config(summary_timeout=7.0)

    await refresh_memory(object(), "sess", config=config, client=client)

    assert client.calls[0]["read_timeout"] == 7.0
    assert client.calls[0]["attempts"] == 1


@pytest.mark.asyncio
async def test_load_memory_returns_none_for_a_fresh_session(monkeypatch):
    _patch_db(monkeypatch)

    assert await load_memory(object(), "sess") is None


@pytest.mark.asyncio
async def test_load_memory_returns_the_stored_text(monkeypatch):
    _patch_db(monkeypatch, memory="  fact 1; fact 2  ")

    assert await load_memory(object(), "sess") == "fact 1; fact 2"


def test_build_memory_messages_merges_the_brief_and_the_transcript():
    messages = build_memory_messages(
        "Earlier: the user prefers UTC.",
        [{"role": "user", "content": "check the disks"}],
    )

    assert messages[0]["role"] == "system"
    assert MEMORY_INSTRUCTION in messages[0]["content"]
    body = messages[1]["content"]
    assert "Existing brief:" in body
    assert "Earlier: the user prefers UTC." in body
    assert "New messages to fold in:" in body
    assert "User: check the disks" in body


def test_build_memory_messages_without_an_existing_brief():
    messages = build_memory_messages(None, [{"role": "assistant", "content": "done"}])

    assert "Existing brief:" not in messages[1]["content"]
    assert "Assistant: done" in messages[1]["content"]
