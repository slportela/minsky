"""Orchestrator state machine: happy path, clarify, budgets, fraud block, no false open."""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from minsky_api.agent.extract import DisputeDetails
from minsky_api.agent.orchestrator import run_turn
from minsky_api.agent.state import ConversationState, Phase
from minsky_api.config import Settings, get_settings
from minsky_api.identity import SessionState, ToolSession
from minsky_api.llm.client import LLMResult
from minsky_api.store.cases_memory import InMemoryCasesBackend
from minsky_api.store.models import CustomerComplaintStats, Product, Transaction
from minsky_api.tools.context import ToolContext


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    def __init__(self, *, get_result: Any = None, exec_rows: list[Any] | None = None) -> None:
        self.get_result = get_result
        self.exec_rows = exec_rows or []

    async def get(self, model: type, identity: Any) -> Any:
        if isinstance(self.get_result, dict):
            return self.get_result.get(model)
        return self.get_result if isinstance(self.get_result, model) else None

    async def exec(self, statement: Any) -> _FakeResult:
        return _FakeResult(self.exec_rows)


class FakeLLM:
    def __init__(self, details: DisputeDetails | list[DisputeDetails]) -> None:
        self._queue = details if isinstance(details, list) else [details]

    async def respond(self, *args: Any, schema: type | None = None, **kwargs: Any) -> LLMResult[Any]:
        details = self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]
        return LLMResult(
            text=details.model_dump_json(),
            parsed=details,
            model="gpt-6-luna",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
        )


def _valid(customer_id: str = "C1") -> ToolSession:
    return ToolSession(session_id="s1", state=SessionState.VALID, customer_id=customer_id)


def _txn(
    *,
    transaction_id: str = "T1",
    amount_usd: str = "25.00",
    is_fraud: bool = False,
    merchant: str = "Cafe",
) -> Transaction:
    return Transaction(
        transaction_id=transaction_id,
        customer_id="C1",
        product_id="P1",
        amount=Decimal(amount_usd),
        currency="USD",
        amount_usd=Decimal(amount_usd),
        amount_usd_source="native_usd",
        transaction_date=datetime(2026, 6, 10, 9, 30),
        merchant_name=merchant,
        transaction_status="Approved",
        is_fraud=is_fraud,
        fraud_score=Decimal("95.00") if is_fraud else Decimal("10.00"),
    )


def _stats(*, repeat: bool = False) -> CustomerComplaintStats:
    return CustomerComplaintStats(customer_id="C1", is_repeat_complainer=repeat)


def _card() -> Product:
    return Product(
        product_id="P1",
        customer_id="C1",
        is_card=True,
        product_number_last4="1234",
        product_status="Active",
    )


def _ctx(txn: Transaction | None = None, *, exec_rows: list[Any] | None = None, repeat: bool = False) -> ToolContext:
    row = txn or _txn()
    return ToolContext(
        session=_valid(),
        db=FakeSession(  # type: ignore[arg-type]
            get_result={Transaction: row, CustomerComplaintStats: _stats(repeat=repeat), Product: _card()},
            exec_rows=exec_rows if exec_rows is not None else [row],
        ),
        cases=InMemoryCasesBackend(),
    )


def _state() -> ConversationState:
    return ConversationState(conversation_id=uuid4())


def _details(**kwargs: Any) -> DisputeDetails:
    base: dict[str, Any] = {
        "out_of_scope": False,
        "merchant": "Cafe",
        "amount": Decimal("25.00"),
        "customer_says_not_me": False,
        "transaction_id": None,
    }
    base.update(kwargs)
    return DisputeDetails(**base)


