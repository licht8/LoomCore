"""Unit tests for tool-result text extraction."""

from src.interfaces.tool_events import extract_text


def test_extract_text_joins_text_content():
    raw = {
        "content": [
            {"type": "text", "text": "hello "},
            {"type": "text", "text": "world"},
        ]
    }
    assert extract_text(raw) == "hello world"


def test_extract_text_stringifies_non_list_content():
    assert extract_text({"content": "plain"}) == "plain"


def test_extract_text_stringifies_dict_content():
    assert extract_text({"content": {"key": "value"}}) == str({"key": "value"})


def test_extract_text_ignores_non_dict_elements():
    raw = {
        "content": [
            {"type": "text", "text": "ok"},
            "junk",
            42,
            {"type": "text", "text": "!"},
        ]
    }
    assert extract_text(raw) == "ok!"


def test_extract_text_coerces_non_string_text():
    raw = {
        "content": [
            {"type": "text", "text": 42},
            {"type": "text", "text": None},
            {"type": "text"},
            {"type": "text", "text": "end"},
        ]
    }
    assert extract_text(raw) == "42end"


def test_extract_text_returns_empty_for_none():
    assert extract_text(None) == ""


def test_extract_text_stringifies_non_dict_raw():
    assert extract_text("raw") == "raw"
