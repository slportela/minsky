"""HTTP chat turn: POC customer header and conversation wiring."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from minsky_api.agent.memory import ConversationStore
from minsky_api.agent.state import Phase
from minsky_api.main import create_app
from minsky_api.store.cases_memory import InMemoryCasesBackend


@pytest.fixture
def client(monkeypatch):
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
        yield test_client


def test_chat_turn_requires_customer_header(client):
    response = client.post("/api/chat/turn", json={"messages": [{"user": "hola"}]})
    assert response.status_code == 401
    assert response.json()["code"] == "missing_credentials"


def test_chat_turn_returns_agent_message(client):
    response = client.post(
        "/api/chat/turn",
        json={"messages": [{"user": "hola"}]},
        headers={"X-Minsky-Customer-Id": "C1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["messages"][-1] == {"agent": "ok-poc"}
    assert body["conversation_id"]


def test_chat_turn_unknown_conversation(client):
    response = client.post(
        "/api/chat/turn",
        json={"conversation_id": str(uuid4()), "messages": [{"user": "hola"}]},
        headers={"X-Minsky-Customer-Id": "C1"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "conversation_not_found"
