"""Orchestrator state machine: happy path, clarify, budgets, fraud block, no false open."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from minsky_api.agent.extract import DisputeDetails
from minsky_api.agent.orchestrator import _candidate_list, run_turn
from minsky_api.agent.speak import Speech
from minsky_api.agent.state import ConversationState, Phase
from minsky_api.agent.wording import clarify_fallback, handoff_offer_declined, handoff_offer_question, safe_sentence
from minsky_api.config import Settings, get_settings
from minsky_api.identity import SessionState, ToolSession
from minsky_api.llm.client import LLMNotConfiguredError, LLMResult
from minsky_api.store.cases_memory import InMemoryCasesBackend
from minsky_api.store.models import CustomerComplaintStats, Product, Transaction
from minsky_api.tools.context import ToolContext
from minsky_api.tools.schemas import TransactionView


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


_NTH_INSTANCES: list[Any] = []


@pytest.fixture(autouse=True)
def _every_scripted_speech_is_reached():
    """A scripted speech on call n that never happens means the test passes without testing what it says.

    The call order changes whenever a step stops using the model (the transaction question did), and the
    tests that count calls silently stop injecting. This turns that into a failure.
    """
    _NTH_INSTANCES.clear()
    yield
    for llm in _NTH_INSTANCES:
        assert llm.injected == llm._repeat, (
            f"the scripted speech on model call {llm._target} was reached {llm.injected} of {llm._repeat} times: "
            "the call order changed and this test no longer tests what it says"
        )


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
        self.injected = 0
        _NTH_INSTANCES.append(self)

    async def respond(self, *args: Any, schema: type | None = None, **kwargs: Any) -> LLMResult[Any]:
        if schema is not None and schema.__name__ == "Speech":
            self._n += 1
            if self._target <= self._n < self._target + self._repeat:
                self.injected += 1
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


def test_max_turns_before_a_confirmed_charge_ends_without_a_case(monkeypatch):
    """No case goes to a person unless the customer confirmed the charge or asked for an agent."""
    monkeypatch.setenv("MINSKY_MAX_TURNS", "2")
    get_settings.cache_clear()
    try:
        ctx = _ctx()
        state = _state()
        llm = FakeLLM(_details())
        state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
        state, _ = asyncio.run(run_turn(state, "tal vez", ctx, llm))  # type: ignore[arg-type]
        state, reply = asyncio.run(run_turn(state, "tal vez", ctx, llm))  # type: ignore[arg-type]
        assert state.phase == Phase.DONE and state.terminal is not None and state.terminal.outcome == "no_case"
        assert not any(a.tool == "create_handoff" for a in ctx.cases.list_audit())
        assert not ctx.cases.list_cases()
        assert reply.startswith("No abrí ningún caso ni te pasé con nadie.")
    finally:
        monkeypatch.delenv("MINSKY_MAX_TURNS", raising=False)
        get_settings.cache_clear()


def test_max_turns_after_the_customer_confirmed_the_charge_still_hands_off(monkeypatch):
    monkeypatch.setenv("MINSKY_MAX_TURNS", "2")
    get_settings.cache_clear()
    try:
        ctx = _ctx()
        state = _state()
        llm = FakeLLM(_details())
        state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
        state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
        assert state.txn_confirmed
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
    assert settings.max_unclear_replies == 3


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
    """Consent is code's: a model 'yes' on a reply that is not an explicit yes never acts."""
    for text in ("sí, pero mejor no", "sim, mas espere", "no, gracias"):
        ctx = _ctx()
        state = _state()
        llm = FakeLLM(_details(), decisions=["yes"])
        state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
        assert state.phase == Phase.CONFIRM_TXN
        state, _ = asyncio.run(run_turn(state, text, ctx, llm))  # type: ignore[arg-type]
        assert state.phase == Phase.CONFIRM_TXN
        assert state.confirmation == "yes"  # the model's verdict stays on the trace; it did not authorize
        assert state.acts[-1] == "ask_again"
        assert not any(a.tool in ("open_dispute", "evaluate_dispute") for a in ctx.cases.list_audit())


def test_model_yes_on_a_plain_yes_still_confirms():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details(), decisions=["yes"])
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_ACT


def test_confirm_turn_classifies_before_any_other_tool():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details(), decisions=["yes"])
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    assert state.pending_question
    audit_len = len(ctx.cases.list_audit())
    # The model says yes to a hedged reply: its verdict is recorded and code turns it into a question.
    state, _ = asyncio.run(run_turn(state, "sí, pero mejor no", ctx, llm))  # type: ignore[arg-type]
    tools = [row.tool for row in ctx.cases.list_audit()]
    assert tools[audit_len] == "classify_reply"
    assert "open_dispute" not in tools
    assert "block_card" not in tools
    assert state.confirmation == "yes"
    assert state.pending_question


def test_blank_confirmation_stays_in_phase_and_opens_nothing():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details(), decisions=[None])
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    state, reply = asyncio.run(run_turn(state, "tal vez", ctx, llm))  # type: ignore[arg-type]
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


def test_clarify_exhausted_offers_a_person_and_does_not_hand_off(monkeypatch):
    monkeypatch.setenv("MINSKY_MAX_CLARIFY_ATTEMPTS", "1")
    get_settings.cache_clear()
    try:
        ctx = _ctx(exec_rows=[])
        state = _state()
        llm = FakeLLM(_details(merchant="Nope", amount=None))
        state, _ = asyncio.run(run_turn(state, "Nope", ctx, llm))  # type: ignore[arg-type]
        assert state.phase == Phase.CLARIFY
        state, reply = asyncio.run(run_turn(state, "sigue sin aparecer", ctx, llm))  # type: ignore[arg-type]
        assert state.phase == Phase.OFFER_HANDOFF and state.offer_reason == "clarify_exhausted"
        assert reply == handoff_offer_question("es")
        assert not any(a.tool == "create_handoff" for a in ctx.cases.list_audit())  # nothing sent: the customer decides
        state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
        assert state.phase == Phase.DONE and "HO-" in reply
        (case,) = ctx.cases.list_cases()
        assert case.reason == "clarify_exhausted" and case.facts["customer_said"]["customer_accepted_offer"] is True
    finally:
        monkeypatch.delenv("MINSKY_MAX_CLARIFY_ATTEMPTS", raising=False)
        get_settings.cache_clear()


def test_inform_route_does_not_let_the_model_create_a_handoff():
    txn = _txn(status="Declined")
    ctx = _ctx(txn, exec_rows=[txn])
    state = _state()
    llm = _NthSpeech(
        _details(),
        n=1,
        speech=Speech(act="handoff", text="Quiero una persona."),
        decisions=["yes"],
    )
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_TXN
    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    # The model's handoff is refused; the customer still gets the code-written explanation, not a 503.
    assert not any(row.tool == "create_handoff" for row in ctx.cases.list_audit())
    assert "Quiero una persona" not in reply and "rechazado" in reply
    assert state.phase == Phase.DONE


