"""Orchestrator state machine: happy path, clarify, budgets, fraud block, no false open."""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from minsky_api.agent.extract import DisputeDetails
from minsky_api.agent.orchestrator import _candidate_list, run_turn
from minsky_api.agent.speak import Speech
from minsky_api.agent.state import ConversationState, Phase
from minsky_api.agent.wording import clarify_fallback
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
    """The deterministic floor: a model 'yes' on a reply that also says no or 'but' never acts.

    A reply that is only "no" is not here: it is a plain decline (see the bare-no tests below).
    """
    for text in ("sí, pero mejor no", "sim, mas espere", "no, gracias"):
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
    # A hedged reply, not a bare "no": the model says yes and the hedge floor turns it into a question.
    state, _ = asyncio.run(run_turn(state, "sí, pero mejor no", ctx, llm))  # type: ignore[arg-type]
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
    state, reply = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    # The model's handoff is refused; the customer still gets the code-written explanation, not a 503.
    assert not any(row.tool == "create_handoff" for row in ctx.cases.list_audit())
    assert "Quiero una persona" not in reply and "rechazado" in reply
    assert state.phase == Phase.DONE


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
    llm = _NthSpeech(_details(), n=2, speech=Speech(act="confirm_open", text="¿Te paso con un asesor?"), repeat=1)
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


def test_follow_up_after_the_case_gets_a_code_answer_when_the_model_invents_a_date():
    """Review item 7: '¿cuándo se resuelve?' after the dispute is opened. An invented date is refused and
    the customer gets the code-written answer instead of a 503."""
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details())
    for text in ("Cafe 25", "sí", "sí"):
        state, _ = asyncio.run(run_turn(state, text, ctx, llm))  # type: ignore[arg-type]
    assert state.phase == Phase.DONE
    invent = _NthSpeech(_details(), n=1, speech=Speech(act="inform", text="Se resolverá el 20 de junio de 2026."))
    state, reply = asyncio.run(run_turn(state, "¿Cuándo se resuelve?", ctx, invent))  # type: ignore[arg-type]
    assert "20 de junio" not in reply and "referencia" in reply


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


def test_clarify_with_no_match_also_falls_back_to_a_code_written_question():
    refused = Speech(act="clarify", text="No lo encuentro. Te cobraremos 900 USD de comisión.")
    llm = _NthSpeech(_details(amount=None), n=1, speech=refused, repeat=2)
    state, reply = asyncio.run(run_turn(_state(), _OPENER, _ctx(exec_rows=[]), llm))  # type: ignore[arg-type]
    assert reply == clarify_fallback("es", None)
    assert state.phase == Phase.CLARIFY
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
    assert state.confirmation == "no"
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
    assert state.confirmation == "no"
    assert state.phase != Phase.CONFIRM_ACT
    assert not any(a.tool == "open_dispute" for a in ctx.cases.list_audit())


def test_a_bare_yes_still_needs_the_model_and_the_hedge_floor():
    """The floor only ever declines. A "yes" is never decided without the model."""
    ctx = _ctx()
    state = _state()
    llm = FakeLLM(_details(), decisions=["no"])
    state, _ = asyncio.run(run_turn(state, "Cafe 25", ctx, llm))  # type: ignore[arg-type]
    state, _ = asyncio.run(run_turn(state, "sí", ctx, llm))  # type: ignore[arg-type]
    assert state.confirmation == "no"  # the model's verdict decides, not the word
