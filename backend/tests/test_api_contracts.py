from uuid import UUID

import pytest
from pydantic import ValidationError

from minsky_api.api.contracts import (
    AgentMessage,
    ChatRequest,
    ChatResponse,
    ErrorCode,
    ErrorResponse,
    UserMessage,
)
from minsky_api.config import get_settings

CONVERSATION_ID = "7f1c2e9a-4b1d-4c3e-9a51-2d0b8f6e1a22"
MAX_CHARS = get_settings().max_message_chars


def test_first_request_has_no_conversation_id():
    request = ChatRequest.model_validate({"messages": [{"user": "Me cobraron dos veces ayer"}]})
    assert request.conversation_id is None
    assert request.messages == [UserMessage(user="Me cobraron dos veces ayer")]


def test_follow_up_carries_the_whole_conversation():
    request = ChatRequest.model_validate(
        {
            "conversation_id": CONVERSATION_ID,
            "messages": [
                {"user": "Me cobraron dos veces ayer"},
                {"agent": "¿Cuál de los dos cargos no reconocés?"},
                {"user": "El segundo"},
            ],
        }
    )
    assert request.conversation_id == UUID(CONVERSATION_ID)
    assert [type(m) for m in request.messages] == [UserMessage, AgentMessage, UserMessage]
    assert [m.text for m in request.messages] == [
        "Me cobraron dos veces ayer",
        "¿Cuál de los dos cargos no reconocés?",
        "El segundo",
    ]


def test_spanish_and_portuguese_text_survive_unchanged():
    text = "Não reconheço a cobrança de São Paulo; año, ñandú"
    assert ChatRequest.model_validate({"messages": [{"user": text}]}).messages[0].text == text


def test_text_at_the_length_limit_is_accepted():
    ChatRequest.model_validate({"messages": [{"user": "a" * MAX_CHARS}]})


def test_length_limit_comes_from_settings(monkeypatch):
    monkeypatch.setenv("MINSKY_MAX_MESSAGE_CHARS", "5")
    get_settings.cache_clear()
    try:
        ChatRequest.model_validate({"messages": [{"user": "12345"}]})
        with pytest.raises(ValidationError):
            ChatRequest.model_validate({"messages": [{"user": "123456"}]})
    finally:
        monkeypatch.delenv("MINSKY_MAX_MESSAGE_CHARS")
        get_settings.cache_clear()


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="no-messages"),
        pytest.param({"messages": []}, id="empty-messages"),
        pytest.param({"messages": "hola"}, id="messages-not-a-list"),
        pytest.param({"messages": [{"user": "hola", "agent": "hola"}]}, id="two-roles-in-one-message"),
        pytest.param({"messages": [{}]}, id="message-without-role"),
        pytest.param({"messages": [{"customer": "hola"}]}, id="unknown-role"),
        pytest.param({"messages": [{"role": "user", "text": "hola"}]}, id="role-text-shape"),
        pytest.param({"messages": [{"user": ""}]}, id="empty-text"),
        pytest.param({"messages": [{"user": "   \n\t"}]}, id="blank-text"),
        pytest.param({"messages": [{"user": "a" * (MAX_CHARS + 1)}]}, id="text-too-long"),
        pytest.param({"messages": [{"user": 123}]}, id="text-not-a-string"),
        pytest.param({"messages": [{"user": "hola"}], "conversation_id": "abc"}, id="conversation-id-not-uuid"),
        pytest.param({"messages": [{"user": "hola"}], "customer_id": "CUST-1"}, id="identity-in-body"),
        pytest.param({"messages": [{"user": "hola"}], "channel": "web_chat"}, id="unknown-field"),
    ],
)
def test_rejects_invalid_requests(body):
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(body)


def test_requests_are_immutable():
    request = ChatRequest.model_validate({"messages": [{"user": "hola"}]})
    with pytest.raises(ValidationError):
        request.conversation_id = UUID(CONVERSATION_ID)  # type: ignore[misc]


def test_response_round_trips_to_the_request_shape():
    response = ChatResponse(
        conversation_id=UUID(CONVERSATION_ID),
        messages=[UserMessage(user="hola"), AgentMessage(agent="¿En qué te ayudo?")],
    )
    assert response.model_dump(mode="json") == {
        "conversation_id": CONVERSATION_ID,
        "messages": [{"user": "hola"}, {"agent": "¿En qué te ayudo?"}],
    }


def test_response_requires_a_conversation_id():
    with pytest.raises(ValidationError):
        ChatResponse.model_validate({"messages": [{"user": "hola"}]})


def test_error_response_uses_known_codes_only():
    error = ErrorResponse(code=ErrorCode.SESSION_EXPIRED, message="Tu sesión venció", request_id="req-1")
    assert error.model_dump(mode="json")["code"] == "session_expired"
    with pytest.raises(ValidationError):
        ErrorResponse.model_validate({"code": "teapot", "message": "x", "request_id": "req-1"})
