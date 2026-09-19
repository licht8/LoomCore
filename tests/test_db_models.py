"""Unit tests for session-id column widths and the chat request contract (no DB)."""

import pytest
from pydantic import ValidationError

from src.api.chat import ChatRequest
from src.db.models import AuditEntry, Message, Session


def _max_length(field) -> int:
    return max(
        (m.max_length for m in field.metadata if getattr(m, "max_length", None)),
        default=0,
    )


def test_session_id_columns_are_64_chars():
    assert Session.__table__.c.id.type.length == 64
    assert Message.__table__.c.session_id.type.length == 64
    assert AuditEntry.__table__.c.session_id.type.length == 64


def test_chat_request_session_id_fits_session_column():
    declared = _max_length(ChatRequest.model_fields["session_id"])
    assert declared == 64
    assert declared <= Session.__table__.c.id.type.length


def test_chat_request_default_session_id_fits():
    payload = ChatRequest(message="hi")
    assert len(payload.session_id) <= Session.__table__.c.id.type.length


def test_chat_request_accepts_sess_prefix_and_64_char_id():
    prefix = ChatRequest(session_id="sess_" + "a" * 32, message="hi")
    assert len(prefix.session_id) == 37
    ChatRequest(session_id="a" * 64, message="hi")


def test_chat_request_rejects_65_char_session_id():
    with pytest.raises(ValidationError):
        ChatRequest(session_id="a" * 65, message="hi")