def test_d09_open_and_read_back():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details())

    state, reply = asyncio.run(run_turn(state, "No reconozco el cargo en Cafe de 25", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_TXN
    assert "T1" in reply

    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_ACT
    assert "abrir" in reply.lower()

    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE
    assert any(a.tool == "open_dispute" and a.outcome == "ok" for a in ctx.cases.list_audit())
    assert "DSP-" in reply


def test_no_false_open_without_confirm():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details())
    state, _ = asyncio.run(run_turn(state, "Cargo Cafe 25", ctx, llm))  # type: ignore[arg-type]
    state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_ACT
    state, reply = asyncio.run(run_turn(state, "no", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE
    assert not any(a.tool == "open_dispute" and a.outcome == "ok" for a in ctx.cases.list_audit())
    assert "cambio" in reply.lower() or "no haré" in reply.lower()


def test_clarify_many_then_pick():
    t1 = _txn(transaction_id="T1", merchant="Cafe")
    t2 = _txn(transaction_id="T2", merchant="Cafe Sur")
    chosen = {"row": t1}

    class SwitchingSession(FakeSession):
        async def get(self, model: type, identity: Any) -> Any:
            if model is Transaction:
                return chosen["row"] if identity == chosen["row"].transaction_id else None
            return await super().get(model, identity)

    ctx = ToolContext(
        session=_valid(),
        db=SwitchingSession(  # type: ignore[arg-type]
            get_result={CustomerComplaintStats: _stats(), Product: _card()},
            exec_rows=[t1, t2],
        ),
        cases=InMemoryCasesBackend(),
    )
    state = _state()
    llm = FakeLLM(_details(merchant="Cafe", amount=None))
    state, reply = asyncio.run(run_turn(state, "Cafe", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CLARIFY
    assert "1." in reply and "2." in reply

    chosen["row"] = t2
    state, reply = asyncio.run(run_turn(state, "2", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_TXN
    assert "T2" in reply


def test_max_turns_handoff(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("MINSKY_MAX_TURNS", "2")
    get_settings.cache_clear()
    try:
        ctx = _ctx()
        state = _state()
        llm = FakeLLM(_details())
        state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
        state, _ = asyncio.run(run_turn(state, "tal vez", ctx, llm))  # type: ignore[arg-type]
        state, reply = asyncio.run(run_turn(state, "tal vez", ctx, llm))  # type: ignore[arg-type]
        assert state.phase == Phase.DONE
        assert any(a.tool == "create_handoff" and a.outcome == "ok" for a in ctx.cases.list_audit())
        assert "límite" in reply.lower() or "asesor" in reply.lower()
    finally:
        monkeypatch.delenv("MINSKY_MAX_TURNS", raising=False)
        get_settings.cache_clear()


def test_fraud_card_block_then_handoff():
    txn = _txn(is_fraud=True)
    ctx = _ctx(txn)
    state = _state()
    llm = FakeLLM(_details(customer_says_not_me=True))
    state, _ = asyncio.run(run_turn(state, "No fui yo en Cafe", ctx, llm))  # type: ignore[arg-type]
    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CARD_OFFER
    assert "bloque" in reply.lower()
    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE
    assert ctx.cases.get_card_block("P1") is not None
    assert any(a.tool == "create_handoff" and a.outcome == "ok" for a in ctx.cases.list_audit())
    assert "fraude" in reply.lower() or "bloque" in reply.lower()


def test_escalate_amount_handoff_without_opening():
    txn = _txn(amount_usd="900.00")
    ctx = _ctx(txn, exec_rows=[txn])
    state = _state()
    llm = FakeLLM(_details(amount=Decimal("900.00")))
    state, _ = asyncio.run(run_turn(state, "Cargo de 900", ctx, llm))  # type: ignore[arg-type]
    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE
    assert not any(a.tool == "open_dispute" and a.outcome == "ok" for a in ctx.cases.list_audit())
    assert any(a.tool == "create_handoff" and a.outcome == "ok" for a in ctx.cases.list_audit())
    assert "asesor" in reply.lower() or "derivo" in reply.lower()


def test_settings_defaults():
    get_settings.cache_clear()
    settings = Settings()
    assert settings.max_turns == 12
    assert settings.max_clarify_attempts == 2
