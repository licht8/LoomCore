"""Unit tests for the AIRouter planner without DB or network."""

import json

import pytest

from src.core.agent import PLANNER_TOOLS, REPLY_TOOL
from src.llm import router as router_module
from src.llm.config import LlmConfig
from src.llm.router import AIRouter

_TEST_CONFIG = LlmConfig(
    provider="custom",
    base_url="https://llm.test/v1",
    model="test-model",
)


class _FakeSession:
    """Stub for an async SQLAlchemy session."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _plan_response(args: dict) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "agent_plan",
                                "arguments": json.dumps(args),
                            }
                        }
                    ]
                }
            }
        ]
    }


def _text_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def _reply_response(text: str) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": REPLY_TOOL,
                                "arguments": json.dumps({"text": text}),
                            }
                        }
                    ]
                }
            }
        ]
    }


@pytest.fixture
def patched_router(monkeypatch):
    """AIRouter with DB stubs; returns (router, state)."""

    state: dict = {
        "queue": [],
        "calls": 0,
        "tools": None,
        "tool_choice": None,
        "temperature": None,
        "last_messages": None,
        "user_saved": [],
        "assistant_saved": [],
        "plans_saved": [],
        "history": [],
    }

    async def fake_history(*args, **kwargs):
        return state["history"]

    async def fake_ensure(*args, **kwargs):
        return "sess"

    async def fake_add_user(db, session_id, content):
        state["user_saved"].append((session_id, content))

    async def fake_add_assistant(db, session_id, content, tool_calls=None):
        state["assistant_saved"].append((session_id, content, tool_calls))

    async def fake_add_plan(db, session_id, plan):
        state["plans_saved"].append((session_id, plan))

    monkeypatch.setattr(router_module, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(router_module, "ensure_session", fake_ensure)
    monkeypatch.setattr(router_module, "get_active", lambda: _TEST_CONFIG)
    monkeypatch.setattr(router_module.db_session, "get_session_history", fake_history)
    monkeypatch.setattr(router_module.db_session, "add_user_message", fake_add_user)
    monkeypatch.setattr(router_module.db_session, "add_assistant_message", fake_add_assistant)
    monkeypatch.setattr(router_module.db_session, "add_plan_message", fake_add_plan)

    router = AIRouter(plugin_manager=None)

    async def fake_chat_completion(
        messages, tools=None, tool_choice=None, temperature=None, model=None
    ):
        state["calls"] += 1
        state["tools"] = tools
        state["tool_choice"] = tool_choice
        state["temperature"] = temperature
        state["last_messages"] = messages
        if state["queue"]:
            return state["queue"].pop(0)
        return {"choices": []}

    router.client.chat_completion = fake_chat_completion  # type: ignore[method-assign]
    return router, state


@pytest.mark.asyncio
async def test_route_text_response(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("hello")]

    result = await router.route("hi", "sess-1")

    assert result["type"] == "text"
    assert result["content"] == "hello"
    assert state["assistant_saved"] == [("sess-1", "hello", None)]


@pytest.mark.asyncio
async def test_route_unconfigured_persists_turn(patched_router, monkeypatch):
    router, state = patched_router
    monkeypatch.setattr(router_module, "get_active", LlmConfig)

    result = await router.route("hi", "sess-1")

    assert result == {
        "type": "text",
        "content": router_module.NOT_CONFIGURED_MESSAGE,
    }
    assert state["user_saved"] == [("sess-1", "hi")]
    assert state["assistant_saved"] == [
        ("sess-1", router_module.NOT_CONFIGURED_MESSAGE, None)
    ]
    assert state["calls"] == 0


@pytest.mark.asyncio
async def test_route_plan_response(patched_router):
    router, state = patched_router
    state["queue"] = [
        _plan_response(
            {
                "goal": "inspect",
                "steps": [
                    {"tool": "core_health", "why": "baseline"},
                    {"tool": "core_list_plugins", "why": "inventory"},
                ],
            }
        )
    ]

    result = await router.route("check the host", "sess-1")

    assert result["type"] == "plan"
    plan = result["plan"]
    assert plan["goal"] == "inspect"
    assert plan["steps"][0]["tool"] == "core_health"
    assert plan["steps"][0]["risk"] == "safe"
    assert plan["status"] == "proposed"
    assert state["plans_saved"][0][1]["goal"] == "inspect"
    assert state["assistant_saved"] == []


@pytest.mark.asyncio
async def test_route_forces_a_planner_tool_call(patched_router):
    router, state = patched_router
    state["queue"] = [_reply_response("ok")]

    await router.route("hi", "sess-1")

    assert state["tools"] == PLANNER_TOOLS
    assert {tool["function"]["name"] for tool in state["tools"]} == {
        "agent_plan",
        "reply",
    }
    assert state["tool_choice"] == "required"
    assert state["temperature"] == 0


@pytest.mark.asyncio
async def test_route_reply_tool_returns_text(patched_router):
    router, state = patched_router
    state["queue"] = [_reply_response("Hi there!")]

    result = await router.route("hi", "sess-1")

    assert result["type"] == "text"
    assert result["content"] == "Hi there!"
    assert state["assistant_saved"][0][1] == "Hi there!"
    assert state["plans_saved"] == []


@pytest.mark.asyncio
async def test_route_reply_strips_tool_name_prefix(patched_router):
    router, state = patched_router
    state["queue"] = [_reply_response("reply: Понял.")]

    result = await router.route("привет", "sess-1")

    assert result["type"] == "text"
    assert result["content"] == "Понял."
    assert state["assistant_saved"][0][1] == "Понял."


@pytest.mark.asyncio
async def test_route_text_fallback_strips_tool_name_prefix(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("agent_plan - just chatting")]

    result = await router.route("hi", "sess-1")

    assert result["type"] == "text"
    assert result["content"] == "just chatting"
    assert state["assistant_saved"][0][1] == "just chatting"


def test_strip_tool_prefix_variants():
    strip = router_module._strip_tool_prefix

    assert strip("reply: hello") == "hello"
    assert strip("reply:hello") == "hello"
    assert strip("Reply - hello") == "hello"
    assert strip("agent_plan — hello") == "hello"
    assert strip("reply: reply: hello") == "hello"
    assert strip("hello") == "hello"
    assert strip("  spaced  ") == "spaced"


@pytest.mark.asyncio
async def test_route_includes_tool_catalog_in_system_prompt(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("ok")]

    await router.route("hi", "sess-1")

    system = state["last_messages"][0]["content"]
    assert "core_health" in system
    assert "Tool catalog" in system


@pytest.mark.asyncio
async def test_route_no_choices(patched_router):
    router, state = patched_router

    result = await router.route("hi", "sess-1")

    assert result["type"] == "text"
    assert result["content"] == "No response from the LLM"


@pytest.mark.asyncio
async def test_route_reasks_after_invalid_plan(patched_router):
    router, state = patched_router
    state["queue"] = [
        _plan_response({"goal": "bad", "steps": [{"tool": "ghost", "why": "x"}]}),
        _plan_response(
            {"goal": "good", "steps": [{"tool": "core_health", "why": "x"}]}
        ),
    ]

    result = await router.route("hi", "sess-1")

    assert result["type"] == "plan"
    assert result["plan"]["goal"] == "good"
    assert state["calls"] == 2


@pytest.mark.asyncio
async def test_route_reasks_when_required_argument_missing(patched_router):
    router, state = patched_router
    state["queue"] = [
        _plan_response(
            {
                "goal": "disable",
                "steps": [{"tool": "core_disable_plugin", "args": {}, "why": "stop"}],
            }
        ),
        _plan_response(
            {
                "goal": "disable",
                "steps": [
                    {
                        "tool": "core_disable_plugin",
                        "args": {"plugin": "LoomWG"},
                        "why": "stop",
                    }
                ],
            }
        ),
    ]

    result = await router.route("disable loomwg", "sess-1")

    assert result["type"] == "plan"
    assert result["plan"]["steps"][0]["args"] == {"plugin": "LoomWG"}
    assert state["calls"] == 2
    assert "plugin" in state["last_messages"][-1]["content"]


@pytest.mark.asyncio
async def test_route_gives_up_after_reask_limit(patched_router):
    router, state = patched_router
    bad = _plan_response({"goal": "bad", "steps": [{"tool": "ghost", "why": "x"}]})
    state["queue"] = [bad, bad]

    result = await router.route("hi", "sess-1")

    assert result["type"] == "text"
    assert "could not form a valid plan" in result["content"]
    assert state["calls"] == 2


@pytest.mark.asyncio
async def test_route_rejects_unexpected_tool(patched_router):
    router, state = patched_router
    unexpected = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"function": {"name": "wg_interface_status", "arguments": "{}"}}
                    ]
                }
            }
        ]
    }
    state["queue"] = [
        unexpected,
        _plan_response({"goal": "ok", "steps": [{"tool": "core_health", "why": "x"}]}),
    ]

    result = await router.route("hi", "sess-1")

    assert result["type"] == "plan"
    assert state["calls"] == 2


@pytest.mark.asyncio
async def test_route_warns_when_plugin_tools_unavailable(patched_router):
    router, state = patched_router

    class _BrokenManager:
        async def get_all_tools(self):
            raise RuntimeError("plugin manager down")

    router.plugin_manager = _BrokenManager()  # type: ignore[assignment]
    state["queue"] = [_text_response("ok")]

    result = await router.route("hi", "sess-1")

    assert result["tool_schema_warning"] == ["all plugins"]
    assert result["content"] == "ok"


@pytest.mark.asyncio
async def test_route_includes_plugin_tools_in_catalog(patched_router):
    router, state = patched_router

    class _Manager:
        async def get_all_tools(self):
            return [
                {
                    "name": "wg_interface_status",
                    "description": "status",
                    "inputSchema": {"type": "object", "properties": {"interface": {}}},
                }
            ]

    router.plugin_manager = _Manager()  # type: ignore[assignment]
    state["queue"] = [_text_response("ok")]

    await router.route("hi", "sess-1")

    assert "wg_interface_status" in state["last_messages"][0]["content"]


@pytest.mark.asyncio
async def test_summarize_plan_uses_soul(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("all good")]

    summary = await router.summarize_plan(
        "check disk",
        {"goal": "check disk", "steps": [{"id": "s1", "tool": "ops_disk_usage"}]},
        [{"step": {"id": "s1"}, "result": {"content": [], "data": {}}}],
    )

    assert summary == "all good"
    assert "Loom" in state["last_messages"][0]["content"]
    assert state["tools"] is None


@pytest.mark.asyncio
async def test_summarize_plan_handles_empty_choices(patched_router):
    router, state = patched_router
    state["queue"] = [{"choices": []}]

    summary = await router.summarize_plan(
        "check disk",
        {"goal": "check disk", "steps": [{"id": "s1", "tool": "ops_disk_usage"}]},
        [{"step": {"id": "s1"}, "result": {"content": [], "data": {}}}],
    )

    assert summary == ""


class _HistoryMessage:
    """Stub ORM message with the attributes the router reads."""

    def __init__(self, role, content, tool_calls=None):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls


@pytest.mark.asyncio
async def test_route_never_sends_internal_roles(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("ok")]
    state["history"] = [
        _HistoryMessage("user", "hi"),
        _HistoryMessage("assistant", "hello"),
        _HistoryMessage("plan", "do stuff", tool_calls=[{"goal": "inspect"}]),
        _HistoryMessage(
            "tool",
            "disk 42%",
            tool_calls=[{"name": "ops_disk_usage"}],
        ),
        _HistoryMessage("system", "ignored"),
        _HistoryMessage("assistant", ""),
        _HistoryMessage("user", ""),
    ]

    await router.route("next", "sess-1")

    messages = state["last_messages"]
    assert {m["role"] for m in messages} <= {"system", "user", "assistant"}
    assert all(m["role"] not in ("tool", "plan") for m in messages)


@pytest.mark.asyncio
async def test_route_drops_tool_messages_preserving_order(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("ok")]
    state["history"] = [
        _HistoryMessage("user", "hi"),
        _HistoryMessage(
            "tool",
            "disk 42%",
            tool_calls=[{"name": "ops_disk_usage"}],
        ),
        _HistoryMessage("assistant", "done"),
    ]

    await router.route("next", "sess-1")

    roles = [m["role"] for m in state["last_messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert state["last_messages"][2]["content"] == "done"
    assert state["last_messages"][-1]["content"] == "next"
    assert not any(
        "disk 42%" in str(m["content"]) for m in state["last_messages"][1:]
    )


@pytest.mark.asyncio
async def test_route_drops_tool_messages_without_name(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("ok")]
    state["history"] = [
        _HistoryMessage("tool", "", tool_calls=None),
        _HistoryMessage("tool", "", tool_calls=[{"name": "ops_disk_usage"}]),
    ]

    await router.route("next", "sess-1")

    messages = state["last_messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[1] == {"role": "user", "content": "next"}


@pytest.mark.asyncio
async def test_route_drops_leaked_tool_notes_from_history(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("ok")]
    state["history"] = [
        _HistoryMessage("user", "hi"),
        _HistoryMessage("assistant", "[tool core_list_tools]"),
        _HistoryMessage("assistant", "Tool result (ops_disk_usage):\ndisk 42%"),
        _HistoryMessage("assistant", "real answer"),
    ]

    await router.route("next", "sess-1")

    messages = state["last_messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[2]["content"] == "real answer"


@pytest.mark.asyncio
async def test_route_does_not_persist_direct_plan(patched_router):
    router, state = patched_router
    state["queue"] = [
        _plan_response(
            {"goal": "disk", "steps": [{"tool": "core_health", "why": "x"}]}
        )
    ]

    result = await router.route("hi", "sess-1")

    assert result["type"] == "plan"
    assert result["plan"]["mode"] == "direct"
    assert state["plans_saved"] == []


@pytest.mark.asyncio
async def test_route_recovers_textual_agent_plan(patched_router):
    router, state = patched_router
    plan_args = {
        "goal": "inspect",
        "mode": "direct",
        "steps": [{"tool": "core_health", "args": {}, "why": "x"}],
    }
    state["queue"] = [_text_response(f"[tool agent_plan] {json.dumps(plan_args)}")]

    result = await router.route("hi", "sess-1")

    assert result["type"] == "plan"
    assert result["plan"]["steps"][0]["tool"] == "core_health"
    assert state["assistant_saved"] == []


@pytest.mark.asyncio
async def test_route_recovers_textual_real_tool(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("[tool core_list_tools]")]

    result = await router.route("show tools", "sess-1")

    assert result["type"] == "plan"
    plan = result["plan"]
    assert plan["mode"] == "direct"
    assert plan["steps"][0]["tool"] == "core_list_tools"


@pytest.mark.asyncio
async def test_route_recovers_narrated_tool_result(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("Tool result (core_list_tools):\n")]

    result = await router.route("what are the tools", "sess-1")

    assert result["type"] == "plan"
    plan = result["plan"]
    assert plan["mode"] == "direct"
    assert plan["steps"][0]["tool"] == "core_list_tools"


@pytest.mark.asyncio
async def test_route_recovers_tagged_tool_call(patched_router):
    router, state = patched_router
    body = json.dumps(
        {
            "name": "agent_plan",
            "arguments": {
                "goal": "x",
                "mode": "direct",
                "steps": [{"tool": "core_health", "args": {}, "why": "x"}],
            },
        }
    )
    state["queue"] = [_text_response(f"<tool_call>{body}</tool_call>")]

    result = await router.route("hi", "sess-1")

    assert result["type"] == "plan"
    assert result["plan"]["steps"][0]["tool"] == "core_health"


@pytest.mark.asyncio
async def test_route_ignores_unknown_textual_tool(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("[tool ghost_tool] {}")]

    result = await router.route("hi", "sess-1")

    assert result["type"] == "text"
    assert result["content"] == "[tool ghost_tool] {}"


@pytest.mark.asyncio
async def test_route_recovers_unwrapped_agent_plan_text(patched_router):
    router, state = patched_router
    plan_args = {
        "goal": "List available tools",
        "mode": "direct",
        "steps": [{"tool": "core_list_tools", "args": {}, "why": "show tools"}],
    }
    state["queue"] = [_text_response(f"agent_plan {json.dumps(plan_args)}")]

    result = await router.route("show tools", "sess-1")

    assert result["type"] == "plan"
    assert result["plan"]["steps"][0]["tool"] == "core_list_tools"
    assert state["assistant_saved"] == []


@pytest.mark.asyncio
async def test_route_recovers_unwrapped_reply_text(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response('reply { "text": "Hi there!" }')]

    result = await router.route("hi", "sess-1")

    assert result["type"] == "text"
    assert result["content"] == "Hi there!"
    assert state["assistant_saved"][0][1] == "Hi there!"
    assert state["calls"] == 1


@pytest.mark.asyncio
async def test_route_recovers_unwrapped_known_tool_text(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("core_list_tools {}")]

    result = await router.route("what are the tools", "sess-1")

    assert result["type"] == "plan"
    plan = result["plan"]
    assert plan["mode"] == "direct"
    assert plan["steps"][0]["tool"] == "core_list_tools"


@pytest.mark.asyncio
async def test_route_unwrapped_unknown_tool_not_executed(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response('ghost_tool {"a": 1}')]

    result = await router.route("hi", "sess-1")

    assert result["type"] == "text"


@pytest.mark.asyncio
async def test_route_unwrapped_plan_after_reask_never_leaks(patched_router):
    router, state = patched_router
    hovered = 'agent_plan {"goal": "x", "steps": [{"tool": ""}]} extra prose'
    state["queue"] = [_text_response(hovered), _text_response(hovered)]

    result = await router.route("hi", "sess-1")

    assert result["type"] == "text"
    assert "agent_plan" not in result["content"]
    assert "steps" not in result["content"]
    assert state["calls"] == 2


def test_leaked_plan_object_detects_json_steps():
    detect = router_module._leaked_plan_object

    assert detect('{"goal": "x", "steps": [{"tool": "core_health"}]}') is not None
    assert detect('Sure thing.\n{"steps": [{"tool": "core_health"}]}') is not None
    assert detect("no json here") is None
    assert detect('{"goal": "x"}') is None


def test_recover_textual_plan_args_reply_sentinel():
    recover = router_module.AIRouter._recover_textual_plan_args

    recovered = recover('reply { "text": "hello" }', "hi", set())

    assert recovered is not None and recovered.get("__reply__") is True
    assert recovered["text"] == "hello"


def test_api_history_drops_leaked_plan_text():
    api = router_module.AIRouter._api_history

    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": 'agent_plan {"goal": "x", "steps": [{"tool": "core_health"}]}',
        },
        {"role": "assistant", "content": "Alright."},
    ]

    assert api(messages) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Alright."},
    ]


def test_is_bare_tool_name_variants():
    is_bare = router_module._is_bare_tool_name

    assert is_bare("reply")
    assert is_bare("  agent_plan  ")
    assert is_bare("`reply`")
    assert is_bare('"agent_plan"')
    assert is_bare("Reply")
    assert is_bare("core_health", {"core_health", "ops_disk_usage"})
    assert not is_bare("Reply is required")
    assert not is_bare("hello")
    assert not is_bare("")
    assert not is_bare("core_health")


@pytest.mark.asyncio
async def test_route_bare_reply_name_is_not_shown(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("reply"), _reply_response("Hi there!")]

    result = await router.route("hi", "sess-1")

    assert result["type"] == "text"
    assert result["content"] == "Hi there!"
    assert state["calls"] == 2
    assert [content for _, content, _ in state["assistant_saved"]] == ["Hi there!"]


@pytest.mark.asyncio
async def test_route_bare_agent_plan_name_falls_back_after_reask(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("agent_plan"), _text_response("agent_plan")]

    result = await router.route("hi", "sess-1")

    assert result["type"] == "text"
    assert result["content"] == router_module._CONFUSED_REPLY
    assert state["calls"] == 2
    assert state["assistant_saved"][0][1] == router_module._CONFUSED_REPLY


@pytest.mark.asyncio
async def test_route_bare_known_tool_name_is_malformed(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("core_health"), _text_response("hello")]

    result = await router.route("hi", "sess-1")

    assert result["content"] == "hello"
    assert state["calls"] == 2


@pytest.mark.asyncio
async def test_route_reply_tool_call_with_empty_text_is_malformed(patched_router):
    router, state = patched_router
    state["queue"] = [_reply_response(""), _reply_response("Hi there!")]

    result = await router.route("hi", "sess-1")

    assert result["content"] == "Hi there!"
    assert state["calls"] == 2


def test_api_history_drops_bare_tool_names():
    api = router_module.AIRouter._api_history

    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "agent_plan"},
        {"role": "assistant", "content": "reply"},
        {"role": "assistant", "content": "Alright."},
        {"role": "assistant", "content": "[tool core_health] {}"},
    ]

    assert api(messages) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Alright."},
    ]


@pytest.mark.asyncio
async def test_route_unwrapped_invalid_json_body_not_executed(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("core_list_tools {oops}")]

    result = await router.route("show tools", "sess-1")

    assert result["type"] == "text"
    assert state["calls"] == 1
    assert state["plans_saved"] == []


@pytest.mark.asyncio
async def test_route_prose_with_known_name_and_brace_not_executed(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response("I could call core_health { maybe }")]

    result = await router.route("hi", "sess-1")

    assert result["type"] == "text"
    assert state["calls"] == 1
    assert "core_health" in result["content"]


@pytest.mark.asyncio
async def test_route_reask_skips_empty_assistant_content(patched_router):
    router, state = patched_router
    state["queue"] = [_text_response(""), _reply_response("Hi there!")]

    result = await router.route("hi", "sess-1")

    assert result["content"] == "Hi there!"
    assert state["calls"] == 2
    assert not any(
        message["role"] == "assistant" and not message.get("content")
        for message in state["last_messages"]
    )
