"""Orchestrator state machine: happy path, clarify, budgets, fraud block, no false open."""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from minsky_api.agent.extract import DisputeDetails
from minsky_api.agent.orchestrator import run_turn
from minsky_api.agent.speak import Speech
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
        self.exec_statements: list[Any] = []

    async def get(self, model: type, identity: Any) -> Any:
        if isinstance(self.get_result, dict):
            return self.get_result.get(model)
        return self.get_result if isinstance(self.get_result, model) else None

    async def exec(self, statement: Any) -> _FakeResult:
        self.exec_statements.append(statement)
        return _FakeResult(self.exec_rows)


class FakeLLM:
    """Extract queue, plus an optional confirmation-decision queue.

    A queued decision is what the model said. It is independent of the customer words, so a test
    can prove the orchestrator follows the model.
    """

    def __init__(
        self,
        details: DisputeDetails | list[DisputeDetails],
        *,
        decisions: list[str | None] | None = None,
    ) -> None:
        self._queue = details if isinstance(details, list) else [details]
        self._decisions = list(decisions) if decisions is not None else None

    async def respond(self, *args: Any, schema: type | None = None, **kwargs: Any) -> LLMResult[Any]:
        if schema is not None and schema.__name__ == "Speech":
            import json

            payload = json.loads(str(args[1][-1]["content"]))
            facts = payload.get("facts") or {}
            parts = [str(value) for value in facts.values() if not isinstance(value, bool)]
            language = str(payload.get("language") or "es")
            parsed = schema(
                act=payload["allowed"][0],
                text=f"{language}: {' '.join(parts)}".strip(),
                claims_card_blocked=facts.get("card_blocked") is True,
            )
            return LLMResult(
                text=parsed.model_dump_json(),
                parsed=parsed,
                model="gpt-6-luna",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1.0,
            )
        if schema is not None and schema.__name__ == "Confirmation":
            decision = self._next_decision(args)
            parsed = None if decision is None else schema(decision=decision)
            return LLMResult(
                text="" if parsed is None else parsed.model_dump_json(),
                parsed=parsed,
                model="gpt-6-luna",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1.0,
            )
        details = self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]
        return LLMResult(
            text=details.model_dump_json(),
            parsed=details,
            model="gpt-6-luna",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
        )

    def _next_decision(self, args: tuple[Any, ...]) -> str | None:
        if self._decisions is not None:
            if not self._decisions:
                raise AssertionError("no confirmation decision queued")
            return self._decisions.pop(0)
        text = str(args[1][-1]["content"]).strip().rstrip(".!?").strip().casefold()
        if text in {"sí", "si", "sim", "yes"}:
            return "yes"
        if text in {"no", "não", "nao"}:
            return "no"
        return "unclear"


class _NthSpeech(FakeLLM):
    """One scripted speech on call `n` (1-based). Every other speech uses the fact stand-in."""

    def __init__(
        self,
        details: DisputeDetails,
        *,
        n: int,
        speech: Speech,
        decisions: list[str | None] | None = None,
        repeat: int = 2,
    ):
        super().__init__(details, decisions=decisions)
        self._n = 0
        self._target = n
        self._repeat = repeat  # 2 = the bounded retry fails too
        self._speech = speech

    async def respond(self, *args: Any, schema: type | None = None, **kwargs: Any) -> LLMResult[Any]:
        if schema is not None and schema.__name__ == "Speech":
            self._n += 1
            if self._target <= self._n < self._target + self._repeat:
                return LLMResult(
                    text=self._speech.model_dump_json(),
                    parsed=self._speech,
                    model="gpt-6-luna",
                    input_tokens=1,
                    output_tokens=1,
                    latency_ms=1.0,
                )
        return await super().respond(*args, schema=schema, **kwargs)


