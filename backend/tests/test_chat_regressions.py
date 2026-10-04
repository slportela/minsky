"""HTTP regressions: trusted identity and recoverable turns over the real orchestrator."""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from minsky_api.agent.state import ConversationState, Phase
from minsky_api.config import get_settings
from minsky_api.llm.client import LLMResult
from minsky_api.main import create_app
from minsky_api.tools.errors import ToolError


class _SpeechLLM:
    """The chat regressions patch the confirm tool. Reply wording still goes through the model API."""

    async def respond(self, instructions: str, messages: list[dict[str, str]], **kwargs: Any) -> LLMResult[Any]:
        import json

        schema = kwargs["schema"]
        payload = json.loads(messages[-1]["content"])
        facts = payload.get("facts") or {}
        parts = [str(value) for value in facts.values() if not isinstance(value, bool)]
        parsed = schema(
            act=payload["allowed"][0],
            text=" ".join(parts) or "es",
        )
        return LLMResult(
            text=parsed.model_dump_json(),
            parsed=parsed,
            model="gpt-6-luna",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
        )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "MINSKY_TEST_SESSIONS", '{"test-token":{"customer_id":"C1","expires_at":"2099-01-01T00:00:00Z"}}'
    )
    get_settings.cache_clear()

    @asynccontextmanager
    async def db():
        yield MagicMock()

    monkeypatch.setattr("minsky_api.api.chat.session", db)
    monkeypatch.setattr("minsky_api.api.chat.LLM", _SpeechLLM)
    with TestClient(create_app()) as http:
        yield http
    get_settings.cache_clear()


def test_customer_id_alone_does_not_authenticate(client):
    response = client.post(
        "/api/chat/turn", headers={"X-Minsky-Customer-Id": "C1"}, json={"messages": [{"user": "hola"}]}
    )
    assert response.status_code == 401


def test_simultaneous_replays_do_not_run_two_turns(client, monkeypatch):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    state = ConversationState(conversation_id=UUID("32345678-1234-4234-8234-123456789abc"), customer_id="C1")
    client.app.state.conversations.put(state)
    calls = 0

    async def slow_turn(state, text, ctx, llm):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        state.messages.extend([("user", text), ("agent", "confirmed")])
        return state, "confirmed"

    monkeypatch.setattr("minsky_api.api.chat.run_turn", slow_turn)
    body = {"conversation_id": str(state.conversation_id), "messages": [{"user": "sí"}]}

    def send():
        return client.post("/api/chat/turn", headers={"Authorization": "Bearer test-token"}, json=body).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send) for _ in range(2)]
        assert sorted(future.result() for future in futures) == [200, 409]
    assert calls == 1


def test_failed_policy_turn_can_be_retried(client, monkeypatch):
    state = ConversationState(
        conversation_id=UUID("12345678-1234-4234-8234-123456789abc"),
        customer_id="C1",
        phase=Phase.CONFIRM_TXN,
        selected_txn_id="T1",
        language="es",
        pending_question="¿Es este el cargo?",
    )
    client.app.state.conversations.put(state)
    calls = 0

    async def policy(ctx: Any, args: Any):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ToolError("transient")
        from minsky_api.tools.schemas import EvaluateDisputeResult

        return EvaluateDisputeResult(
            transaction_id="T1", rule_id="D09-eligible", route="open_dispute", offer_card_block=False
        )

    async def confirmed(ctx: Any, args: Any, llm: Any):
        from minsky_api.tools.schemas import ClassifyReplyResult

        return ClassifyReplyResult(decision="yes")

    monkeypatch.setattr("minsky_api.agent.orchestrator.classify_reply", confirmed)
    monkeypatch.setattr("minsky_api.agent.orchestrator.evaluate_dispute", policy)
    body = {"conversation_id": str(state.conversation_id), "messages": [{"user": "sí"}]}
    headers = {"Authorization": "Bearer test-token", "X-Minsky-Customer-Id": "C1"}
    assert client.post("/api/chat/turn", headers=headers, json=body).status_code == 502
    assert client.post("/api/chat/turn", headers=headers, json=body).status_code == 200
    assert calls == 2


def test_readback_failure_recovers_existing_dispute(client, monkeypatch):
    from minsky_api.tools.schemas import DisputeView, OpenDisputeResult

    state = ConversationState(
        conversation_id=UUID("22345678-1234-4234-8234-123456789abc"),
        customer_id="C1",
        phase=Phase.CONFIRM_ACT,
        selected_txn_id="T1",
        language="es",
        rule_id="D09-eligible",
        pending_question="¿Es este el cargo?",
    )
    client.app.state.conversations.put(state)
    cases = client.app.state.cases

    async def opened(ctx: Any, args: Any):
        record = cases.create_dispute(customer_id="C1", transaction_id="T1", reason="wrong_amount")
        return OpenDisputeResult(dispute=DisputeView.model_validate(record), created=True)

    calls = 0
    from minsky_api.tools.bank import get_dispute as real_get

    async def readback(ctx: Any, args: Any):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ToolError("readback failed after write")
        return await real_get(ctx, args)

    async def confirmed(ctx: Any, args: Any, llm: Any):
        from minsky_api.tools.schemas import ClassifyReplyResult

        return ClassifyReplyResult(decision="yes")

    monkeypatch.setattr("minsky_api.agent.orchestrator.classify_reply", confirmed)
    monkeypatch.setattr("minsky_api.agent.orchestrator.open_dispute", opened)
    monkeypatch.setattr("minsky_api.agent.orchestrator.get_dispute", readback)
    body = {"conversation_id": str(state.conversation_id), "messages": [{"user": "sí"}]}
    headers = {"Authorization": "Bearer test-token"}
    assert client.post("/api/chat/turn", headers=headers, json=body).status_code == 502
    first_record = cases.get_dispute_by_transaction(customer_id="C1", transaction_id="T1")
    response = client.post("/api/chat/turn", headers=headers, json=body)
    assert response.status_code == 200
    assert first_record.dispute_id in response.json()["messages"][-1]["agent"]
    assert cases.get_dispute_by_transaction(customer_id="C1", transaction_id="T1") == first_record
