"""Degraded mode over HTTP: when the model is unavailable the customer is told the site is under maintenance.

The model cannot ask anything, so nothing goes to a person on its own: no case, no handoff (a case goes to a person
only after the customer confirms the charge or accepts an agent). The reply is written by code, says to ask technical
service, ends the conversation, and claims nothing except a dispute the failed turn had already opened.
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
from minsky_api.tools.errors import ToolError

_HANDOFF = re.compile(r"HO-[0-9a-f]{12}")
_MAINTENANCE = "El sitio está en mantenimiento en este momento. Por favor, consulta con el servicio técnico."
_REQUEST = httpx2.Request("POST", "https://llm.invalid/v1/responses")


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
def test_model_failure_says_maintenance_and_creates_no_case(harness, failure):
    app, client, turn = harness
    turn.failure = failure
    response = _post(client, "No reconozco un cargo de 25.00 USD")
    assert response.status_code == 200, response.text
    reply = response.json()["messages"][-1]["agent"]
    assert reply.startswith(_MAINTENANCE), reply
    assert not _HANDOFF.search(reply)
    assert not app.state.cases.list_cases() and not app.state.cases.list_audit()


def test_reply_is_spanish_by_default_and_names_no_other_action(harness):
    _app, client, turn = harness
    turn.failure = openai.APITimeoutError(request=_REQUEST)
    reply = _post(client, "No reconozco un cargo").json()["messages"][-1]["agent"]
    assert "mantenimiento" in reply and "servicio técnico" in reply
    for claim in ("abrí", "reclamo abierto", "bloque", "reembols", "devol", "asesor", "especialista"):
        assert claim not in reply.lower()


def test_reply_follows_a_portuguese_message(harness):
    _app, client, turn = harness
    turn.failure = openai.APITimeoutError(request=_REQUEST)
    reply = _post(client, "Não reconheço uma cobrança de 25.00 USD").json()["messages"][-1]["agent"]
    assert "manutenção" in reply and "serviço técnico" in reply
    assert "nova conversa" in reply


def test_nothing_of_the_customer_text_is_stored_beyond_the_history(harness):
    app, client, turn = harness
    turn.failure = openai.APIConnectionError(request=_REQUEST)
    _post(client, "mi tarjeta termina en 4242 y vivo en la calle Falsa 123")
    assert not app.state.cases.list_cases() and not app.state.cases.list_audit()


def test_the_conversation_ends_and_keeps_progress_from_the_failed_turn(harness):
    app, client, turn = harness
    turn.failure = openai.APITimeoutError(request=_REQUEST)
    turn.partial_txn = "T-PARTIAL"  # the failed turn had already set this on the live state
    response = _post(client, "hola")
    state = app.state.conversations.get(UUID(response.json()["conversation_id"]))
    assert state.phase == Phase.DONE and state.terminal is not None and state.terminal.outcome == "unavailable"
    assert state.selected_txn_id == "T-PARTIAL"
    assert [role for role, _ in state.messages] == ["user", "agent"]


def test_a_dispute_opened_before_the_failure_is_reported_by_its_reference(harness):
    app, client, turn = harness
    turn.failure = openai.APITimeoutError(request=_REQUEST)
    turn.partial_txn = "T1"
    dispute = app.state.cases.create_dispute(customer_id="C1", transaction_id="T1", reason="unrecognized")
    response = _post(client, "hola")
    assert response.status_code == 200, response.text
    reply = response.json()["messages"][-1]["agent"]
    assert reply.startswith(f"Tu reclamo {dispute.dispute_id} ya quedó registrado. {_MAINTENANCE}")
    assert not app.state.cases.list_cases()  # the dispute is not a case here; no handoff was added


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
    assert texts[:3] == ["hola", "ok", "sigo aquí"] and texts[3].startswith(_MAINTENANCE)


def test_if_the_store_cannot_be_read_the_answer_is_an_honest_503(harness, monkeypatch):
    app, client, turn = harness
    turn.failure = openai.APITimeoutError(request=_REQUEST)
    turn.partial_txn = "T1"

    def broken(**_kwargs):
        raise RuntimeError("password=hunter2 connection refused")

    monkeypatch.setattr(app.state.cases, "get_dispute_by_transaction", broken)
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
    assert response.json()["messages"][-1]["agent"].startswith(_MAINTENANCE)


def test_auth_provider_errors_are_503_without_the_maintenance_message(harness):
    _app, client, turn = harness
    turn.failure = openai.AuthenticationError(
        "bad key",
        response=httpx2.Response(401, request=_REQUEST),
        body=None,
    )
    response = _post(client, "hola")
    assert response.status_code == 503
    # Config faults are not "maintenance" and must not invent a status for the customer.
    assert "mantenimiento" not in response.text


@pytest.mark.asyncio
async def test_closing_on_an_outage_twice_creates_nothing_and_says_the_same():
    from uuid import uuid4

    from minsky_api.agent.degraded import close_on_outage
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
    _, first = await close_on_outage(ctx, live, "hola", language="es")
    _, again = await close_on_outage(ctx, live, "hola", language="es")
    assert first == again and first.startswith(_MAINTENANCE)
    assert not cases.list_cases() and not cases.list_audit()