class _RaiseOnSpeech(FakeLLM):
    def __init__(self, details: DisputeDetails, *, fail_on: int) -> None:
        super().__init__(details)
        self._n = 0
        self._fail_on = fail_on

    async def respond(self, *args: Any, schema: type | None = None, **kwargs: Any) -> LLMResult[Any]:
        if schema is not None and schema.__name__ == "Speech":
            self._n += 1
            if self._n == self._fail_on:
                raise RuntimeError("compose_speech: bad act")
        return await super().respond(*args, schema=schema, **kwargs)


def _valid(customer_id: str = "C1") -> ToolSession:
    return ToolSession(session_id="s1", state=SessionState.VALID, customer_id=customer_id)


def _txn(
    *,
    transaction_id: str = "T1",
    amount_usd: str = "25.00",
    is_fraud: bool = False,
    merchant: str = "Cafe",
    status: str = "Approved",
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
        transaction_status=status,
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


def _state(customer_id: str = "C1") -> ConversationState:
    return ConversationState(conversation_id=uuid4(), customer_id=customer_id)


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


def test_asking_for_confirmation_stores_the_question():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details())
    state, reply = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_TXN
    assert state.pending_question == reply
    assert state.confirmation is None
    assert state.acts[-1] == "confirm_txn"
    assert "Cafe" in reply and "25.00 USD" in reply and "10 de junio de 2026" in reply
    assert "T1" not in reply  # internal ids stay out of customer text


