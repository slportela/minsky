"""POST /api/chat/turn — POC chat endpoint over the dispute orchestrator.

Identity is resolved from a server-provisioned bearer credential; OTP/Cognito come later.
Conversations are bound to that customer id; client history must match the server store.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from minsky_api.agent.degraded import MODEL_FAILURES, hand_off_on_outage
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
from minsky_api.identity.errors import PermissionDenied
from minsky_api.identity.http import resolve_session
from minsky_api.identity.session import ToolSession
from minsky_api.llm.client import LLM, LLMNotConfiguredError
from minsky_api.store.cases_memory import InMemoryCasesBackend
from minsky_api.store.db import session
from minsky_api.tools.context import ToolContext
from minsky_api.tools.errors import ToolDenied, ToolError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


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
    authorization: Annotated[str | None, Header()] = None,
) -> ChatResponse | JSONResponse:
    """One customer turn, authenticated with a server-provisioned test credential."""
    try:
        tool_session = resolve_session(authorization)
    except PermissionDenied as exc:
        return _error(ErrorCode(exc.reason), "test session credential is missing, invalid, or expired", 401)
    except RuntimeError:
        return _error(ErrorCode.SERVICE_UNAVAILABLE, "test session credentials are unavailable", 503)
    conversations: ConversationStore = request.app.state.conversations
    conversation_id = body.conversation_id or uuid4()
    async with conversations.turn(conversation_id):
        return await _authenticated_turn(body, request, tool_session, conversation_id)


async def _authenticated_turn(
    body: ChatRequest, request: Request, tool_session: ToolSession, conversation_id: UUID
) -> ChatResponse | JSONResponse:
    assert tool_session.customer_id is not None
    customer_id = tool_session.customer_id
    conversations: ConversationStore = request.app.state.conversations
    cases: InMemoryCasesBackend = request.app.state.cases

    if body.conversation_id is None:
        state = ConversationState(conversation_id=conversation_id, customer_id=customer_id)
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

    try:
        llm = LLM()
    except LLMNotConfiguredError:
        return _error(ErrorCode.SERVICE_UNAVAILABLE, "LLM is not configured (MINSKY_LLM_API_KEY)", 503)

    before = deepcopy(state)  # the failed turn can mutate the live state before it raises
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
    except MODEL_FAILURES as exc:
        return await _degraded_turn(before, user_text, tool_session, cases, conversations, exc)
    except RuntimeError as exc:
        return _error(ErrorCode.SERVICE_UNAVAILABLE, str(exc), 503)

    conversations.put(state)
    return _chat_response(state)


def _chat_response(state: ConversationState) -> ChatResponse:
    history: list[UserMessage | AgentMessage] = []
    for role, text in state.messages:
        history.append(UserMessage(user=text) if role == "user" else AgentMessage(agent=text))
    return ChatResponse(conversation_id=state.conversation_id, messages=tuple(history))


async def _degraded_turn(
    before: ConversationState,
    user_text: str,
    tool_session: ToolSession,
    cases: InMemoryCasesBackend,
    conversations: ConversationStore,
    failure: Exception,
) -> ChatResponse | JSONResponse:
    """The model is unavailable: hand off with context and say so (docs/architecture.md, principle 5)."""
    logger.warning("model unavailable (%s): handing the conversation to an agent", type(failure).__name__)
    try:
        async with session() as db:
            ctx = ToolContext(session=tool_session, db=db, cases=cases)
            state, _reply = await hand_off_on_outage(ctx, before, user_text, failure)
    except Exception:  # whatever stops the handoff: never promise one that does not exist
        logger.warning("handoff after model outage failed (%s)", type(failure).__name__, exc_info=True)
        return _error(ErrorCode.SERVICE_UNAVAILABLE, "the assistant is unavailable; please try again later", 503)
    conversations.put(state)
    return _chat_response(state)
