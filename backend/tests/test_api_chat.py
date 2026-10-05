"""HTTP chat turn: POC customer header, ownership, history checks."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock
from uuid import UUID, uuid4

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


def test_runtime_failure_hides_the_internal_reason(app_and_client, monkeypatch):
    _app, client = app_and_client

    async def boom(state, text, ctx, llm):
        raise RuntimeError("compose_speech: unverified card block")

    monkeypatch.setattr("minsky_api.api.chat.run_turn", boom)
    response = client.post(
        "/api/chat/turn",
        json={"messages": [{"user": "hola"}]},
        headers={"Authorization": "Bearer token-c1"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"
    assert "compose_speech" not in response.text
    assert "unverified" not in response.text


def test_a_new_conversation_runs_the_mode_chosen_at_its_start(app_and_client, monkeypatch):
    app, client = app_and_client

    async def fake_agentic_turn(state, text, ctx, llm):
        state.phase = Phase.DONE
        state.messages.append(("user", text))
        state.messages.append(("agent", "ok-agentic"))
        return state, "ok-agentic"

    monkeypatch.setattr("minsky_api.api.chat.run_agentic_turn", fake_agentic_turn)
    headers = {"Authorization": "Bearer token-c1"}

    monkeypatch.setenv("MINSKY_AGENT_MODE", "agentic")
    get_settings.cache_clear()
    agentic = client.post("/api/chat/turn", json={"messages": [{"user": "hola"}]}, headers=headers)
    assert agentic.json()["messages"][-1] == {"agent": "ok-agentic"}
    stored = app.state.conversations.get(UUID(agentic.json()["conversation_id"]))
    assert stored is not None and stored.mode == "agentic"

    # flipping the setting does not move a running conversation into the other mode's phases
    monkeypatch.setenv("MINSKY_AGENT_MODE", "workflow")
    get_settings.cache_clear()
    workflow = client.post("/api/chat/turn", json={"messages": [{"user": "hola"}]}, headers=headers)
    assert workflow.json()["messages"][-1] == {"agent": "ok-poc"}


# ---------------------------------------------------------------- the flow can be chosen, if the server allows


@pytest.fixture
def two_flows(app_and_client, monkeypatch):
    """The two flows answer differently, so a test sees which one ran. The switch is set per test."""
    app, client = app_and_client

    async def fake_agentic_turn(state, text, ctx, llm):
        state.messages.append(("user", text))
        state.messages.append(("agent", "ok-agentic"))
        return state, "ok-agentic"

    async def fake_workflow_turn(state, text, ctx, llm):
        state.messages.append(("user", text))
        state.messages.append(("agent", "ok-workflow"))
        return state, "ok-workflow"

    monkeypatch.setattr("minsky_api.api.chat.run_agentic_turn", fake_agentic_turn)
    monkeypatch.setattr("minsky_api.api.chat.run_turn", fake_workflow_turn)

    def configure(*, switch: bool, default: str = "workflow") -> None:
        monkeypatch.setenv("MINSKY_ALLOW_MODE_SWITCH", "true" if switch else "false")
        monkeypatch.setenv("MINSKY_AGENT_MODE", default)
        get_settings.cache_clear()

    return app, client, configure


_AUTH = {"Authorization": "Bearer token-c1"}


def _post(client, messages, **extra):
    return client.post("/api/chat/turn", json={"messages": messages, **extra}, headers=_AUTH)


def test_the_options_say_whether_the_flow_can_be_chosen_and_the_servers_default(two_flows):
    _app, client, configure = two_flows
    configure(switch=False)
    assert client.get("/api/chat/options").json() == {"mode_switch": False, "mode": "workflow"}
    configure(switch=True, default="agentic")
    assert client.get("/api/chat/options").json() == {"mode_switch": True, "mode": "agentic"}


def test_with_the_switch_off_a_client_cannot_choose_the_flow(two_flows):
    app, client, configure = two_flows
    configure(switch=False)
    response = _post(client, [{"user": "hola"}], mode="agentic")
    assert response.status_code == 200
    body = response.json()
    assert body["messages"][-1] == {"agent": "ok-workflow"} and body["mode"] == "workflow"
    stored = app.state.conversations.get(UUID(body["conversation_id"]))
    assert stored is not None and stored.mode == "workflow"


def test_with_the_switch_on_a_client_chooses_the_flow_of_a_new_conversation(two_flows):
    app, client, configure = two_flows
    configure(switch=True)
    agentic = _post(client, [{"user": "hola"}], mode="agentic").json()
    assert agentic["messages"][-1] == {"agent": "ok-agentic"} and agentic["mode"] == "agentic"
    workflow = _post(client, [{"user": "hola"}], mode="workflow").json()
    assert workflow["messages"][-1] == {"agent": "ok-workflow"} and workflow["mode"] == "workflow"


def test_with_the_switch_on_but_no_mode_sent_the_servers_default_applies(two_flows):
    _app, client, configure = two_flows
    configure(switch=True, default="agentic")
    body = _post(client, [{"user": "hola"}]).json()
    assert body["mode"] == "agentic" and body["messages"][-1] == {"agent": "ok-agentic"}


def test_a_conversation_keeps_its_flow_whatever_a_later_turn_asks_for(two_flows):
    app, client, configure = two_flows
    configure(switch=True)
    first = _post(client, [{"user": "hola"}], mode="agentic").json()
    second = _post(
        client,
        [{"user": "hola"}, {"agent": "ok-agentic"}, {"user": "sigo"}],
        conversation_id=first["conversation_id"],
        mode="workflow",  # a later turn cannot move the conversation to the other flow
    ).json()
    assert second["mode"] == "agentic" and second["messages"][-1] == {"agent": "ok-agentic"}
    stored = app.state.conversations.get(UUID(first["conversation_id"]))
    assert stored is not None and stored.mode == "agentic"


def test_a_flow_that_does_not_exist_is_refused(two_flows):
    _app, client, configure = two_flows
    configure(switch=True)
    response = _post(client, [{"user": "hola"}], mode="turbo")
    assert response.status_code in (400, 422)


def test_the_customer_still_comes_from_the_credential_whatever_the_flow(two_flows):
    """The flow is not a permission: choosing one never changes whose records a conversation reads."""
    app, client, configure = two_flows
    configure(switch=True)
    body = _post(client, [{"user": "hola"}], mode="agentic").json()
    stored = app.state.conversations.get(UUID(body["conversation_id"]))
    assert stored is not None and stored.customer_id == "C1"