def test_d09_open_and_read_back():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details())

    state, reply = asyncio.run(run_turn(state, "No reconozco el cargo en Cafe de 25", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_TXN
    assert "Cafe" in reply

    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_ACT
    assert state.acts[-1] == "confirm_open"
    assert "D09" not in reply  # the customer hears the reason, not the rule id
    assert "condiciones" in reply

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
    assert state.acts[-1] == "abort"


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
    assert "Cafe Sur" in reply


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
        assert "HO-" in reply
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
    assert state.acts[-1] == "offer_block"
    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE
    assert ctx.cases.get_card_block("P1") is not None
    assert any(a.tool == "create_handoff" and a.outcome == "ok" for a in ctx.cases.list_audit())
    assert state.claims_card_blocked is True
    assert "HO-" in reply


def test_escalate_amount_handoff_without_opening():
    txn = _txn(amount_usd="900.00")
    ctx = _ctx(txn, exec_rows=[txn])
    state = _state()
    llm = FakeLLM(_details(amount=Decimal("900.00")))
    state, _ = asyncio.run(run_turn(state, "Cargo de 900", ctx, llm))  # type: ignore[arg-type]
    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE
    assert state.pending_question is None
    assert not any(a.tool == "open_dispute" and a.outcome == "ok" for a in ctx.cases.list_audit())
    assert any(a.tool == "create_handoff" and a.outcome == "ok" for a in ctx.cases.list_audit())
    assert state.acts[-1] == "handoff"
    assert "HO-" in reply


def test_settings_defaults():
    get_settings.cache_clear()
    settings = Settings()
    assert settings.max_turns == 12
    assert settings.max_clarify_attempts == 2


def test_empty_extract_does_not_list_latest_txns():
    """No merchant/amount/date/id → clarify, never an unfiltered list dump."""
    t1 = _txn(transaction_id="T1")
    t2 = _txn(transaction_id="T2", merchant="Other")
    ctx = _ctx(t1, exec_rows=[t1, t2])
    state = _state()
    llm = FakeLLM(_details(merchant=None, amount=None, transaction_id=None))
    state, reply = asyncio.run(run_turn(state, "hola quiero algo", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CLARIFY
    assert "T1" not in reply and "T2" not in reply
    assert ctx.db.exec_statements == []  # type: ignore[attr-defined]
    assert state.acts[-1] == "clarify"


def test_card_offer_without_product_does_not_claim_block():
    txn = _txn(is_fraud=True)
    ctx = _ctx(txn)
    state = _state()
    llm = FakeLLM(_details(customer_says_not_me=True))
    state, _ = asyncio.run(run_turn(state, "No fui yo", ctx, llm))  # type: ignore[arg-type]
    state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CARD_OFFER
    state.selected_product_id = None
    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE
    assert ctx.cases.get_card_block("P1") is None
    assert "bloqueé" not in reply.lower()
    assert any(a.tool == "create_handoff" and a.outcome == "ok" for a in ctx.cases.list_audit())


def test_model_yes_on_a_hedged_reply_asks_again():
    """The deterministic floor: a model 'yes' on a reply that also says no or 'but' never acts."""
    for text in ("no", "sí, pero mejor no", "sim, mas espere"):
        ctx = _ctx()
        state = _state()
        llm = FakeLLM(_details(), decisions=["yes"])
        state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
        assert state.phase == Phase.CONFIRM_TXN
        state, _ = asyncio.run(run_turn(state, text, ctx, llm))  # type: ignore[arg-type]
        assert state.phase == Phase.CONFIRM_TXN
        assert state.confirmation == "unclear"
        assert state.acts[-1] == "ask_again"
        assert not any(a.tool in ("open_dispute", "evaluate_dispute") for a in ctx.cases.list_audit())


def test_model_yes_on_a_plain_yes_still_confirms():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details(), decisions=["yes"])
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    state, _ = asyncio.run(run_turn(state, "Sí, ese mismo", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_ACT


def test_confirm_turn_classifies_before_any_other_tool():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details(), decisions=["yes"])
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    assert state.pending_question
    audit_len = len(ctx.cases.list_audit())
    state, _ = asyncio.run(run_turn(state, "no", ctx, llm))  # type: ignore[arg-type]
    tools = [row.tool for row in ctx.cases.list_audit()]
    assert tools[audit_len] == "classify_reply"
    assert "open_dispute" not in tools
    assert "block_card" not in tools
    assert state.confirmation == "unclear"
    assert state.pending_question


def test_blank_confirmation_stays_in_phase_and_opens_nothing():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details(), decisions=[None])
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_TXN
    assert state.confirmation == "unclear"
    assert state.acts[-1] == "ask_again"
    assert not any(a.tool == "open_dispute" for a in ctx.cases.list_audit())


def test_confirm_without_a_stored_question_does_not_act():
    ctx = _ctx()
    state = _state()
    state.phase = Phase.CONFIRM_ACT
    state.language = "es"
    state.pending_question = None
    state.selected_txn_id = "T1"
    state.selected_product_id = "P1"
    state.rule_id = "D09-eligible"
    state.route = "open_dispute"
    llm = FakeLLM(_details(), decisions=["yes"])
    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    tools = [row.tool for row in ctx.cases.list_audit()]
    assert state.confirmation == "unclear"
    assert "classify_reply" not in tools
    assert "open_dispute" not in tools
    assert "block_card" not in tools
    assert state.acts[-1] == "ask_again"
    assert state.phase == Phase.CONFIRM_ACT


def test_lone_y_is_not_confirmation():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details())
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_TXN
    state, reply = asyncio.run(run_turn(state, "y", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_TXN
    assert state.acts[-1] == "ask_again"


def test_customer_mismatch_denied():
    ctx = _ctx()
    state = ConversationState(conversation_id=uuid4(), customer_id="OTHER")
    llm = FakeLLM(_details())
    with pytest.raises(PermissionError, match="conversation_customer_mismatch"):
        asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]


def test_clarify_exhausted_handoff(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("MINSKY_MAX_CLARIFY_ATTEMPTS", "1")
    get_settings.cache_clear()
    try:
        ctx = _ctx(exec_rows=[])
        state = _state()
        llm = FakeLLM(_details(merchant="Nope", amount=None))
        state, _ = asyncio.run(run_turn(state, "Nope", ctx, llm))  # type: ignore[arg-type]
        assert state.phase == Phase.CLARIFY
        state, reply = asyncio.run(run_turn(state, "sigue sin aparecer", ctx, llm))  # type: ignore[arg-type]
        assert state.phase == Phase.DONE
        assert any(a.tool == "create_handoff" and a.outcome == "ok" for a in ctx.cases.list_audit())
        assert "HO-" in reply
    finally:
        monkeypatch.delenv("MINSKY_MAX_CLARIFY_ATTEMPTS", raising=False)
        get_settings.cache_clear()


def test_model_cannot_handoff_instead_of_confirming():
    ctx = _ctx()
    state = _state()
    llm = _NthSpeech(_details(), n=1, speech=Speech(act="handoff", text="Quiero una persona."))
    with pytest.raises(RuntimeError, match="not allowed"):
        asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    assert not any(row.tool == "create_handoff" for row in ctx.cases.list_audit())


def test_unverified_block_sentence_does_not_send_or_act():
    ctx = _ctx()
    state = _state()
    llm = _NthSpeech(
        _details(),
        n=1,
        speech=Speech(
            act="confirm_txn",
            text="Bloqueé la tarjeta. Cafe 25.00 USD 10 de junio de 2026",
            claims_card_blocked=False,
        ),
    )
    with pytest.raises(RuntimeError, match="unverified"):
        asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    tools = [row.tool for row in ctx.cases.list_audit()]
    assert "open_dispute" not in tools
    assert "block_card" not in tools
    assert "create_handoff" not in tools


def test_inform_route_does_not_let_the_model_create_a_handoff():
    txn = _txn(status="Declined")
    ctx = _ctx(txn, exec_rows=[txn])
    state = _state()
    llm = _NthSpeech(
        _details(),
        n=2,
        speech=Speech(act="handoff", text="Quiero una persona."),
        decisions=["yes"],
    )
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_TXN
    with pytest.raises(RuntimeError, match="not allowed"):
        asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert not any(row.tool == "create_handoff" for row in ctx.cases.list_audit())


def test_speech_failure_after_open_reports_the_dispute_id():
    ctx = _ctx()
    state = _state()
    llm = _RaiseOnSpeech(_details(), fail_on=3)
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE
    assert "DSP-" in reply
    assert any(row.tool == "open_dispute" and row.outcome == "ok" for row in ctx.cases.list_audit())


def test_d04_inform_includes_existing_dispute_ref():
    from evals.runner import _scripted_speech

    speech = _scripted_speech(
        '{"language":"es","allowed":["inform","handoff"],"facts":{"existing_dispute_id":"DSP-abc","rule_id":"D04-already-disputed"}}'
    )
    assert speech.act == "inform"
    assert "DSP-abc" in speech.text


def test_clarification_keeps_merchant_and_replaces_amount():
    from minsky_api.agent.orchestrator import _merge_details

    state = _state()
    _merge_details(state, _details(merchant="Cafe", amount=None))
    merged = _merge_details(state, _details(merchant=None, amount=Decimal("25")))
    assert merged.merchant == "Cafe"
    assert merged.amount == Decimal("25")
    merged = _merge_details(state, _details(merchant=None, amount=Decimal("30")))
    assert merged.merchant == "Cafe" and merged.amount == Decimal("30")
    merged = _merge_details(state, _details(merchant="Other", amount=None, reset_search=True))
    assert merged.merchant == "Other" and merged.amount is None


def test_copy_store_does_not_commit_failed_mutations():
    from minsky_api.agent.memory import ConversationStore

    store = ConversationStore()
    state = _state()
    store.put(state)
    state.messages.append(("user", "external mutation"))
    copy = store.get(state.conversation_id)
    assert copy is not None and copy.messages == []
    copy.messages.append(("user", "failed turn"))
    unchanged = store.get(state.conversation_id)
    assert unchanged is not None and unchanged.messages == []


async def test_search_miss_keeps_filters_when_amount_is_corrected():
    from datetime import date

    from evals.fixtures import FixtureBank

    bank = FixtureBank([_txn(), _txn(transaction_id="T2", merchant="Other")])
    ctx = ToolContext(session=_valid(), db=bank, cases=InMemoryCasesBackend())  # type: ignore[arg-type]
    state = _state()
    first = _details(amount=Decimal("50"), date_from=date(2026, 6, 1), date_to=date(2026, 6, 15))
    llm = FakeLLM([first, _details(merchant=None, amount=Decimal("25"))])
    try:
        state, reply = await run_turn(state, "Cafe, 50", ctx, llm)  # type: ignore[arg-type]
        assert state.acts[-1] == "clarify"
        assert state.search_details == first
        state, reply = await run_turn(state, "Era de 25", ctx, llm)  # type: ignore[arg-type]
        assert state.phase == Phase.CONFIRM_TXN
        assert state.selected_txn_id == "T1"
        assert state.search_details.merchant == "Cafe"
        assert state.search_details.date_from == first.date_from
        assert state.search_details.date_to == first.date_to
        assert state.search_details.amount == Decimal("25")
    finally:
        bank.close()


async def test_missing_merchant_does_not_select_another_merchant_on_amount_followup():
    from evals.fixtures import FixtureBank

    bank = FixtureBank([_txn(merchant="Other")])
    ctx = ToolContext(session=_valid(), db=bank, cases=InMemoryCasesBackend())  # type: ignore[arg-type]
    llm = FakeLLM([_details(amount=None), _details(merchant=None, amount=Decimal("25"))])
    try:
        state, _ = await run_turn(_state(), "Un cargo de Cafe", ctx, llm)  # type: ignore[arg-type]
        state, reply = await run_turn(state, "Era de 25", ctx, llm)  # type: ignore[arg-type]
        assert state.phase == Phase.CLARIFY
        assert state.selected_txn_id is None
        assert state.acts[-1] == "clarify"
        assert all(row.tool != "open_dispute" for row in ctx.cases.list_audit())
    finally:
        bank.close()


class _FixedLanguage:
    def __init__(self, code: str) -> None:
        self.code = code
        self.calls: list[str] = []

    def detect(self, text: str) -> str:
        self.calls.append(text)
        return self.code


def test_detector_runs_once_and_sim_keeps_portuguese():
    ctx = _ctx()
    state = _state()
    detector = _FixedLanguage("pt")
    llm = FakeLLM(_details())
    opening = "Quiero disputar un cargo en Cafe de 25"
    state, reply = asyncio.run(run_turn(state, opening, ctx, llm, detector=detector))  # type: ignore[arg-type]
    assert state.language == "pt"
    assert reply.startswith("pt:")
    assert detector.calls == [opening]
    state, reply = asyncio.run(run_turn(state, "sim", ctx, llm, detector=detector))  # type: ignore[arg-type]
    assert detector.calls == [opening]
    assert state.phase == Phase.CONFIRM_ACT
    assert reply.startswith("pt:")


def test_spanish_first_message_keeps_spanish_phrases():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details())
    state, reply = asyncio.run(run_turn(state, "Quiero disputar un cargo en Cafe de 25", ctx, llm))  # type: ignore[arg-type]
    assert state.language == "es"
    assert reply.startswith("es:")


def test_quero_opener_replies_in_portuguese_and_sim_confirms():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details())
    state, reply = asyncio.run(run_turn(state, "Quero disputar uma cobrança de 25 dólares no Cafe", ctx, llm))  # type: ignore[arg-type]
    assert state.language == "pt"
    assert reply.startswith("pt:")
    state, reply = asyncio.run(run_turn(state, "sim", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_ACT
    assert reply.startswith("pt:")


def test_one_bad_reply_is_retried_and_never_sent():
    ctx = _ctx()
    state = _state()
    bad = Speech(act="confirm_txn", text="Bloqueé la tarjeta. Cafe 25.00 USD 10 de junio de 2026")
    llm = _NthSpeech(_details(), n=1, speech=bad, repeat=1)
    state, reply = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_TXN
    assert "Bloqueé" not in reply
    assert not any(row.tool in ("block_card", "create_handoff") for row in ctx.cases.list_audit())
