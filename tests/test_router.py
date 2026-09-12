"""Unit tests for the AIRouter without DB or network."""

import pytest

from src.llm import router as router_module
from src.llm.router import AIRouter


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

    async def fake_add_user(*args, **kwargs):
        return None

    async def fake_add_assistant(*args, **kwargs):
        return None

    monkeypatch.setattr(router_module, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(router_module, "ensure_session", fake_ensure)
    monkeypatch.setattr(router_module.db_session, "get_session_history", fake_history)
    monkeypatch.setattr(router_module.db_session, "add_user_message", fake_add_user)
    monkeypatch.setattr(router_module.db_session, "add_assistant_message", fake_add_assistant)

    router = AIRouter(plugin_manager=None)
    state: dict = {"response": {"choices": []}}

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
                                "name": "wg_status",
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
    assert result["tool_name"] == "wg_status"
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
                        {"function": {"name": "wg_status", "arguments": "not-json"}}
                    ]
                }
            }
        ]
    }

    result = await router.route("status", "sess-1")

    assert result["type"] == "tool_call"
    assert result["tool_args"] == {"_raw": "not-json"}
