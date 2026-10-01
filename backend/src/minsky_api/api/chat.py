"""POST /api/chat/turn — POC chat endpoint over the dispute orchestrator.

Identity is a temporary header (X-Minsky-Customer-Id) until API-key + OTP exist.
"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request
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

router = APIRouter(prefix="/api/chat", tags=["chat"])

CUSTOMER_HEADER = "X-Minsky-Customer-Id"


def _error(code: ErrorCode, message: str, status_code: int) -> JSONResponse:
    body = ErrorResponse(code=code, message=message, request_id=str(uuid4()))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _last_user_text(request: ChatRequest) -> str:
    last = request.messages[-1]
    if not isinstance(last, UserMessage):
        raise HTTPException(status_code=400, detail="last message must be from the user")
    return last.text


@router.post("/turn", response_model=None)
async def chat_turn(
    body: ChatRequest,
    request: Request,
    x_minsky_customer_id: Annotated[str | None, Header(alias=CUSTOMER_HEADER)] = None,
) -> ChatResponse | JSONResponse:
    """One customer turn. POC auth: require X-Minsky-Customer-Id."""
    if not x_minsky_customer_id or not x_minsky_customer_id.strip():
        return _error(ErrorCode.MISSING_CREDENTIALS, f"header {CUSTOMER_HEADER} is required", 401)

    conversations: ConversationStore = request.app.state.conversations
    cases: InMemoryCasesBackend = request.app.state.cases

    try:
        user_text = _last_user_text(body)
    except HTTPException:
        return _error(ErrorCode.FORGED_AGENT_TURN, "last message must be from the user", 400)

    if body.conversation_id is None:
        state = ConversationState(conversation_id=uuid4())
    else:
        state = conversations.get(body.conversation_id)
        if state is None:
            return _error(ErrorCode.CONVERSATION_NOT_FOUND, "unknown conversation_id", 404)

    tool_session = ToolSession(
        session_id=f"poc-{x_minsky_customer_id.strip()}",
        state=SessionState.VALID,
        customer_id=x_minsky_customer_id.strip(),
    )

    try:
        llm = LLM()
    except LLMNotConfiguredError:
        return _error(ErrorCode.INVALID_CREDENTIALS, "LLM is not configured (MINSKY_LLM_API_KEY)", 503)

    try:
        async with session() as db:
            ctx = ToolContext(session=tool_session, db=db, cases=cases)
            state, reply = await run_turn(state, user_text, ctx, llm)
    except ValueError as exc:
        return _error(ErrorCode.INVALID_PAYLOAD, str(exc), 400)

    conversations.put(state)
    history: list[UserMessage | AgentMessage] = []
    for role, text in state.messages:
        history.append(UserMessage(user=text) if role == "user" else AgentMessage(agent=text))
    return ChatResponse(conversation_id=state.conversation_id, messages=tuple(history))
