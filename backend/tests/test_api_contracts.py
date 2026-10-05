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
from minsky_api.main import create_app

CONVERSATION_ID = "7f1c2e9a-4b1d-4c3e-9a51-2d0b8f6e1a22"
MAX_CHARS = get_settings().max_message_chars


@pytest.fixture
def max_chars_env(monkeypatch):
    """Sets MINSKY_MAX_MESSAGE_CHARS for one test and leaves the settings cache clean afterwards."""

    def set_value(value: str) -> None:
        monkeypatch.setenv("MINSKY_MAX_MESSAGE_CHARS", value)
        get_settings.cache_clear()

    yield set_value
    monkeypatch.delenv("MINSKY_MAX_MESSAGE_CHARS", raising=False)
    get_settings.cache_clear()


def test_first_request_has_no_conversation_id():
    request = ChatRequest.model_validate({"messages": [{"user": "Me cobraron dos veces ayer"}]})
    assert request.conversation_id is None
    assert request.messages == (UserMessage(user="Me cobraron dos veces ayer"),)


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


def test_length_limit_comes_from_settings(max_chars_env):
    max_chars_env("5")
    ChatRequest.model_validate({"messages": [{"user": "12345"}]})
    with pytest.raises(ValidationError):
        ChatRequest.model_validate({"messages": [{"user": "123456"}]})


@pytest.mark.parametrize("value", ["abc", "0", "-1"])
def test_invalid_length_setting_fails_at_startup(max_chars_env, value):
    max_chars_env(value)
    with pytest.raises(ValidationError):
        create_app()


@pytest.mark.parametrize("value", ["abc", "0"])
def test_invalid_length_setting_is_a_server_error_not_a_client_error(max_chars_env, value):
    max_chars_env(value)
    with pytest.raises(RuntimeError, match="invalid server settings"):
        ChatRequest.model_validate({"messages": [{"user": "hola"}]})


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
        pytest.param(
            {"messages": [{"user": "hola"}], "conversation_id": "00000000-0000-0000-0000-000000000000"},
            id="conversation-id-nil-uuid",
        ),
        pytest.param(
            {"messages": [{"user": "hola"}], "conversation_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8"},
            id="conversation-id-not-uuid4",
        ),
        pytest.param({"messages": [{"user": "hola", "customer_id": "CUST-1"}]}, id="identity-inside-message"),
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
    assert isinstance(request.messages, tuple)
    hash(request)


def test_response_round_trips_to_the_request_shape():
    response = ChatResponse(
        conversation_id=UUID(CONVERSATION_ID),
        messages=(UserMessage(user="hola"), AgentMessage(agent="¿En qué te ayudo?")),
        mode="workflow",
    )
    assert response.model_dump(mode="json") == {
        "conversation_id": CONVERSATION_ID,
        "messages": [{"user": "hola"}, {"agent": "¿En qué te ayudo?"}],
        "mode": "workflow",
    }


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"messages": [{"agent": "hola"}]}, id="no-conversation-id"),
        pytest.param({"conversation_id": CONVERSATION_ID, "messages": []}, id="no-messages"),
        pytest.param(
            {"conversation_id": CONVERSATION_ID, "messages": [{"agent": "hola"}, {"user": "chau"}]},
            id="ends-with-user",
        ),
    ],
)
def test_rejects_invalid_responses(body):
    with pytest.raises(ValidationError):
        ChatResponse.model_validate(body)


def test_error_response_uses_known_codes_only():
    error = ErrorResponse(code=ErrorCode.SESSION_EXPIRED, message="Tu sesión venció", request_id="req-1")
    assert error.model_dump(mode="json")["code"] == "session_expired"
    with pytest.raises(ValidationError):
        ErrorResponse.model_validate({"code": "teapot", "message": "x", "request_id": "req-1"})


def test_a_request_may_name_the_flow_of_a_new_conversation_and_only_two_flows_exist():
    assert ChatRequest.model_validate({"messages": [{"user": "hola"}], "mode": "agentic"}).mode == "agentic"
    assert ChatRequest.model_validate({"messages": [{"user": "hola"}]}).mode is None
    with pytest.raises(ValidationError):
        ChatRequest.model_validate({"messages": [{"user": "hola"}], "mode": "something-else"})