def test_speech_failure_after_open_reports_the_dispute_id():
    ctx = _ctx()
    state = _state()
    llm = _RaiseOnSpeech(_details(), fail_on=2)
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
        # Nothing is 50, but the Cafe charge in the range is 25: proposed with the difference said, not selected.
        assert state.acts[-1] == "confirm_txn"
        assert state.match_tier == "closest"
        assert "otro monto" in reply and "25.00 USD" in reply
        assert state.search_details == first
        state.phase = Phase.CLARIFY  # the customer did not take the proposal and gives the amount again
        state, reply = await run_turn(state, "Era de 25", ctx, llm)  # type: ignore[arg-type]
        assert state.phase == Phase.CONFIRM_TXN
        assert state.match_tier is None
        assert state.selected_txn_id == "T1"
        assert state.search_details.merchant == "Cafe"
        assert state.search_details.date_from == first.date_from
        assert state.search_details.date_to == first.date_to
        assert state.search_details.amount == Decimal("25")
    finally:
        bank.close()


async def test_another_merchant_is_proposed_with_the_difference_and_never_opened_without_a_yes():
    from evals.fixtures import FixtureBank

    bank = FixtureBank([_txn(merchant="Other")])
    ctx = ToolContext(session=_valid(), db=bank, cases=InMemoryCasesBackend())  # type: ignore[arg-type]
    llm = FakeLLM([_details(amount=None), _details(merchant=None, amount=Decimal("25"))])
    try:
        state, _ = await run_turn(_state(), "Un cargo de Cafe", ctx, llm)  # type: ignore[arg-type]
        assert state.phase == Phase.CLARIFY  # a merchant alone finds nothing: the Other charge does not match it
        assert state.selected_txn_id is None
        state, reply = await run_turn(state, "Era de 25", ctx, llm)  # type: ignore[arg-type]
        # The amount holds and the merchant differs: said out loud, and only a yes moves on.
        assert state.phase == Phase.CONFIRM_TXN and state.match_tier == "closest"
        assert "No encontré un cargo de 25 en Cafe" in reply and "otro comercio" in reply and "Other" in reply
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
    assert reply.startswith("É esta a cobrança a que você se refere")
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
    assert reply.startswith("¿Es este el cargo al que te refieres")


def test_quero_opener_replies_in_portuguese_and_sim_confirms():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details())
    state, reply = asyncio.run(run_turn(state, "Quero disputar uma cobrança de 25 dólares no Cafe", ctx, llm))  # type: ignore[arg-type]
    assert state.language == "pt"
    assert reply.startswith("É esta a cobrança a que você se refere")
    state, reply = asyncio.run(run_turn(state, "sim", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_ACT
    assert reply.startswith("pt:")


def test_opened_dispute_is_queued_low_with_verified_facts():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details())
    for text in ("No reconozco el cargo en Cafe de 25", "sí", "sí"):
        state, _ = asyncio.run(run_turn(state, text, ctx, llm))  # type: ignore[arg-type]
    queued = ctx.cases.list_cases()
    assert len(queued) == 1
    case = queued[0]
    assert case.kind == "dispute" and case.priority == "Low" and case.queue == "disputes"
    assert case.facts["verified"]["merchant"] == "Cafe" and case.facts["verified"]["transaction_id"] == "T1"
    assert case.open_questions and case.status == "new"


def test_fraud_handoff_with_block_is_queued_critical():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details(customer_says_not_me=True), decisions=["yes", "yes"])
    for text in ("No reconozco el cargo en Cafe de 25, yo no fui", "sí", "sí"):
        state, _ = asyncio.run(run_turn(state, text, ctx, llm))  # type: ignore[arg-type]
    queued = ctx.cases.list_cases()
    assert [c.priority for c in queued] == ["Critical"]
    case = queued[0]
    assert case.queue == "fraud" and case.rule_id and case.rule_id.startswith("D06")
    assert any(a.startswith("card_blocked:") for a in case.actions)
    assert case.facts["customer_said"]["customer_request"].startswith("No reconozco")
    assert "did not make" in case.summary


def test_router_sets_the_dispute_type_but_not_the_route():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details())
    state, _ = asyncio.run(run_turn(state, "Me cobraron dos veces la misma compra en Cafe", ctx, llm))  # type: ignore[arg-type]
    assert state.router_label == "duplicate" and state.dispute_reason == "duplicate_charge"
    for text in ("sí", "sí"):
        state, _ = asyncio.run(run_turn(state, text, ctx, llm))  # type: ignore[arg-type]
    case = ctx.cases.list_cases()[0]
    assert case.reason == "duplicate_charge" and "duplicate charge" in case.summary
    assert state.rule_id == "D09-eligible"  # the policy still decides


def test_router_abstains_on_noise_and_keeps_the_default_type():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details())
    state, _ = asyncio.run(run_turn(state, "xq zzv 25", ctx, llm))  # type: ignore[arg-type]
    assert state.router_label is None  # below the val-fitted threshold: abstain, never guess
    assert state.dispute_reason == "unspecified"


def test_the_question_a_yes_authorizes_is_written_by_code():
    """Whatever the model writes, the stored question names the exact action that a yes will run."""
    ctx = _ctx()
    state = _state()
    llm = _NthSpeech(_details(), n=1, speech=Speech(act="confirm_open", text="¿Te paso con un asesor?"), repeat=1)
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_ACT
    assert reply.endswith("¿Abro el reclamo por este cargo? Responde sí o no.")
    assert state.pending_question == reply


def test_classifier_and_customer_disagreement_becomes_an_open_question():
    """The router reads 'not me' but the customer's words (extraction) do not: the agent must ask."""
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details(customer_says_not_me=False))
    for text in ("No reconozco este cargo de 25 en Cafe, no sé qué es", "sí", "sí"):
        state, _ = asyncio.run(run_turn(state, text, ctx, llm))  # type: ignore[arg-type]
    assert state.dispute_reason == "unrecognized_charge"  # the router's confident reading
    case = ctx.cases.list_cases()[0]
    assert any("confirm with the customer" in q for q in case.open_questions)


def test_d04_already_disputed_answers_with_the_existing_reference():
    """Regression: 'ya hay un reclamo abierto' with the existing reference used to be refused (503)."""
    ctx = _ctx()
    existing = ctx.cases.create_dispute(customer_id="C1", transaction_id="T1", reason="wrong_amount")
    state = _state()
    llm = FakeLLM(_details())
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.rule_id == "D04-already-disputed"
    assert state.phase == Phase.DONE
    assert existing.dispute_id in reply and "reclamo abierto" in reply
    assert len([c for c in ctx.cases.list_cases()]) == 0  # nothing new was opened or queued


_NEW_CONVERSATION_ES = "Esta conversación terminó. Si quieres disputar otro cargo, inicia una nueva conversación."


class _NoModelAfterTheCase(FakeLLM):
    """Any model call fails: once the case is settled the conversation must not need the model."""

    async def respond(self, *args: Any, **kwargs: Any) -> LLMResult[Any]:
        raise AssertionError("the model was called after the conversation ended")


