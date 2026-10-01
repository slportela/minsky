"""POST /api/chat/turn — POC chat endpoint over the dispute orchestrator.

Identity is a temporary header (X-Minsky-Customer-Id) until API-key + OTP exist.
Conversations are bound to that customer id; client history must match the server store.
"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from minsky_api.agent.memory import ConversationStore
from minsky_api.agent.orchestrator import run_turn
from minsky_api.agent.state import ConversationState
from minsky_api.api.contracts import (
    AgentMessage,
    ChatRequest,
    ChatResponse,
    ErrorCode,
    ErrorResponse,
    UserMessage,
)
from minsky_api.identity.session import SessionState, ToolSession
from minsky_api.llm.client import LLM, LLMNotConfiguredError
from minsky_api.store.cases_memory import InMemoryCasesBackend
from minsky_api.store.db import session
from minsky_api.tools.context import ToolContext
from minsky_api.tools.errors import ToolDenied, ToolError

router = APIRouter(prefix="/api/chat", tags=["chat"])

CUSTOMER_HEADER = "X-Minsky-Customer-Id"


def _error(code: ErrorCode, message: str, status_code: int) -> JSONResponse:
    body = ErrorResponse(code=code, message=message, request_id=str(uuid4()))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _history_tuples(body: ChatRequest) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for message in body.messages:
        if isinstance(message, UserMessage):
            out.append(("user", message.text))
        else:
            out.append(("agent", message.text))
    return out


def _check_history(state: ConversationState, body: ChatRequest) -> ErrorCode | None:
    """Client must replay server history and append exactly one new user turn."""
    incoming = _history_tuples(body)
    if not incoming or incoming[-1][0] != "user":
        return ErrorCode.FORGED_AGENT_TURN
    prior = incoming[:-1]
    if prior != state.messages:
        return ErrorCode.HISTORY_MISMATCH
    return None


@router.post("/turn", response_model=None)
async def chat_turn(
    body: ChatRequest,
    request: Request,
    x_minsky_customer_id: Annotated[str | None, Header(alias=CUSTOMER_HEADER)] = None,
) -> ChatResponse | JSONResponse:
    """One customer turn. POC auth: require X-Minsky-Customer-Id (not real auth)."""
    if not x_minsky_customer_id or not x_minsky_customer_id.strip():
        return _error(ErrorCode.MISSING_CREDENTIALS, f"header {CUSTOMER_HEADER} is required", 401)
    customer_id = x_minsky_customer_id.strip()

    conversations: ConversationStore = request.app.state.conversations
    cases: InMemoryCasesBackend = request.app.state.cases

    if body.conversation_id is None:
        state = ConversationState(conversation_id=uuid4(), customer_id=customer_id)
        if len(body.messages) != 1 or not isinstance(body.messages[0], UserMessage):
            return _error(ErrorCode.INVALID_PAYLOAD, "first turn must be a single user message", 400)
        user_text = body.messages[0].text
    else:
        state = conversations.get(body.conversation_id)
        if state is None:
            return _error(ErrorCode.CONVERSATION_NOT_FOUND, "unknown conversation_id", 404)
        if state.customer_id != customer_id:
            return _error(ErrorCode.CONVERSATION_FORBIDDEN, "conversation belongs to another customer", 403)
        mismatch = _check_history(state, body)
        if mismatch is not None:
            return _error(mismatch, "conversation history does not match the server", 409)
        user_text = body.messages[-1].text if isinstance(body.messages[-1], UserMessage) else ""
        if not user_text:
            return _error(ErrorCode.FORGED_AGENT_TURN, "last message must be from the user", 400)

    tool_session = ToolSession(
        session_id=f"poc-{customer_id}",
        state=SessionState.VALID,
        customer_id=customer_id,
    )

    try:
        llm = LLM()
    except LLMNotConfiguredError:
        return _error(ErrorCode.SERVICE_UNAVAILABLE, "LLM is not configured (MINSKY_LLM_API_KEY)", 503)

    try:
        async with session() as db:
            ctx = ToolContext(session=tool_session, db=db, cases=cases)
            state, _reply = await run_turn(state, user_text, ctx, llm)
    except ValueError as exc:
        return _error(ErrorCode.INVALID_PAYLOAD, str(exc), 400)
    except PermissionError as exc:
        return _error(ErrorCode.CONVERSATION_FORBIDDEN, str(exc), 403)
    except (ToolDenied, ToolError) as exc:
        return _error(ErrorCode.TOOL_FAILURE, str(exc), 502)
    except RuntimeError as exc:
        return _error(ErrorCode.SERVICE_UNAVAILABLE, str(exc), 503)

    conversations.put(state)
    history: list[UserMessage | AgentMessage] = []
    for role, text in state.messages:
        history.append(UserMessage(user=text) if role == "user" else AgentMessage(agent=text))
    return ChatResponse(conversation_id=state.conversation_id, messages=tuple(history))
