"""Unit tests for the AIRouter without DB or network."""

import pytest

from src.llm import router as router_module
from src.llm.prompt import SYSTEM_PROMPT
from src.llm.router import AIRouter


def test_system_prompt_requires_reverification():
    """The assistant must treat live tool output as the only source of truth."""
    lowered = SYSTEM_PROMPT.lower()
    assert "re-run" in lowered or "re run" in lowered or "re-run the relevant read tool" in lowered
    assert "current state" in lowered
    assert "never treat" in lowered


class _FakeSession:
    """Stub for an async SQLAlchemy session."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


@pytest.fixture
def patched_router(monkeypatch):
    """AIRouter with DB stubs; returns (router, state).

    state["response"] replaces the HetznerClient.chat_completion response.
    """

    async def fake_history(*args, **kwargs):
        return []

    async def fake_ensure(*args, **kwargs):
        return "sess"

    state: dict = {"response": {"choices": []}, "user_saved": [], "assistant_saved": []}

    async def fake_add_user(db, session_id, content):
        state["user_saved"].append((session_id, content))

    async def fake_add_assistant(db, session_id, content, tool_calls=None):
        state["assistant_saved"].append((session_id, content, tool_calls))

    monkeypatch.setattr(router_module, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(router_module, "ensure_session", fake_ensure)
    monkeypatch.setattr(router_module.db_session, "get_session_history", fake_history)
    monkeypatch.setattr(router_module.db_session, "add_user_message", fake_add_user)
    monkeypatch.setattr(router_module.db_session, "add_assistant_message", fake_add_assistant)

    router = AIRouter(plugin_manager=None)

    async def fake_chat_completion(messages, tools=None):
        return state["response"]

    router.client.chat_completion = fake_chat_completion  # type: ignore[method-assign]
    return router, state


@pytest.mark.asyncio
async def test_route_text_response(patched_router):
    router, state = patched_router
    state["response"] = {"choices": [{"message": {"content": "hello"}}]}

    result = await router.route("hi", "sess-1")

    assert result["type"] == "text"
    assert result["content"] == "hello"
    assert result["tool_name"] is None


@pytest.mark.asyncio
async def test_route_tool_call(patched_router):
    router, state = patched_router
    state["response"] = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "wg_interface_status",
                                "arguments": '{"interface": "wg0"}',
                            }
                        }
                    ]
                }
            }
        ]
    }

    result = await router.route("status", "sess-1")

    assert result["type"] == "tool_call"
    assert result["tool_name"] == "wg_interface_status"
    assert result["tool_args"] == {"interface": "wg0"}


@pytest.mark.asyncio
async def test_route_no_choices(patched_router):
    router, state = patched_router
    state["response"] = {"choices": []}

    result = await router.route("hi", "sess-1")

    assert result["type"] == "text"
    assert result["content"] == "No response from the LLM"


@pytest.mark.asyncio
async def test_route_broken_tool_arguments(patched_router):
    router, state = patched_router
    state["response"] = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"function": {"name": "wg_interface_status", "arguments": "not-json"}}
                    ]
                }
            }
        ]
    }

    result = await router.route("status", "sess-1")

    assert result["type"] == "tool_call"
    assert result["tool_args"] == {"_raw": "not-json"}


@pytest.mark.asyncio
async def test_route_persists_user_and_assistant_messages(patched_router):
    router, state = patched_router
    state["response"] = {"choices": [{"message": {"content": "reply"}}]}

    await router.route("hello", "sess-9")

    assert state["user_saved"] == [("sess-9", "hello")]
    assert state["assistant_saved"] == [("sess-9", "reply", None)]


@pytest.mark.asyncio
async def test_route_persists_tool_call(patched_router):
    router, state = patched_router
    raw_tool_calls = [
        {"function": {"name": "wg_interface_status", "arguments": "{}"}}
    ]
    state["response"] = {"choices": [{"message": {"tool_calls": raw_tool_calls}}]}

    await router.route("status", "sess-9")

    assert state["assistant_saved"][0][0] == "sess-9"
    assert state["assistant_saved"][0][1] == ""
    assert state["assistant_saved"][0][2] == raw_tool_calls


@pytest.mark.asyncio
async def test_route_warns_when_plugin_tools_unavailable(patched_router):
    router, state = patched_router

    class _BrokenManager:
        async def get_all_tools(self):
            raise RuntimeError("plugin manager down")

    router.plugin_manager = _BrokenManager()  # type: ignore[assignment]
    state["response"] = {"choices": [{"message": {"content": "ok"}}]}

    result = await router.route("hi", "sess-1")

    assert result["tool_schema_warning"] == ["all plugins"]
    assert result["content"] == "ok"


@pytest.mark.asyncio
async def test_route_without_plugin_manager(patched_router):
    router, state = patched_router
    assert router.plugin_manager is None
    state["response"] = {"choices": [{"message": {"content": "ok"}}]}

    result = await router.route("hi", "sess-1")

    assert result["type"] == "text"
    assert "tool_schema_warning" not in result