def _settled(texts: tuple[str, ...]) -> tuple[ToolContext, ConversationState]:
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details())
    for text in texts:
        state, _ = asyncio.run(run_turn(state, text, ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE
    return ctx, state


def test_after_an_opened_dispute_every_message_gets_the_status_and_a_new_conversation_notice():
    """A conversation that ended is terminal: code answers with the status and the reference, whatever is said, and
    the model is not called. Found by hand: "¿Y cómo sigue?" got "consulta otras opciones con el banco" right after
    the case went to a specialist."""
    ctx, state = _settled(("Cafe 25", "sí", "sí"))
    assert state.terminal is not None and state.terminal.outcome == "dispute_opened"
    reference = state.terminal.reference
    assert reference
    before = len(ctx.cases.list_cases()), len(ctx.cases.list_audit())
    for text in ("¿Cuándo se resuelve?", "ok", "sí", "quiero reclamar otro cargo de 80 USD"):
        state, reply = asyncio.run(run_turn(state, text, ctx, _NoModelAfterTheCase(_details())))  # type: ignore[arg-type]
        assert reply == f"Tu reclamo está abierto con la referencia {reference}. {_NEW_CONVERSATION_ES}"
        assert state.phase == Phase.DONE
    assert (len(ctx.cases.list_cases()), len(ctx.cases.list_audit())) == before  # nothing new, not even a lookup


def test_after_a_handoff_the_status_is_the_handoff_and_a_card_block_is_reported_only_if_it_happened():
    ctx, state = _settled(("Cafe 25", "sí", "no"))  # declining the action ends it without any change
    assert state.terminal is not None and state.terminal.outcome == "cancelled"
    state, reply = asyncio.run(run_turn(state, "¿y ahora?", ctx, _NoModelAfterTheCase(_details())))  # type: ignore[arg-type]
    assert reply == f"No hice ningún cambio. {_NEW_CONVERSATION_ES}"

    from minsky_api.agent.state import Terminal
    from minsky_api.agent.wording import terminal_reply

    handed = Terminal("handoff", "HO-123")
    assert (
        terminal_reply("es", handed)
        == f"Tu caso está con un especialista, referencia HO-123; el equipo te avisará. {_NEW_CONVERSATION_ES}"
    )
    blocked = Terminal("handoff", "HO-123", card_blocked=True)
    assert terminal_reply("es", blocked).startswith("Tu tarjeta está bloqueada. Tu caso está con un especialista")
    assert "bloqueada" not in terminal_reply("es", handed)
    assert "nova conversa" in terminal_reply("pt", handed) and "HO-123" in terminal_reply("pt", handed)
    down = Terminal("unavailable", dispute_id="DSP-9")
    assert terminal_reply("es", down) == (
        "Tu reclamo DSP-9 ya quedó registrado. El sitio está en mantenimiento en este momento. Por favor, consulta "
        "con el servicio técnico. Cuando el sitio vuelva a estar disponible, inicia una nueva conversación."
    )
    assert "manutenção" in terminal_reply("pt", Terminal("unavailable")) and "HO-" not in terminal_reply("es", down)
    informed = Terminal("informed", dispute_id="DSP-9")
    assert terminal_reply("es", informed).startswith("No abrí ningún reclamo nuevo. El reclamo ya abierto es DSP-9.")


def test_a_handoff_ends_the_conversation_the_same_way_whatever_caused_it():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details())
    for text in (
        "Cafe 25",
        "sí",
        "mmm",
        "no sé",
        "quizás",
    ):  # the charge is confirmed; the action question gets no answer
        state, reply = asyncio.run(run_turn(state, text, ctx, llm))  # type: ignore[arg-type]
    assert state.terminal is not None and state.terminal.outcome == "handoff"
    ref = state.terminal.reference
    state, reply = asyncio.run(run_turn(state, "hola?", ctx, _NoModelAfterTheCase(_details())))  # type: ignore[arg-type]
    assert ref and ref in reply and reply.endswith(_NEW_CONVERSATION_ES)


def test_a_transfer_is_not_called_a_charge():
    from minsky_api.agent.wording import confirm_question, transaction_noun

    assert transaction_noun("Transfer", "es") == "transferencia"
    assert transaction_noun("Withdrawal", "pt") == "saque"
    assert (
        confirm_question("confirm_open", "es", "Transfer")
        == "¿Abro el reclamo por esta transferencia? Responde sí o no."
    )
    assert "cargo" in confirm_question("confirm_open", "es", None)


# ---- clarify: the model asks, code owns the option list -------------------------------------------------------

_OPENER = "Quiero disputar un cargo de Cafe."


def _two_cafes() -> tuple[ToolContext, str]:
    t1 = _txn(transaction_id="T1", amount_usd="25.00")
    t2 = _txn(transaction_id="T2", amount_usd="30.00")
    ctx = ToolContext(
        session=_valid(),
        db=FakeSession(get_result={CustomerComplaintStats: _stats(), Product: _card()}, exec_rows=[t1, t2]),  # type: ignore[arg-type]
        cases=InMemoryCasesBackend(),
    )
    views = [
        TransactionView(
            transaction_id=row.transaction_id,
            product_id=row.product_id,
            transaction_date=row.transaction_date,
            amount_usd=row.amount_usd,
            merchant_name=row.merchant_name,
            transaction_status=row.transaction_status,
        )
        for row in (t1, t2)
    ]
    return ctx, _candidate_list(views, "es")


def test_clarify_accepts_a_model_that_punctuates_the_options_its_own_way():
    """The live model wrote the options on one line with ';' and the guard refused it twice: a 503."""
    ctx, options = _two_cafes()
    text = "¿Cuál de estos cargos no reconoces? " + options.replace("\n", "; ") + "."
    llm = _NthSpeech(_details(amount=None), n=1, speech=Speech(act="clarify", text=text), repeat=1)
    state, reply = asyncio.run(run_turn(_state(), _OPENER, ctx, llm))  # type: ignore[arg-type]
    assert state.language == "es"
    assert state.phase == Phase.CLARIFY
    assert state.candidate_txn_ids == ["T1", "T2"]
    assert reply == text  # every option is named exactly, so code adds nothing


def test_clarify_appends_the_exact_list_when_the_model_names_no_options():
    ctx, options = _two_cafes()
    text = "Veo dos cargos de Cafe el 10 de junio de 2026: uno de 25.00 USD y otro de 30.00 USD. ¿Cuál de los dos?"
    llm = _NthSpeech(_details(amount=None), n=1, speech=Speech(act="clarify", text=text), repeat=1)
    state, reply = asyncio.run(run_turn(_state(), _OPENER, ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CLARIFY
    assert reply == f"{text}\n{options}"
    assert reply.count("1. Cafe") == 1


def test_clarify_sends_a_code_written_question_when_both_attempts_are_refused():
    """No 503: a reply that fails the checks twice is replaced by a question the code wrote."""
    ctx, options = _two_cafes()
    refused = Speech(act="clarify", text="¿Cuál de estos? Te cobraremos 900 USD de comisión.")
    llm = _NthSpeech(_details(amount=None), n=1, speech=refused, repeat=2)
    state, reply = asyncio.run(run_turn(_state(), _OPENER, ctx, llm))  # type: ignore[arg-type]
    assert reply == clarify_fallback("es", options)
    assert "900" not in reply
    assert state.phase == Phase.CLARIFY
    assert state.candidate_txn_ids == ["T1", "T2"]
    assert state.acts[-1] == "clarify"


def test_no_match_is_a_code_written_question_that_asks_for_the_charge_not_for_those_transactions():
    """Found by hand: "un cargo de ayer" found nothing and the model asked "¿cuál de esas transacciones?"."""
    llm = FakeLLM(_details(amount=None))
    state, reply = asyncio.run(run_turn(_state(), _OPENER, _ctx(exec_rows=[]), llm))  # type: ignore[arg-type]
    assert reply == clarify_fallback("es", None)
    assert state.phase == Phase.CLARIFY and state.acts[-1] == "clarify"
    assert state.candidate_txn_ids == []


def test_a_missing_llm_key_is_not_hidden_by_the_clarify_fallback():
    """A fallback covers a reply that failed the checks, never a system that cannot call the model."""

    class _NotConfigured(FakeLLM):
        async def respond(self, *args: Any, schema: type | None = None, **kwargs: Any) -> LLMResult[Any]:
            if schema is not None and schema.__name__ == "Speech":
                raise LLMNotConfiguredError("no key")
            return await super().respond(*args, schema=schema, **kwargs)

    ctx, _ = _two_cafes()
    with pytest.raises(LLMNotConfiguredError):
        asyncio.run(run_turn(_state(), _OPENER, ctx, _NotConfigured(_details(amount=None))))  # type: ignore[arg-type]


# ---- a bare "no" declines, whatever the model reads ---------------------------------------------------------------


def test_a_bare_no_to_the_card_offer_hands_off_even_if_the_model_reads_it_as_yes():
    """Live, the classifier read "no" as "yes" in 5 of 60 calls under the D06 wording. The hedge floor then asked
    again, so the customer was neither blocked nor handed to the fraud team. A plain "no" now declines."""
    ctx = _ctx(_txn(is_fraud=True))
    state = _state()
    llm = FakeLLM(_details(customer_says_not_me=True), decisions=["yes", "yes"])
    state, _ = asyncio.run(run_turn(state, "No fui yo en Cafe", ctx, llm))  # type: ignore[arg-type]
    state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CARD_OFFER
    state, reply = asyncio.run(run_turn(state, "no", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE
    assert state.confirmation == "yes"  # the misread stays on the trace, which is how it can be seen
    assert "HO-" in reply
    assert ctx.cases.get_card_block("P1") is None
    audit = ctx.cases.list_audit()
    assert any(a.tool == "create_handoff" and a.outcome == "ok" for a in audit)
    assert not any(a.tool == "block_card" and a.outcome == "ok" for a in audit)


def test_a_bare_no_to_the_transaction_question_opens_nothing():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details(), decisions=["yes", "yes"])
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_TXN
    state, _ = asyncio.run(run_turn(state, "No.", ctx, llm))  # type: ignore[arg-type]
    assert state.confirmation == "yes"
    assert state.phase != Phase.CONFIRM_ACT
    assert not any(a.tool == "open_dispute" for a in ctx.cases.list_audit())


def test_an_explicit_yes_proceeds_whatever_the_model_recorded():
    """The model's verdict is evidence. A plain "sí" is consent even when the classifier returned nothing usable."""
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details(), decisions=[None])
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_ACT  # the next question is already asked, so the verdict is on the audit
    assert any(
        r.tool == "classify_reply" and r.outcome == "ok" and r.reason == "unclear" for r in ctx.cases.list_audit()
    )


# ---- ported from #27 (c841def, Arturo Collazo Gil): consent is explicit, whatever the model says ----------------


def test_explicit_no_does_not_open_when_the_model_says_yes():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details(), decisions=["yes", "yes"])
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_ACT
    state, _ = asyncio.run(run_turn(state, "no,", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE
    assert state.confirmation == "yes"
    assert not any(row.tool == "open_dispute" and row.outcome == "ok" for row in ctx.cases.list_audit())


def test_a_sentence_yes_does_not_open_even_when_the_model_says_yes():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details(), decisions=["yes", "yes"])
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_ACT
    state, _ = asyncio.run(run_turn(state, "sí, es ese", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_ACT
    assert state.acts[-1] == "ask_again"
    assert not any(row.tool == "open_dispute" and row.outcome == "ok" for row in ctx.cases.list_audit())


def test_explicit_no_does_not_block_the_card_when_the_model_says_yes():
    txn = _txn(is_fraud=True)
    ctx = _ctx(txn)
    state = _state()
    llm = FakeLLM(_details(customer_says_not_me=True), decisions=["yes", "yes"])
    state, _ = asyncio.run(run_turn(state, "No fui yo en Cafe", ctx, llm))  # type: ignore[arg-type]
    state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CARD_OFFER
    state, _ = asyncio.run(run_turn(state, "não", ctx, llm))  # type: ignore[arg-type]
    assert ctx.cases.get_card_block("P1") is None
    assert not any(row.tool == "block_card" and row.outcome == "ok" for row in ctx.cases.list_audit())


# ---- a reply refused twice is a code-written sentence on every path, never an HTTP 503 ----------------------------

_REFUSED = "Te cobraremos 900 USD de comisión."  # an invented amount: the grounding check refuses it every time


def _refused(act: str) -> Speech:
    return Speech.model_validate({"act": act, "text": _REFUSED})


def _turns(llm: FakeLLM, ctx: ToolContext, *texts: str) -> tuple[ConversationState, str]:
    state = _state()
    reply = ""
    for text in texts:
        state, reply = asyncio.run(run_turn(state, text, ctx, llm))  # type: ignore[arg-type]
    return state, reply


def test_a_refused_ask_again_is_a_code_written_one():
    llm = _NthSpeech(_details(), n=1, speech=_refused("ask_again"), repeat=2)
    state, reply = _turns(llm, _ctx(), "Cafe 25", "tal vez")
    assert state.phase == Phase.CONFIRM_TXN
    # The code-written sentence repeats the question that was asked and always says how to answer.
    assert reply.startswith("No me quedó claro.")
    assert state.pending_question and state.pending_question.rstrip(". ").split("Responde")[0].strip() in reply
    assert reply.endswith("En esta parte del proceso solo puedes responder «sí» o «no».")


def test_a_refused_open_confirmation_keeps_the_code_written_question():
    llm = _NthSpeech(_details(), n=1, speech=_refused("confirm_open"), repeat=2)
    state, reply = _turns(llm, _ctx(), "Cafe 25", "sí")
    assert state.phase == Phase.CONFIRM_ACT
    assert reply.startswith("El cargo cumple las condiciones") and reply.endswith("Responde sí o no.")
    assert "900" not in reply and state.pending_question == reply


def test_a_refused_card_offer_keeps_the_code_written_question():
    ctx = _ctx(_txn(is_fraud=True))
    llm = _NthSpeech(_details(customer_says_not_me=True), n=1, speech=_refused("offer_block"), repeat=2)
    state, reply = _turns(llm, ctx, "No fui yo en Cafe", "sí")
    assert state.phase == Phase.CARD_OFFER
    assert "alguien podría estar usando tu tarjeta" in reply and "¿Bloqueo tu tarjeta ahora?" in reply


def test_a_refused_abort_is_a_code_written_one():
    llm = _NthSpeech(_details(), n=2, speech=_refused("abort"), repeat=2)
    ctx = _ctx()
    state, reply = _turns(llm, ctx, "Cafe 25", "sí", "no")
    assert state.phase == Phase.DONE
    assert reply == safe_sentence("abort", "es", {})
    assert not any(row.tool == "open_dispute" for row in ctx.cases.list_audit())


def test_a_refused_clarify_or_abort_after_a_no_is_a_code_written_clarification():
    llm = _NthSpeech(_details(), n=1, speech=_refused("clarify"), repeat=2)
    state, reply = _turns(llm, _ctx(), "Cafe 25", "no")
    assert state.phase == Phase.CLARIFY
    assert reply == clarify_fallback("es", None)


def test_a_missing_key_is_still_loud_on_the_paths_that_now_have_a_fallback():
    class _NotConfigured(FakeLLM):
        async def respond(self, *args: Any, schema: type | None = None, **kwargs: Any) -> LLMResult[Any]:
            if schema is not None and schema.__name__ == "Speech":
                raise LLMNotConfiguredError("no key")
            return await super().respond(*args, schema=schema, **kwargs)

    with pytest.raises(LLMNotConfiguredError):
        _turns(_NotConfigured(_details()), _ctx(), "Cafe 25", "sí")


def test_the_transaction_question_ends_with_a_code_owned_yes_or_no_hint_exactly_once():
    ctx = _ctx()
    llm = FakeLLM(_details())
    state, reply = _turns(llm, ctx, "Cafe 25")
    assert state.phase == Phase.CONFIRM_TXN
    assert reply.endswith("Responde sí o no.") and reply.count("sí o no") == 1
    assert state.pending_question == reply  # the classifier sees the question the customer saw


# ---- asking again: the model rephrases, code says that only yes or no works here ------------------------------

_ONLY_ES = "En esta parte del proceso solo puedes responder «sí» o «no»."
_ONLY_PT = "Nesta parte do processo você só pode responder «sim» ou «não»."


def test_a_natural_reply_to_the_transaction_question_is_told_how_to_answer_and_the_conversation_can_go_on():
    """The loop found by hand on the deployed demo: "lo reconozco", "el de antes" and a retyped claim each got a
    request for more details, and nothing ever told the customer that only sí or no moves this step on."""
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details(), decisions=["unclear", "unclear", "yes"])
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_TXN
    pending = state.pending_question
    assert pending
    for natural in ("lo reconozco", "el de antes"):  # two tries: the third unclear reply goes to a person
        state, reply = asyncio.run(run_turn(state, natural, ctx, llm))  # type: ignore[arg-type]
        assert reply.endswith(_ONLY_ES), reply
        assert state.phase == Phase.CONFIRM_TXN
        assert state.acts[-1] == "ask_again"
        assert state.pending_question == pending  # every further reply is asked the same thing again
        assert not any(a.tool == "open_dispute" for a in ctx.cases.list_audit())
    state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_ACT  # a plain yes still moves on


def test_the_notice_is_there_even_if_the_model_asks_for_more_details():
    """The model is not trusted to say how to answer: this is the reply that went out in the loop."""
    ctx = _ctx()
    state = _state()
    wrong = Speech(act="ask_again", text="¿Puedes contarme un poco más sobre el cargo que no reconoces?")
    llm = _NthSpeech(_details(), n=1, speech=wrong, repeat=1, decisions=["unclear"])
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    state, reply = asyncio.run(run_turn(state, "lo reconozco", ctx, llm))  # type: ignore[arg-type]
    assert reply == f"{wrong.text} {_ONLY_ES}"


def test_a_model_closing_instruction_is_replaced_by_the_notice():
    ctx = _ctx()
    state = _state()
    rephrased = Speech(act="ask_again", text="¿Es ese el cargo al que te refieres? Responde sí o no.")
    llm = _NthSpeech(_details(), n=1, speech=rephrased, repeat=1, decisions=["unclear"])
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    state, reply = asyncio.run(run_turn(state, "ese mismo", ctx, llm))  # type: ignore[arg-type]
    assert reply == f"¿Es ese el cargo al que te refieres? {_ONLY_ES}"


def test_a_refused_rephrasing_falls_back_to_the_pending_question_with_the_notice():
    ctx = _ctx()
    state = _state()
    refused = Speech(act="ask_again", text="¿Seguro? Se te cobrarán 900 USD de comisión si no respondes.")
    llm = _NthSpeech(_details(), n=1, speech=refused, repeat=2, decisions=["unclear"])
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    pending = state.pending_question
    assert pending
    state, reply = asyncio.run(run_turn(state, "lo reconozco", ctx, llm))  # type: ignore[arg-type]
    assert "900" not in reply
    assert reply.startswith("No me quedó claro.")
    assert reply.endswith(_ONLY_ES)
    assert "Cafe" in reply  # the question that was asked is repeated, not replaced


def test_the_notice_follows_the_language_of_the_conversation():
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details(), decisions=["unclear"])
    state, _ = asyncio.run(run_turn(state, "Quero disputar uma cobrança de 25 dólares no Cafe", ctx, llm))  # type: ignore[arg-type]
    assert state.language == "pt"
    state, reply = asyncio.run(run_turn(state, "reconheço", ctx, llm))  # type: ignore[arg-type]
    assert reply.endswith(_ONLY_PT), reply


# ---- the transaction question is code's, not the model's ---------------------------------------------------------


class _SpokenActs(FakeLLM):
    """Records which acts the model was allowed to speak."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.allowed: list[tuple[str, ...]] = []

    async def respond(self, *args: Any, schema: type | None = None, **kwargs: Any) -> LLMResult[Any]:
        if schema is not None and schema.__name__ == "Speech":
            import json

            self.allowed.append(tuple(json.loads(str(args[1][-1]["content"]))["allowed"]))
        return await super().respond(*args, schema=schema, **kwargs)


def test_the_transaction_question_is_written_by_code_and_the_model_is_never_asked_for_it():
    """It was the model's, and a refused or hijacked reply there needed its own tests. Now it cannot be either: the
    model is never asked for it, so it cannot add a claim, hand off instead of asking, or word it badly."""
    ctx = _ctx()
    state = _state()
    llm = _SpokenActs(_details())
    state, reply = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    assert reply == "¿Es este el cargo al que te refieres: Cafe, 25.00 USD, del 10 de junio de 2026? Responde sí o no."
    assert state.pending_question == reply
    assert state.phase == Phase.CONFIRM_TXN
    assert state.acts[-1] == "confirm_txn"
    assert all("confirm_txn" not in allowed for allowed in llm.allowed)
    assert not any(row.tool in ("block_card", "create_handoff") for row in ctx.cases.list_audit())


def test_the_transaction_question_after_picking_a_candidate_is_also_written_by_code():
    t1 = _txn(transaction_id="T1", merchant="Cafe")
    t2 = _txn(transaction_id="T2", merchant="Cafe Sur")
    ctx = _ctx(t2, exec_rows=[t1, t2])
    state = _state()
    llm = _SpokenActs(_details(merchant="Cafe", amount=None))
    state, _ = asyncio.run(run_turn(state, "Cafe", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CLARIFY
    state, reply = asyncio.run(run_turn(state, "2", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_TXN
    assert reply.startswith("¿Es este el cargo al que te refieres: Cafe Sur, 25.00 USD")
    assert all("confirm_txn" not in allowed for allowed in llm.allowed)


def test_a_customer_who_does_not_recognize_the_charge_is_not_asked_whether_they_recognize_it():
    """The fraud wording trap: "no reconozco este cargo" answered by "¿Reconoces este cargo?"."""
    ctx = _ctx(_txn(is_fraud=True))
    state = _state()
    llm = _SpokenActs(_details(customer_says_not_me=True))
    state, reply = asyncio.run(run_turn(state, "No reconozco un cargo de 25 USD en Cafe", ctx, llm))  # type: ignore[arg-type]
    assert "reconoc" not in reply.casefold()
    assert reply.startswith("¿Es este el cargo al que te refieres")
    assert state.customer_says_not_me is True
    state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CARD_OFFER  # "sí, es ese" leads to the fraud path, as the customer meant


# ---- too many replies in a row that are not a plain yes or no: a person takes the case -------------------------


def _unclear_turns(llm: FakeLLM, ctx: ToolContext, *texts: str) -> tuple[ConversationState, str]:
    state = _state()
    reply = ""
    for text in texts:
        state, reply = asyncio.run(run_turn(state, text, ctx, llm))  # type: ignore[arg-type]
    return state, reply


def test_the_third_unclear_reply_to_the_transaction_question_offers_a_person_and_sends_nothing():
    """The charge is not confirmed yet, so nothing goes to a person until the customer says yes to an agent."""
    ctx = _ctx()
    llm = FakeLLM(_details(), decisions=["unclear", "unclear", "unclear"])
    state = _state()
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    pending = state.pending_question
    assert pending
    for count, words in enumerate(("lo reconozco", "ese mismo"), start=1):
        state, reply = asyncio.run(run_turn(state, words, ctx, llm))  # type: ignore[arg-type]
        assert state.phase == Phase.CONFIRM_TXN and state.unclear_count == count
        assert reply.endswith("En esta parte del proceso solo puedes responder «sí» o «no».")
    state, reply = asyncio.run(run_turn(state, "claro que sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.OFFER_HANDOFF and state.offer_reason == "unclear_confirmation"
    assert reply == handoff_offer_question("es")
    assert not ctx.cases.list_cases()
    assert not any(a.tool in ("create_handoff", "open_dispute", "block_card") for a in ctx.cases.list_audit())
    state, reply = asyncio.run(run_turn(state, "sí", ctx, FakeLLM(_details())))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE and "HO-" in reply
    (case,) = ctx.cases.list_cases()
    assert case.kind == "handoff" and case.reason == "unclear_confirmation"
    assert case.facts["customer_said"]["customer_accepted_offer"] is True


def test_the_third_unclear_reply_after_the_charge_is_confirmed_hands_the_case_to_a_person_and_opens_nothing():
    ctx = _ctx()
    llm = FakeLLM(_details(), decisions=["yes", "unclear", "unclear", "unclear"])
    state, _ = _unclear_turns(llm, ctx, "Cafe 25", "sí")
    assert state.phase == Phase.CONFIRM_ACT and state.txn_confirmed
    pending = state.pending_question
    for words in ("lo reconozco", "ese mismo", "claro que sí"):
        state, reply = asyncio.run(run_turn(state, words, ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE
    assert "HO-" in reply
    (case,) = ctx.cases.list_cases()
    assert case.kind == "handoff" and case.reason == "unclear_confirmation"
    assert case.facts["customer_said"]["unanswered_question"] == pending
    assert case.facts["customer_said"]["last_customer_replies"] == ["lo reconozco", "ese mismo", "claro que sí"]
    assert "plain yes or no" in case.summary
    audit = ctx.cases.list_audit()
    assert not any(a.tool in ("open_dispute", "block_card") for a in audit)


def test_a_plain_answer_starts_the_count_again_for_the_next_question():
    ctx = _ctx()
    llm = FakeLLM(_details(), decisions=["unclear", "unclear", "yes", "unclear", "unclear", "yes"])
    state, _ = _unclear_turns(llm, ctx, "Cafe 25", "lo reconozco", "ese mismo", "sí")
    assert state.phase == Phase.CONFIRM_ACT and state.unclear_count == 0
    for words in ("mmm", "no sé"):
        state, _ = asyncio.run(run_turn(state, words, ctx, llm))  # type: ignore[arg-type]
        assert state.phase == Phase.CONFIRM_ACT  # two more unclear replies to a new question: still no handoff
    assert not any(a.tool == "create_handoff" for a in ctx.cases.list_audit())
    state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE
    assert any(a.tool == "open_dispute" and a.outcome == "ok" for a in ctx.cases.list_audit())


def test_the_limit_is_a_setting(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MINSKY_MAX_UNCLEAR_REPLIES", "2")
    get_settings.cache_clear()
    try:
        ctx = _ctx()
        llm = FakeLLM(_details(), decisions=["yes", "unclear", "unclear"])
        state, reply = _unclear_turns(llm, ctx, "Cafe 25", "sí", "lo reconozco", "ese mismo")
        assert state.phase == Phase.DONE and "HO-" in reply
    finally:
        monkeypatch.delenv("MINSKY_MAX_UNCLEAR_REPLIES", raising=False)
        get_settings.cache_clear()


def test_a_fraud_customer_who_cannot_answer_the_card_offer_keeps_the_fraud_priority():
    ctx = _ctx(_txn(is_fraud=True))
    llm = FakeLLM(_details(customer_says_not_me=True), decisions=["yes", "unclear", "unclear", "unclear"])
    state, _ = _unclear_turns(llm, ctx, "No fui yo en Cafe", "sí")
    assert state.phase == Phase.CARD_OFFER
    for words in ("mmm", "no sé", "quizás"):
        state, reply = asyncio.run(run_turn(state, words, ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE and "HO-" in reply
    (case,) = ctx.cases.list_cases()
    assert case.reason == "unclear_confirmation"
    assert (case.queue, case.priority) == ("fraud", "Critical")
    assert case.rule_id and case.rule_id.startswith("D06")
    assert ctx.cases.get_card_block("P1") is None  # nothing was blocked: no clear yes


def test_a_dispute_handoff_carries_no_rule_so_it_does_not_repeat_the_open_reason():
    ctx = _ctx()
    llm = FakeLLM(_details(), decisions=["yes", "unclear", "unclear", "unclear"])
    _unclear_turns(llm, ctx, "Cafe 25", "sí", "lo reconozco", "ese mismo", "claro que sí")
    (case,) = ctx.cases.list_cases()
    assert case.rule_id is None and case.queue == "general"


# ---- no case goes to a person before the customer confirms the charge or asks for an agent ----------------------


def _offered(ctx: ToolContext | None = None) -> tuple[ToolContext, ConversationState]:
    """A conversation whose first message is not about any charge ("hola"): the agent is offered, not sent."""
    ctx = ctx or _ctx()
    llm = FakeLLM(_details(out_of_scope=True))
    state, reply = asyncio.run(run_turn(_state(), "hola", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.OFFER_HANDOFF and reply == handoff_offer_question("es")
    return ctx, state


def test_a_greeting_gets_an_offer_of_a_person_and_no_case():
    ctx, state = _offered()
    assert state.offer_reason == "out_of_scope" and state.handoff_offers == 1
    assert state.pending_question == handoff_offer_question("es")
    assert not ctx.cases.list_cases() and not any(a.tool == "create_handoff" for a in ctx.cases.list_audit())
    assert "agente humano" not in handoff_offer_question("es") and "asesor humano" in handoff_offer_question("es")


def test_yes_to_the_offer_hands_off_and_the_case_says_the_customer_asked_for_it():
    ctx, state = _offered()
    state, reply = asyncio.run(run_turn(state, "sí", ctx, FakeLLM(_details())))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE and state.handoff_accepted and "HO-" in reply
    (case,) = ctx.cases.list_cases()
    assert case.reason == "out_of_scope" and case.facts["customer_said"]["customer_accepted_offer"] is True
    assert case.facts["verified"] == {}  # no charge was ever confirmed, and the case does not pretend one was


def test_no_to_the_offer_goes_back_to_asking_for_the_charge_with_fresh_tries():
    ctx, state = _offered()
    state, reply = asyncio.run(run_turn(state, "no", ctx, FakeLLM(_details())))  # type: ignore[arg-type]
    assert reply == handoff_offer_declined("es")
    assert state.phase == Phase.CLARIFY and state.clarify_count == 0 and state.pending_question is None
    assert not ctx.cases.list_cases()
    state, reply = asyncio.run(run_turn(state, "Cafe 25", ctx, FakeLLM(_details())))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_TXN  # and the conversation goes on as any other
    state, _ = asyncio.run(run_turn(state, "sí", ctx, FakeLLM(_details())))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_ACT and state.txn_confirmed


def test_a_second_no_ends_the_conversation_without_a_case():
    ctx, state = _offered()
    state, _ = asyncio.run(run_turn(state, "no", ctx, FakeLLM(_details())))  # type: ignore[arg-type]
    state, reply = asyncio.run(run_turn(state, "hola otra vez", ctx, FakeLLM(_details(out_of_scope=True))))  # type: ignore[arg-type]
    assert state.phase == Phase.OFFER_HANDOFF and state.handoff_offers == 2
    state, reply = asyncio.run(run_turn(state, "no", ctx, FakeLLM(_details())))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE and state.terminal is not None and state.terminal.outcome == "no_case"
    assert reply == f"No abrí ningún caso ni te pasé con nadie. {_NEW_CONVERSATION_ES}"
    assert not ctx.cases.list_cases()
    state, reply = asyncio.run(run_turn(state, "hola", ctx, _NoModelAfterTheCase(_details())))  # type: ignore[arg-type]
    assert reply.endswith(_NEW_CONVERSATION_ES)


def test_a_third_offer_is_never_made_and_the_number_is_a_setting(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MINSKY_MAX_HANDOFF_OFFERS", "1")
    get_settings.cache_clear()
    try:
        ctx, state = _offered()
        state, reply = asyncio.run(run_turn(state, "no", ctx, FakeLLM(_details())))  # type: ignore[arg-type]
        assert state.terminal is not None and state.terminal.outcome == "no_case"
        assert not ctx.cases.list_cases()
    finally:
        monkeypatch.delenv("MINSKY_MAX_HANDOFF_OFFERS", raising=False)
        get_settings.cache_clear()


def test_replies_that_are_not_yes_or_no_to_the_offer_end_without_a_case():
    ctx, state = _offered()
    llm = FakeLLM(DisputeDetails())  # nothing about a charge in these replies
    for words in ("mmm", "no sé"):
        state, _ = asyncio.run(run_turn(state, words, ctx, llm))  # type: ignore[arg-type]
        assert state.phase == Phase.OFFER_HANDOFF
    state, reply = asyncio.run(run_turn(state, "quizás", ctx, llm))  # type: ignore[arg-type]
    assert state.terminal is not None and state.terminal.outcome == "no_case"
    assert not ctx.cases.list_cases() and not any(a.tool == "create_handoff" for a in ctx.cases.list_audit())


def test_a_handoff_cannot_be_created_before_the_charge_is_confirmed_or_an_agent_accepted():
    """The rule is in code: a state machine bug fails loudly instead of sending a case nobody agreed to."""
    from minsky_api.agent.orchestrator import _handoff

    ctx = _ctx()
    state = _state()
    with pytest.raises(RuntimeError, match="before the customer confirmed"):
        asyncio.run(_handoff(ctx, state, FakeLLM(_details()), reason="clarify_exhausted"))  # type: ignore[arg-type]
    assert not ctx.cases.list_cases()
    state.handoff_accepted = True  # the customer asked for an agent: now it is allowed
    state.language = "es"
    asyncio.run(_handoff(ctx, state, FakeLLM(_details()), reason="clarify_exhausted"))  # type: ignore[arg-type]
    assert len(ctx.cases.list_cases()) == 1


def test_a_policy_handoff_needs_the_confirmed_charge_and_has_it():
    ctx = _ctx(_txn(is_fraud=True))
    llm = FakeLLM(_details(customer_says_not_me=True))
    state = _state()
    state, _ = asyncio.run(run_turn(state, "No fui yo en Cafe", ctx, llm))  # type: ignore[arg-type]
    assert not state.txn_confirmed and not ctx.cases.list_cases()  # asked which charge; nothing sent yet
    state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.txn_confirmed and state.phase == Phase.CARD_OFFER


def test_answering_the_offer_with_the_charge_declines_the_agent_and_goes_on():
    """Found by hand on the deployed demo: offered an agent, the customer answered with the charge (what the offer
    asked for) and was told that only sí or no works."""
    ctx, state = _offered()
    llm = FakeLLM(_details(merchant="Cafe", amount=Decimal("25.00")))
    state, reply = asyncio.run(run_turn(state, "Quiero reclamar un cargo de 25.00 USD en Cafe", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_TXN and "Cafe" in reply
    assert not state.handoff_accepted and not ctx.cases.list_cases()
    assert not any(a.tool == "create_handoff" for a in ctx.cases.list_audit())
    assert state.handoff_offers == 1 and state.unclear_count == 0
    state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_ACT and state.txn_confirmed


def test_a_charge_that_is_not_found_after_the_offer_asks_for_it_again_and_does_not_hand_off():
    ctx, state = _offered(_ctx(exec_rows=[]))
    llm = FakeLLM(_details(merchant="Nope", amount=Decimal("9.99")))
    state, reply = asyncio.run(run_turn(state, "un cargo de 9.99 en Nope", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CLARIFY and reply == clarify_fallback("es", None)
    assert not ctx.cases.list_cases()


def test_an_unclear_reply_with_no_charge_in_it_is_still_asked_again_after_the_offer():
    ctx, state = _offered()
    state, reply = asyncio.run(run_turn(state, "mmm no sé", ctx, FakeLLM(DisputeDetails())))  # type: ignore[arg-type]
    assert state.phase == Phase.OFFER_HANDOFF and state.unclear_count == 1
    assert reply.endswith("En esta parte del proceso solo puedes responder «sí» o «no».")


# --- Flexible matching: what the customer hears and what still needs a yes ---------------------------------------


def _at(txn: Transaction, *, when: datetime | None = None, **fields: Any) -> Transaction:
    if when is not None:
        txn.transaction_date = when
    for name, value in fields.items():
        setattr(txn, name, value)
    return txn


def test_a_near_amount_is_proposed_with_the_difference_and_opens_only_after_two_yes():
    row = _txn(amount_usd="123.10", merchant="Super Ahorro")
    ctx = _ctx(row, exec_rows=[row])
    llm = FakeLLM(_details(merchant=None, amount=Decimal("123")))
    state, reply = asyncio.run(run_turn(_state(), "No reconozco un cargo de 123 dólares", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_TXN and state.match_tier == "near"
    assert state.acts[-1] == "confirm_txn" and state.selected_txn_id == "T1"
    assert reply.startswith("No encontré un cargo de 123.") and "123.10 USD" in reply and "monto cercano" in reply
    assert state.pending_question == reply
    assert all(row.tool != "open_dispute" for row in ctx.cases.list_audit())

    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CONFIRM_ACT  # the policy now reads the charge's real facts
    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    dispute = ctx.cases.get_dispute_by_transaction(customer_id="C1", transaction_id="T1")
    assert dispute is not None and dispute.dispute_id in reply


def test_declining_a_near_proposal_opens_nothing():
    row = _txn(amount_usd="123.10")
    ctx = _ctx(row, exec_rows=[row])
    llm = FakeLLM(_details(merchant=None, amount=Decimal("123")))
    state, _ = asyncio.run(run_turn(_state(), "Un cargo de 123", ctx, llm))  # type: ignore[arg-type]
    state, _ = asyncio.run(run_turn(state, "no", ctx, llm))  # type: ignore[arg-type]
    assert state.selected_txn_id is None
    assert all(entry.tool != "open_dispute" for entry in ctx.cases.list_audit())


def test_the_exact_charge_is_proposed_alone_even_when_a_near_one_exists():
    exact = _txn(transaction_id="T-EXACT", amount_usd="25.37")
    near = _txn(transaction_id="T-NEAR", amount_usd="25.38")
    ctx = _ctx(exact, exec_rows=[near, exact])
    llm = FakeLLM(_details(merchant=None, amount=Decimal("25.37")))
    state, reply = asyncio.run(run_turn(_state(), "Un cargo de 25.37", ctx, llm))  # type: ignore[arg-type]
    assert state.selected_txn_id == "T-EXACT" and state.match_tier is None
    assert "No encontré" not in reply


async def test_several_near_charges_are_listed_and_a_number_picks_one():
    from evals.fixtures import FixtureBank

    bank = FixtureBank([_txn(transaction_id="T1", amount_usd="83.00"), _txn(transaction_id="T2", amount_usd="83.40")])
    ctx = ToolContext(session=_valid(), db=bank, cases=InMemoryCasesBackend())  # type: ignore[arg-type]
    llm = FakeLLM(_details(merchant=None, amount=Decimal("83.20"), approximate=True))
    try:
        state, reply = await run_turn(_state(), "Como 83.20", ctx, llm)  # type: ignore[arg-type]
        assert state.phase == Phase.CLARIFY and state.acts[-1] == "clarify"
        assert sorted(state.candidate_txn_ids) == ["T1", "T2"] and "1. " in reply and "2. " in reply
        picked = state.candidate_txn_ids[1]
        state, reply = await run_turn(state, "2", ctx, llm)  # type: ignore[arg-type]
        assert state.phase == Phase.CONFIRM_TXN and state.selected_txn_id == picked
    finally:
        bank.close()


def test_yesterday_is_resolved_by_the_system_date_and_a_charge_of_today_is_near():
    today = get_settings().today
    row = _at(_txn(), when=datetime(today.year, today.month, today.day, 2, 0))
    ctx = _ctx(row, exec_rows=[row])
    llm = FakeLLM(_details(merchant=None, amount=None, days_ago=1))
    state, reply = asyncio.run(run_turn(_state(), "Es de ayer", ctx, llm))  # type: ignore[arg-type]
    assert state.match_tier == "near" and "17 de junio de 2026" in reply and "fecha cercana" in reply
    assert state.search_details.date_from == date(2026, 6, 17) and state.search_details.days_ago is None


def test_the_charge_of_the_day_asked_is_exact():
    row = _at(_txn(), when=datetime(2026, 6, 17, 20, 0))
    ctx = _ctx(row, exec_rows=[row])
    llm = FakeLLM(_details(merchant=None, amount=None, days_ago=1))
    state, reply = asyncio.run(run_turn(_state(), "Es de ayer", ctx, llm))  # type: ignore[arg-type]
    assert state.selected_txn_id == "T1" and state.match_tier is None


def test_a_kind_alone_is_not_a_search():
    ctx = _ctx(exec_rows=[_txn(), _txn(transaction_id="T2")])
    llm = FakeLLM(_details(merchant=None, amount=None, transaction_type="Purchase"))
    state, reply = asyncio.run(run_turn(_state(), "Una compra", ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.CLARIFY and state.candidate_txn_ids == [] and state.selected_txn_id is None


def test_a_local_currency_amount_is_matched_in_that_currency():
    row = _at(_txn(amount_usd="123.00"), amount=Decimal("500000.00"), currency="COP")
    ctx = _ctx(row, exec_rows=[row])
    llm = FakeLLM(_details(merchant=None, amount=Decimal("500000"), currency="COP"))
    state, reply = asyncio.run(run_turn(_state(), "Un cargo de 500000 pesos colombianos", ctx, llm))  # type: ignore[arg-type]
    assert state.selected_txn_id == "T1" and state.match_tier is None


def test_the_back_office_sees_that_the_charge_was_a_near_match():
    row = _txn(amount_usd="600.50")
    ctx = _ctx(row, exec_rows=[row])
    llm = FakeLLM(_details(merchant=None, amount=Decimal("600")))
    state, _ = asyncio.run(run_turn(_state(), "Un cargo de 600", ctx, llm))  # type: ignore[arg-type]
    state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]  # above the auto limit: handoff
    handoff = next(iter(ctx.cases.list_cases()))
    assert handoff.facts["customer_said"]["match_tier"] == "near"
