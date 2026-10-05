"""Degraded mode over HTTP: when the model is unavailable the customer is handed off, not shown a 500.

docs/architecture.md, principle 5: "Degrade to a human, never to a guess." The fallback never claims an
action beyond the handoff it created, and it names that handoff so the customer can quote it.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient

from minsky_api.agent.memory import ConversationStore
from minsky_api.agent.state import Phase
from minsky_api.config import get_settings
from minsky_api.llm.client import ModelMismatchError, ModelOutputError
from minsky_api.main import create_app
from minsky_api.store.cases_memory import InMemoryCasesBackend
from minsky_api.tools import bank as bank_tools
from minsky_api.tools.errors import ToolError

_HANDOFF = re.compile(r"HO-[0-9a-f]{12}")
_REQUEST = httpx2.Request("POST", "https://llm.invalid/v1/responses")


def _handoff_id(reply: str) -> str:
    match = _HANDOFF.search(reply)
    assert match, reply
    return match.group(0)


AUTH = {"Authorization": "Bearer token-c1"}


def _provider_errors() -> list[BaseException]:
    return [
        openai.APITimeoutError(request=_REQUEST),
        openai.APIConnectionError(request=_REQUEST),
        openai.RateLimitError("slow down", response=httpx2.Response(429, request=_REQUEST), body=None),
        openai.InternalServerError("upstream", response=httpx2.Response(500, request=_REQUEST), body=None),
        ModelMismatchError("asked for gpt-6-luna, got something-else"),
        ModelOutputError("the model returned a reply that does not fit the schema"),
    ]


class _Turn:
    """What the faked orchestrator does on the next call: succeed, or mutate the state and then fail."""

    def __init__(self) -> None:
        self.failure: Exception | None = None
        self.partial_txn: str | None = None


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setenv(
        "MINSKY_TEST_SESSIONS",
        '{"token-c1":{"customer_id":"C1","expires_at":"2099-01-01T00:00:00Z"}}',
    )
    get_settings.cache_clear()
    app = create_app()
    app.state.cases = InMemoryCasesBackend()
    app.state.conversations = ConversationStore()
    turn = _Turn()

    @asynccontextmanager
    async def fake_session():
        db = MagicMock()
        db.get = AsyncMock(return_value=None)  # enqueue_case looks up resolution benchmarks
        yield db

    async def fake_run_turn(state, text, ctx, llm):
        from minsky_api.agent.language import default_language_detector

        state.turn_count += 1
        state.messages.append(("user", text))
        if state.language is None:
            state.language = default_language_detector().detect(text)
        if turn.partial_txn:
            state.selected_txn_id = turn.partial_txn  # progress of a turn that then fails
        if turn.failure is not None:
            raise turn.failure
        state.messages.append(("agent", "ok"))
        return state, "ok"

    monkeypatch.setattr("minsky_api.api.chat.run_turn", fake_run_turn)
    monkeypatch.setattr("minsky_api.api.chat.LLM", MagicMock)
    monkeypatch.setattr("minsky_api.api.chat.session", fake_session)
    # a 500 must show up as a status code here, not as an exception raised into the test
    with TestClient(app, raise_server_exceptions=False) as client:
        yield app, client, turn
    get_settings.cache_clear()


def _post(client, text, conversation_id=None):
    body = {"messages": [{"user": text}]}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return client.post("/api/chat/turn", json=body, headers=AUTH)


@pytest.mark.parametrize("failure", _provider_errors(), ids=lambda e: type(e).__name__)
def test_model_failure_hands_off_instead_of_failing(harness, failure):
    app, client, turn = harness
    turn.failure = failure
    response = _post(client, "No reconozco un cargo de 25.00 USD")
    assert response.status_code == 200, response.text
    reply = response.json()["messages"][-1]["agent"]
    handoff = app.state.cases.get_handoff(_handoff_id(reply))
    assert handoff is not None and handoff.customer_id == "C1"
    assert handoff.reason == "assistant_unavailable"


def test_reply_is_spanish_by_default_and_names_no_other_action(harness):
    _app, client, turn = harness
    turn.failure = openai.APITimeoutError(request=_REQUEST)
    reply = _post(client, "No reconozco un cargo").json()["messages"][-1]["agent"]
    assert "problema técnico" in reply
    for claim in ("abrí", "reclamo abierto", "bloque", "reembols", "devol"):
        assert claim not in reply.lower()


def test_reply_follows_a_portuguese_message(harness):
    _app, client, turn = harness
    turn.failure = openai.APITimeoutError(request=_REQUEST)
    reply = _post(client, "Não reconheço uma cobrança de 25.00 USD").json()["messages"][-1]["agent"]
    assert "problema técnico" in reply and "Tive" in reply
    assert "Encaminhei" in reply


def test_handoff_carries_identifiers_not_the_customer_text(harness):
    app, client, turn = harness
    turn.failure = openai.APIConnectionError(request=_REQUEST)
    secret = "mi tarjeta termina en 4242 y vivo en la calle Falsa 123"
    reply = _post(client, secret).json()["messages"][-1]["agent"]
    handoff = app.state.cases.get_handoff(_handoff_id(reply))
    assert "4242" not in repr(handoff.facts) and "Falsa" not in repr(handoff.facts)
    assert handoff.facts["failure"] == "APIConnectionError"
    assert handoff.facts["phase"] == "understand"


def test_handoff_keeps_progress_from_the_failed_turn(harness):
    app, client, turn = harness
    turn.failure = openai.APITimeoutError(request=_REQUEST)
    turn.partial_txn = "T-PARTIAL"  # the failed turn had already set this on the live state
    response = _post(client, "hola")
    state = app.state.conversations.get(UUID(response.json()["conversation_id"]))
    assert state.phase == Phase.DONE
    assert state.selected_txn_id == "T-PARTIAL"
    assert [role for role, _ in state.messages] == ["user", "agent"]
    reply = response.json()["messages"][-1]["agent"]
    handoff = app.state.cases.get_handoff(_handoff_id(reply))
    assert handoff is not None
    assert handoff.facts["transaction_id"] == "T-PARTIAL"


def test_handoff_reports_a_dispute_opened_before_the_failure(harness):
    app, client, turn = harness
    turn.failure = openai.APITimeoutError(request=_REQUEST)
    turn.partial_txn = "T1"
    app.state.cases.create_dispute(customer_id="C1", transaction_id="T1", reason="unrecognized")
    response = _post(client, "hola")
    assert response.status_code == 200, response.text
    reply = response.json()["messages"][-1]["agent"]
    handoff = app.state.cases.get_handoff(_handoff_id(reply))
    assert handoff is not None
    dispute_id = handoff.facts["dispute_id"]
    assert isinstance(dispute_id, str) and dispute_id.startswith("DSP-")
    assert dispute_id in reply
    assert f"dispute_opened:{dispute_id}" in handoff.actions


def test_failure_on_a_later_turn_keeps_the_history(harness):
    app, client, turn = harness
    first = _post(client, "hola")
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]
    turn.failure = openai.RateLimitError("slow down", response=httpx2.Response(429, request=_REQUEST), body=None)
    body = {
        "conversation_id": conversation_id,
        "messages": [*first.json()["messages"], {"user": "sigo aquí"}],
    }
    second = client.post("/api/chat/turn", json=body, headers=AUTH)
    assert second.status_code == 200, second.text
    texts = [next(iter(m.values())) for m in second.json()["messages"]]
    assert texts[:3] == ["hola", "ok", "sigo aquí"] and "problema técnico" in texts[3]


def test_if_the_handoff_cannot_be_created_the_answer_is_an_honest_503(harness, monkeypatch):
    app, client, turn = harness
    turn.failure = openai.APITimeoutError(request=_REQUEST)

    def broken(**_kwargs):
        raise RuntimeError("password=hunter2 connection refused")

    monkeypatch.setattr(app.state.cases, "create_handoff", broken)
    response = _post(client, "hola")
    assert response.status_code == 503
    assert "hunter2" not in response.text and "refused" not in response.text


def test_other_failures_keep_their_status(harness):
    from minsky_api.agent.speak import SpeechError

    _app, client, turn = harness
    turn.failure = ToolError("bank read failed")
    assert _post(client, "hola").status_code == 502
    turn.failure = RuntimeError("unexpected orchestration state")
    assert _post(client, "hola").status_code == 503
    turn.failure = SpeechError("compose_speech: reply drops merchant")
    response = _post(client, "hola")
    assert response.status_code == 200
    assert _HANDOFF.search(response.json()["messages"][-1]["agent"])


def test_auth_provider_errors_are_503_without_a_handoff(harness):
    _app, client, turn = harness
    turn.failure = openai.AuthenticationError(
        "bad key",
        response=httpx2.Response(401, request=_REQUEST),
        body=None,
    )
    response = _post(client, "hola")
    assert response.status_code == 503
    # Config faults must not invent a case reference.
    assert not _HANDOFF.search(response.text)


def test_enqueue_failure_after_handoff_is_an_honest_503(harness, monkeypatch):
    _app, client, turn = harness
    turn.failure = openai.APITimeoutError(request=_REQUEST)

    async def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("case queue down")

    monkeypatch.setattr(bank_tools, "enqueue_case", fail_enqueue)
    response = _post(client, "hola")
    assert response.status_code == 503
    assert not _HANDOFF.search(response.text)


@pytest.mark.asyncio
async def test_the_handoff_is_idempotent_per_conversation_and_turn():
    from uuid import uuid4

    from minsky_api.agent.degraded import hand_off_on_outage
    from minsky_api.agent.state import ConversationState
    from minsky_api.identity.session import SessionState, ToolSession
    from minsky_api.tools.context import ToolContext

    cases = InMemoryCasesBackend()
    session = ToolSession(session_id="s1", state=SessionState.VALID, customer_id="C1")
    db = MagicMock()
    db.get = AsyncMock(return_value=None)
    ctx = ToolContext(session=session, db=db, cases=cases)
    live = ConversationState(conversation_id=uuid4(), customer_id="C1", language="es", turn_count=1)
    live.messages.append(("user", "hola"))
    failure = openai.APITimeoutError(request=_REQUEST)
    _, first = await hand_off_on_outage(ctx, live, "hola", failure, language="es")
    _, again = await hand_off_on_outage(ctx, live, "hola", failure, language="es")
    assert _handoff_id(first) == _handoff_id(again)


@pytest.mark.asyncio
async def test_outage_reuses_an_existing_handoff_for_the_same_turn():
    """max_turns (or similar) must not yield a second queue case when speech then fails."""
    from uuid import uuid4

    from minsky_api.agent.degraded import hand_off_on_outage
    from minsky_api.agent.state import ConversationState
    from minsky_api.identity.session import SessionState, ToolSession
    from minsky_api.tools.context import ToolContext

    cases = InMemoryCasesBackend()
    session = ToolSession(session_id="s1", state=SessionState.VALID, customer_id="C1")
    db = MagicMock()
    db.get = AsyncMock(return_value=None)
    ctx = ToolContext(session=session, db=db, cases=cases)
    conversation_id = uuid4()
    existing = cases.create_handoff(
        customer_id="C1",
        reason="max_turns",
        rule_id=None,
        facts={"transaction_id": "T1"},
        actions=(),
        idempotency_key=f"{conversation_id}:3",
    )
    live = ConversationState(
        conversation_id=conversation_id,
        customer_id="C1",
        language="es",
        turn_count=3,
        selected_txn_id="T1",
    )
    live.messages.append(("user", "hola"))
    _, reply = await hand_off_on_outage(ctx, live, "hola", openai.APITimeoutError(request=_REQUEST), language="es")
    assert _handoff_id(reply) == existing.handoff_id
    assert cases.get_handoff(existing.handoff_id) is not None
    assert cases.get_case(existing.handoff_id) is not None
    assert len(cases.list_cases()) == 1
