"""Chat API contracts: the shape of what crosses the HTTP boundary, and nothing more.

The body never carries identity: the customer comes from the API key alone. Structure is checked
here; conversation rules (who owns a conversation, whether the history matches what the server
stored) need state and live elsewhere.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import UUID4, AfterValidator, BaseModel, ConfigDict, Field, StrictStr, ValidationError, model_validator

from minsky_api.config import get_settings


def _max_message_chars() -> int:
    # A ValueError raised inside a field validator would be reported as the client's fault.
    try:
        return get_settings().max_message_chars
    except ValidationError as error:
        raise RuntimeError("invalid server settings") from error


def _check_text(text: str) -> str:
    if not text.strip():
        raise ValueError("text must not be blank")
    limit = _max_message_chars()
    if len(text) > limit:
        raise ValueError(f"text must have at most {limit} characters")
    return text


Text = Annotated[StrictStr, AfterValidator(_check_text)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UserMessage(_Strict):
    user: Text

    @property
    def text(self) -> str:
        return self.user


class AgentMessage(_Strict):
    agent: Text

    @property
    def text(self) -> str:
        return self.agent


Message = UserMessage | AgentMessage


class ChatRequest(_Strict):
    """The whole conversation so far. `conversation_id` is absent on the first request; the server assigns it."""

    conversation_id: UUID4 | None = None
    messages: tuple[Message, ...] = Field(min_length=1)


class ChatResponse(_Strict):
    conversation_id: UUID4
    messages: tuple[Message, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _ends_with_agent(self) -> ChatResponse:
        if not isinstance(self.messages[-1], AgentMessage):
            raise ValueError("a response must end with an agent message")
        return self


class ErrorCode(StrEnum):
    MISSING_CREDENTIALS = "missing_credentials"
    INVALID_CREDENTIALS = "invalid_credentials"
    SESSION_EXPIRED = "session_expired"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    INVALID_PAYLOAD = "invalid_payload"
    FORGED_AGENT_TURN = "forged_agent_turn"
    CONVERSATION_NOT_FOUND = "conversation_not_found"
    HISTORY_MISMATCH = "history_mismatch"
    CONVERSATION_FORBIDDEN = "conversation_forbidden"
    SERVICE_UNAVAILABLE = "service_unavailable"
    TOOL_FAILURE = "tool_failure"
    CASE_NOT_FOUND = "case_not_found"
    CASE_CONFLICT = "case_conflict"
    NOT_FOUND = "not_found"
    CUSTOMER_NOT_FOUND = "customer_not_found"
    RATE_LIMITED = "rate_limited"


class ErrorResponse(_Strict):
    code: ErrorCode
    message: str
    request_id: str
