"""Unit tests for prompt assembly."""

from src.llm.prompt import SYSTEM_PROMPT, build_messages


def test_build_messages_orders_system_history_user():
    history = [
        {"role": "user", "content": "previous"},
        {"role": "assistant", "content": "reply"},
    ]

    messages = build_messages("hello", history)

    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert messages[1] == {"role": "user", "content": "previous"}
    assert messages[2] == {"role": "assistant", "content": "reply"}
    assert messages[-1] == {"role": "user", "content": "hello"}
    assert len(messages) == 4


def test_build_messages_without_history():
    messages = build_messages("solo", [])

    assert messages == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "solo"},
    ]


def test_build_messages_does_not_mutate_history():
    history = [{"role": "user", "content": "kept"}]

    build_messages("hello", history)

    assert history == [{"role": "user", "content": "kept"}]
