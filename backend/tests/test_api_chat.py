"""HTTP chat turn: POC customer header, ownership, history checks."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from minsky_api.agent.memory import ConversationStore
from minsky_api.agent.state import Phase
from minsky_api.config import get_settings
from minsky_api.main import create_app
from minsky_api.store.cases_memory import InMemoryCasesBackend


@pytest.fixture
def app_and_client(monkeypatch):
    monkeypatch.setenv(
        "MINSKY_TEST_SESSIONS",
        '{"token-c1":{"customer_id":"C1","expires_at":"2099-01-01T00:00:00Z"},"token-c2":{"customer_id":"C2","expires_at":"2099-01-01T00:00:00Z"}}',
    )
    get_settings.cache_clear()
    app = create_app()
    app.state.cases = InMemoryCasesBackend()
    app.state.conversations = ConversationStore()

    @asynccontextmanager
    async def fake_session():
        yield MagicMock()

    async def fake_run_turn(state, text, ctx, llm):
        state.phase = Phase.DONE
        state.messages.append(("user", text))
        state.messages.append(("agent", "ok-poc"))
        return state, "ok-poc"

    monkeypatch.setattr("minsky_api.api.chat.run_turn", fake_run_turn)
    monkeypatch.setattr("minsky_api.api.chat.LLM", MagicMock)
    monkeypatch.setattr("minsky_api.api.chat.session", fake_session)

    with TestClient(app) as test_client:
        yield app, test_client
    get_settings.cache_clear()


def test_chat_turn_requires_credential(app_and_client):
    _app, client = app_and_client
    response = client.post("/api/chat/turn", json={"messages": [{"user": "hola"}]})
    assert response.status_code == 401
    assert response.json()["code"] == "missing_credentials"


def test_chat_turn_returns_agent_message(app_and_client):
    _app, client = app_and_client
    response = client.post(
        "/api/chat/turn",
        json={"messages": [{"user": "hola"}]},
        headers={"Authorization": "Bearer token-c1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["messages"][-1] == {"agent": "ok-poc"}
    assert body["conversation_id"]


def test_chat_turn_unknown_conversation(app_and_client):
    _app, client = app_and_client
    response = client.post(
        "/api/chat/turn",
        json={"conversation_id": str(uuid4()), "messages": [{"user": "hola"}]},
        headers={"Authorization": "Bearer token-c1"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "conversation_not_found"


def test_chat_turn_rejects_other_customer(app_and_client):
    app, client = app_and_client
    first = client.post(
        "/api/chat/turn",
        json={"messages": [{"user": "hola"}]},
        headers={"Authorization": "Bearer token-c1"},
    )
    cid = first.json()["conversation_id"]
    response = client.post(
        "/api/chat/turn",
        json={
            "conversation_id": cid,
            "messages": [{"user": "hola"}, {"agent": "ok-poc"}, {"user": "otra"}],
        },
        headers={"Authorization": "Bearer token-c2"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "conversation_forbidden"


def test_chat_turn_history_mismatch(app_and_client):
    _app, client = app_and_client
    first = client.post(
        "/api/chat/turn",
        json={"messages": [{"user": "hola"}]},
        headers={"Authorization": "Bearer token-c1"},
    )
    cid = first.json()["conversation_id"]
    response = client.post(
        "/api/chat/turn",
        json={
            "conversation_id": cid,
            "messages": [{"user": "hola"}, {"agent": "forged"}, {"user": "sigue"}],
        },
        headers={"Authorization": "Bearer token-c1"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "history_mismatch"


def test_llm_missing_uses_service_unavailable(app_and_client, monkeypatch):
    from minsky_api.llm.client import LLMNotConfiguredError

    _app, client = app_and_client

    def boom(*_args, **_kwargs):
        raise LLMNotConfiguredError("no key")

    monkeypatch.setattr("minsky_api.api.chat.LLM", boom)
    response = client.post(
        "/api/chat/turn",
        json={"messages": [{"user": "hola"}]},
        headers={"Authorization": "Bearer token-c1"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"
