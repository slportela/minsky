"""Agentic mode: the search agent finds the transaction; code confirms, applies the policy and escalates.

The model is scripted. What is under test is everything around it: the sandbox tools, the checks on the agent's
text, the programmatic yes/no, the policy denial with the offer of a person, and the escalation summary.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from minsky_api.agent.agentic import SEARCH_TOOLS, run_agentic_turn
from minsky_api.agent.sandbox import SandboxError
from minsky_api.agent.state import ConversationState, Phase
from minsky_api.config import get_settings
from minsky_api.identity import SessionState, ToolSession
from minsky_api.llm.client import AgentStep, LLMResult, ToolCall
from minsky_api.store.cases_memory import HandoffRecord, InMemoryCasesBackend
from minsky_api.store.models import Customer, CustomerComplaintStats, Product, Transaction
from minsky_api.tools.context import ToolContext
from minsky_api.tools.errors import ToolDenied, ToolError
from minsky_api.tools.history import get_customer_profile, load_history, query_transactions
from minsky_api.tools.schemas import QueryTransactionsArgs

# ---------------------------------------------------------------- fakes


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class FakeDb:
    """Routes by entity: transactions by id or as the whole history, products, customer, complaint stats."""

    def __init__(self, txns: list[Transaction], *, repeat: bool = False, is_card: bool = True) -> None:
        self.txns = {t.transaction_id: t for t in txns}
        self.repeat = repeat
        self.is_card = is_card

    async def get(self, model: type, identity: Any) -> Any:
        if model is Transaction:
            return self.txns.get(identity)
        if model is CustomerComplaintStats:
            return CustomerComplaintStats(
                customer_id="C1",
                complaints_total=2,
                complaints_last_90d=1 if self.repeat else 0,
                is_repeat_complainer=self.repeat,
            )
        if model is Product:
            return Product(
                product_id="P1",
                customer_id="C1",
                is_card=self.is_card,
                product_type="Credit Card",
                product_status="Active",
            )
        if model is Customer:
            return Customer(
                customer_id="C1", first_name="Ana", country="Mexico", segment="Premium", customer_status="Active"
            )
        return None

    async def exec(self, statement: Any) -> _Rows:
        entity = statement.column_descriptions[0]["entity"]
        if entity is Transaction:
            return _Rows(sorted(self.txns.values(), key=lambda t: t.transaction_date or datetime.min, reverse=True))
        if entity is Product:
            return _Rows([await self.get(Product, "P1")])
        return _Rows([])


class StubDetector:
    def detect(self, text: str) -> str:
        return "es"


def txn(
    txn_id: str,
    amount: str,
    merchant: str | None,
    when: datetime,
    *,
    status: str = "Approved",
    kind: str = "Purchase",
    is_fraud: bool = False,
    customer: str = "C1",
) -> Transaction:
    return Transaction(
        transaction_id=txn_id,
        customer_id=customer,
        product_id="P1",
        transaction_type=kind,
        amount=Decimal(amount),
        currency="USD",
        amount_usd=Decimal(amount),
        amount_usd_source="native_usd",
        transaction_date=when,
        merchant_name=merchant,
        transaction_status=status,
        is_fraud=is_fraud,
        fraud_score=Decimal("90.00") if is_fraud else Decimal("5.00"),
    )


def ctx_for(txns: list[Transaction], *, repeat: bool = False, is_card: bool = True) -> ToolContext:
    return ToolContext(
        session=ToolSession(session_id="s1", state=SessionState.VALID, customer_id="C1"),
        db=FakeDb(txns, repeat=repeat, is_card=is_card),  # type: ignore[arg-type]
        cases=InMemoryCasesBackend(),
    )


def say(text: str) -> AgentStep:
    return AgentStep(text=text, tool_calls=(), model="gpt-6-luna", input_tokens=1, output_tokens=1, latency_ms=1.0)


_calls = 0


def call(name: str, **args: Any) -> AgentStep:
    global _calls
    _calls += 1
    return AgentStep(
        text="",
        tool_calls=(ToolCall(call_id=f"call_{_calls}", name=name, arguments=json.dumps(args)),),
        model="gpt-6-luna",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1.0,
    )


def sql(query: str) -> AgentStep:
    return call("query_transactions", sql=query)


class ScriptedAgent:
    """A queue of agent steps, plus the stand-ins for the model's other two jobs (yes/no evidence, summary)."""

    def __init__(self, steps: list[AgentStep], *, narrative: str = "The customer disputed a charge.") -> None:
        self.steps = list(steps)
        self.narrative = narrative
        self.requests: list[list[dict[str, Any]]] = []
        self.instructions: list[str] = []

    async def step(self, instructions: str, items: list[dict[str, Any]], *, tools: Any, **_: Any) -> AgentStep:
        assert tuple(tools) == SEARCH_TOOLS
        # The Responses API rejects a function call that is not answered before the next request.
        answered = {i["call_id"] for i in items if i.get("type") == "function_call_output"}
        assert all(i["call_id"] in answered for i in items if i.get("type") == "function_call")
        self.requests.append(json.loads(json.dumps(items)))
        self.instructions.append(instructions)
        if not self.steps:
            raise AssertionError("the agent was called more times than scripted")
        return self.steps.pop(0)

    async def respond(self, instructions: str, messages: list[dict[str, str]], *, schema: type | None = None, **_: Any):
        name = schema.__name__ if schema else ""
        if name == "Confirmation":
            said = messages[-1]["content"].strip().rstrip(".!?").casefold()
            affirmative = said in {"sí", "si", "sim"} or said.startswith(("sí,", "si,", "sí ", "si "))
            decision = "yes" if affirmative else "no" if said in {"no", "não"} else "unclear"
            parsed = schema(decision=decision)  # type: ignore[misc]
        elif name == "_NarrativeOut":
            parsed = schema(text=self.narrative)  # type: ignore[misc]
        else:
            raise AssertionError(f"unexpected respond call: {name}")
        return LLMResult(text="", parsed=parsed, model="gpt-6-luna", input_tokens=1, output_tokens=1, latency_ms=1.0)


class Chat:
    def __init__(
        self, txns: list[Transaction], agent: ScriptedAgent, *, repeat: bool = False, is_card: bool = True
    ) -> None:
        self.ctx = ctx_for(txns, repeat=repeat, is_card=is_card)
        self.agent = agent
        self.state = ConversationState(conversation_id=uuid4(), customer_id="C1", mode="agentic")

    async def say(self, text: str) -> str:
        self.state, reply = await run_agentic_turn(self.state, text, self.ctx, self.agent, StubDetector())  # type: ignore[arg-type]
        return reply

    def audit(self, tool: str, outcome: str = "ok") -> list[Any]:
        return [a for a in self.ctx.cases.list_audit() if a.tool == tool and a.outcome == outcome]


JUNE_10 = datetime(2026, 6, 10, 9, 30)
FIND_123 = (
    "SELECT transaction_id, amount, merchant_name, transaction_date FROM transactions WHERE abs(amount - 123) < 1"
)
ASK = "¿Me puedes decir en qué comercio fue el cargo y más o menos qué día?"


@pytest.fixture(autouse=True)
def _agentic_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MINSKY_AGENT_MAX_TOOL_CALLS", "5")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------- tools


async def test_the_sandbox_is_built_from_the_session_customers_rows_only():
    ctx = ctx_for([txn("T1", "10", "Cafe", JUNE_10), txn("TX", "99", "Other", JUNE_10, customer="C2")])
    with pytest.raises(ToolError):
        await load_history(ctx)  # a foreign row in the history is a bug: refuse, never serve it
    assert ctx.cases.list_audit()[-1].reason == "foreign_row"


async def test_a_query_runs_for_the_session_customer_and_is_audited():
    ctx = ctx_for([txn("T1", "123.10", "Cafe", JUNE_10)])
    history = await load_history(ctx)
    result = await query_transactions(ctx, history, QueryTransactionsArgs(sql=FIND_123))
    assert result.transaction_ids == ("T1",)
    assert [a.tool for a in ctx.cases.list_audit()] == ["load_history", "query_transactions"]


async def test_a_refused_query_is_audited_as_a_denial_and_gives_the_model_a_reason():
    ctx = ctx_for([txn("T1", "123.10", "Cafe", JUNE_10)])
    history = await load_history(ctx)
    with pytest.raises(SandboxError):
        await query_transactions(ctx, history, QueryTransactionsArgs(sql="SELECT is_fraud FROM transactions"))
    assert ctx.cases.list_audit()[-1].outcome == "denied"


async def test_queries_need_a_valid_session():
    ctx = ctx_for([txn("T1", "10", "Cafe", JUNE_10)])
    history = await load_history(ctx)
    anonymous = ToolContext(
        session=ToolSession(session_id="s2", state=SessionState.ANONYMOUS), db=ctx.db, cases=ctx.cases
    )
    with pytest.raises(ToolDenied):
        await query_transactions(anonymous, history, QueryTransactionsArgs(sql="SELECT 1"))
    with pytest.raises(ToolDenied):
        await load_history(anonymous)
    with pytest.raises(ToolDenied):
        await get_customer_profile(anonymous)


async def test_a_history_cannot_be_queried_from_another_customers_session():
    ctx = ctx_for([txn("T1", "10", "Cafe", JUNE_10)])
    history = await load_history(ctx)
    other = ToolContext(
        session=ToolSession(session_id="s3", state=SessionState.VALID, customer_id="C2"), db=ctx.db, cases=ctx.cases
    )
    with pytest.raises(ToolError):
        await query_transactions(other, history, QueryTransactionsArgs(sql="SELECT 1"))


async def test_the_customer_profile_has_no_name_and_counts_products():
    profile = (await get_customer_profile(ctx_for([]))).profile
    assert (profile.segment, profile.country, profile.products) == ("Premium", "Mexico", {"Credit Card": 1})
    assert "Ana" not in profile.model_dump_json()


# ---------------------------------------------------------------- happy path


async def test_a_near_amount_is_found_confirmed_and_the_dispute_opens_after_one_yes_and_a_recognition():
    chat = Chat(
        [txn("T1", "123.10", "Cafe Sur", JUNE_10)],
        ScriptedAgent([sql(FIND_123), call("propose_transaction", transaction_id="T1", customer_says_not_me=False)]),
    )
    card = await chat.say("No reconozco un cargo de 123 dólares")
    assert chat.state.phase == Phase.CONFIRM_DISPUTE
    # the card is read back from the bank: real amount, real merchant, no internal id
    assert "123.10 USD" in card and "Cafe Sur" in card and "10 de junio de 2026" in card
    assert "T1" not in card
    assert not chat.audit("open_dispute")

    recognize = await chat.say("sí")
    assert chat.state.phase == Phase.RECOGNIZE
    assert "reconoces" in recognize
    assert not chat.audit("open_dispute")

    done = await chat.say("sí")  # recognises it: a dispute about the charge, not about who made it
    assert chat.state.phase == Phase.DONE
    assert "DSP-" in done
    assert len(chat.audit("open_dispute")) == 1
    assert chat.state.customer_says_not_me is False


async def test_the_search_prompt_has_the_date_of_the_data_and_the_agent_sees_the_whole_conversation():
    agent = ScriptedAgent([say(ASK), say(ASK)])
    chat = Chat([txn("T1", "10", "Cafe", JUNE_10)], agent)
    await chat.say("Hay un cargo raro en mi cuenta")
    await chat.say("Fue en un restaurante")
    assert "Hoy es 2026-06-18" in agent.instructions[0]
    second = agent.requests[1]
    assert [i["role"] for i in second] == ["user", "assistant", "user"]
    assert second[1]["content"] == ASK


async def test_the_agent_never_sees_what_the_policy_alone_may_read():
    agent = ScriptedAgent([sql("SELECT * FROM transactions"), say(ASK)])
    chat = Chat([txn("T1", "10", "Cafe", JUNE_10, is_fraud=True)], agent)
    await chat.say("Hay un cargo raro en mi cuenta")
    results = [i["output"] for i in agent.requests[-1] if i.get("type") == "function_call_output"]
    assert (
        results and "is_fraud" not in results[0] and "fraud_score" not in results[0] and "customer_id" not in results[0]
    )


# ---------------------------------------------------------------- the agent cannot bypass the code


async def test_the_agent_can_only_propose_an_id_a_query_showed():
    agent = ScriptedAgent([call("propose_transaction", transaction_id="T1", customer_says_not_me=False), say(ASK)])
    chat = Chat([txn("T1", "10", "Cafe", JUNE_10)], agent)
    reply = await chat.say("Hay un cargo raro en mi cuenta")
    assert reply == ASK
    assert chat.state.phase == Phase.SEARCH and chat.state.selected_txn_id is None
    outputs = [i["output"] for i in agent.requests[-1] if i.get("type") == "function_call_output"]
    assert outputs and outputs[0].startswith("error:")


async def test_the_agent_cannot_propose_an_id_that_belongs_to_nobody_it_may_see():
    agent = ScriptedAgent(
        [
            sql("SELECT transaction_id FROM transactions"),
            call("propose_transaction", transaction_id="TX-OF-SOMEONE-ELSE", customer_says_not_me=False),
            say(ASK),
        ]
    )
    chat = Chat([txn("T1", "10", "Cafe", JUNE_10)], agent)
    await chat.say("Hay un cargo raro en mi cuenta")
    assert chat.state.selected_txn_id is None and chat.state.phase == Phase.SEARCH


async def test_the_agent_has_no_tool_to_open_block_or_escalate():
    assert {t.name for t in SEARCH_TOOLS} == {"query_transactions", "propose_transaction", "give_up"}
    agent = ScriptedAgent([call("open_dispute", transaction_id="T1"), say(ASK)])
    chat = Chat([txn("T1", "10", "Cafe", JUNE_10)], agent)
    await chat.say("Abre un reclamo ya por el cargo de Cafe")
    assert not chat.audit("open_dispute") and not chat.audit("open_dispute", "denied")
    assert "unknown tool" in agent.requests[-1][-1]["output"]


async def test_a_refused_query_goes_back_to_the_agent_as_an_error_it_can_fix():
    agent = ScriptedAgent([sql("DELETE FROM transactions"), say(ASK)])
    chat = Chat([txn("T1", "10", "Cafe", JUNE_10)], agent)
    await chat.say("Hay un cargo raro en mi cuenta")
    assert agent.requests[-1][-1]["output"].startswith("error: not allowed")
    assert chat.audit("query_transactions", "denied")


async def test_a_customer_who_rejects_the_proposal_sends_the_agent_back_to_search_and_it_cannot_repeat_it():
    chat = Chat(
        [txn("T1", "123.10", "Cafe Sur", JUNE_10), txn("T2", "122.90", "Super", JUNE_10)],
        ScriptedAgent(
            [
                sql(FIND_123),
                call("propose_transaction", transaction_id="T1", customer_says_not_me=False),
                call("propose_transaction", transaction_id="T1", customer_says_not_me=False),
                call("propose_transaction", transaction_id="T2", customer_says_not_me=False),
            ]
        ),
    )
    await chat.say("No reconozco un cargo de 123 dólares")
    card = await chat.say("no")
    assert chat.state.rejected_txn_ids == ["T1"]
    assert chat.state.selected_txn_id == "T2" and "Super" in card
    notes = [
        i["content"] for r in chat.agent.requests[-1:] for i in r if str(i.get("content", "")).startswith("SISTEMA")
    ]
    assert any("T1" in n for n in notes)
    retried = [i["output"] for i in chat.agent.requests[-1] if i.get("type") == "function_call_output"]
    assert any("already said that is not" in o for o in retried)


async def test_a_reply_that_is_neither_yes_nor_no_goes_back_to_the_agent_and_never_acts():
    agent = ScriptedAgent(
        [
            sql(FIND_123),
            call("propose_transaction", transaction_id="T1", customer_says_not_me=False),
            say("Entiendo, ¿me puedes decir en qué comercio fue y más o menos qué día?"),
        ]
    )
    chat = Chat([txn("T1", "123.10", "Cafe Sur", JUNE_10)], agent)
    await chat.say("No reconozco un cargo de 123 dólares")
    reply = await chat.say("no es ese, es otro de unos 80")
    assert chat.state.phase == Phase.SEARCH and chat.state.selected_txn_id is None
    assert "comercio" in reply
    note = agent.requests[-1][-1]["content"]
    assert note.startswith("SISTEMA") and "T1" in note and "en vez de sí o no" in note
    assert chat.state.rejected_txn_ids == []  # not a flat no: the agent may propose it again
    assert not chat.audit("open_dispute")


async def test_a_yes_with_extra_words_asks_for_a_plain_yes_instead_of_acting_or_calling_the_agent():
    agent = ScriptedAgent([sql(FIND_123), call("propose_transaction", transaction_id="T1", customer_says_not_me=False)])
    chat = Chat([txn("T1", "123.10", "Cafe Sur", JUNE_10)], agent)
    await chat.say("No reconozco un cargo de 123 dólares")
    reply = await chat.say("sí, ese mismo")  # the agent has no more scripted steps: it must not be called
    assert chat.state.phase == Phase.CONFIRM_DISPUTE and "sí o no" in reply
    assert not chat.audit("open_dispute")


# ---------------------------------------------------------------- the agent's words are checked


@pytest.mark.parametrize(
    "bad",
    [
        "Listo, ya abrí tu reclamo por ese cargo y quedará resuelto pronto.",
        "Tu tarjeta quedó bloqueada y te devolvimos el dinero de ese movimiento.",
        "Ya pasé tu caso a un asesor que te llamará mañana mismo.",
        "Ese cargo fue de 999.99 dólares según lo que veo en tu cuenta de hoy.",
        "Encontré el cargo, aplica la regla D07 por el monto de tu cuenta.",
    ],
)
async def test_a_reply_that_claims_an_action_or_invents_a_fact_never_reaches_the_customer(bad):
    agent = ScriptedAgent([say(bad), say(ASK)])
    chat = Chat([txn("T1", "10", "Cafe", JUNE_10)], agent)
    reply = await chat.say("Hay un cargo raro en mi cuenta")
    assert reply == ASK
    assert "SISTEMA" in agent.requests[1][-1]["content"]
    assert bad not in [m for _, m in chat.state.messages]


@pytest.mark.parametrize(
    "bad",
    [
        "Encontré un cargo de 123.1 en Amazon del 10 de junio. ¿Es ese?",
        "No vi nada con ese monto, pero hay uno en Starbucks. ¿Es ese?",
    ],
)
async def test_a_merchant_the_agent_made_up_never_reaches_the_customer(bad):
    agent = ScriptedAgent([sql(FIND_123), say(bad), say(ASK)])
    chat = Chat([txn("T1", "123.10", "Cafe Sur", JUNE_10)], agent)
    reply = await chat.say("No reconozco un cargo de 123 dólares")
    assert reply == ASK
    assert "the name" in agent.requests[-1][-1]["content"]


@pytest.mark.parametrize(
    "good",
    [
        "Encontré un cargo de 123.1 USD en Café Sur del 10 de junio. ¿Es ese?",
        "No vi 123.00 exacto, pero sí 123.1 en CAFE SUR. ¿Es ese?",
        "Encontré uno en Cafe Sur. Cafe Sur cobró 123.1 USD, ¿es ese?",
        "¿Fue en Cafe Sur o en otro lado? Dime y lo busco.",
    ],
)
async def test_real_merchants_are_allowed_whatever_the_accents_case_or_position(good):
    agent = ScriptedAgent([sql(FIND_123), say(good)])
    chat = Chat([txn("T1", "123.10", "Cafe Sur", JUNE_10)], agent)
    reply = await chat.say("No reconozco un cargo de 123 dólares")
    assert reply == good


async def test_a_merchant_the_customer_named_may_be_repeated():
    agent = ScriptedAgent([sql(FIND_123), say("No encontré nada en Netflix con ese monto. ¿Recuerdas el día?")])
    chat = Chat([txn("T1", "123.10", "Cafe Sur", JUNE_10)], agent)
    reply = await chat.say("No reconozco un cargo de 123 dólares de Netflix")
    assert "Netflix" in reply


async def test_an_agent_that_keeps_failing_the_checks_gets_a_code_written_question_not_an_error():
    bad = "Listo, ya abrí tu reclamo por ese cargo y quedará resuelto pronto."
    chat = Chat([txn("T1", "10", "Cafe", JUNE_10)], ScriptedAgent([say(bad), say(bad)]))
    reply = await chat.say("Hay un cargo raro en mi cuenta")
    assert "comercio" in reply and chat.state.phase == Phase.SEARCH


async def test_amounts_and_dates_the_agent_read_in_a_query_are_allowed_in_its_reply():
    agent = ScriptedAgent(
        [
            sql(FIND_123),
            say("No encontré 123.00 exacto, pero sí un cargo de 123.1 en Cafe Sur del 10 de junio. ¿Es ese?"),
        ]
    )
    chat = Chat([txn("T1", "123.10", "Cafe Sur", JUNE_10)], agent)
    reply = await chat.say("No reconozco un cargo de 123 dólares")
    assert "Cafe Sur" in reply and chat.state.phase == Phase.SEARCH


# ---------------------------------------------------------------- the policy denies, a person is offered


def _to_card(txns: list[Transaction], *, repeat: bool = False, not_me: bool = False, is_card: bool = True) -> Chat:
    return Chat(
        txns,
        ScriptedAgent(
            [
                sql("SELECT transaction_id FROM transactions"),
                call("propose_transaction", transaction_id=txns[0].transaction_id, customer_says_not_me=not_me),
            ],
            narrative="The customer wanted to dispute a charge and asked for an agent after the denial.",
        ),
        repeat=repeat,
        is_card=is_card,
    )


@pytest.mark.parametrize(
    ("row", "rule", "heard"),
    [
        (txn("T1", "30", "Cafe", datetime(2025, 1, 5, 9, 0)), "D05", "120 días"),
        (txn("T1", "900", "TV", JUNE_10), "D07", "por el monto"),
        (txn("T1", "30", "Cafe", JUNE_10, status="Declined"), "D01", "rechazado"),
        (txn("T1", "30", "Cafe", JUNE_10, status="Reversed"), "D02", "revertido"),
        (txn("T1", "30", "Cafe", JUNE_10, status="Pending"), "D03", "pendiente"),
    ],
)
async def test_when_the_policy_says_no_the_customer_hears_why_and_is_offered_a_person(row, rule, heard):
    chat = _to_card([row])
    await chat.say("No reconozco un cargo")
    await chat.say("sí")
    reply = await chat.say("sí")  # recognises it
    assert (chat.state.rule_id or "").startswith(rule)
    assert chat.state.phase == Phase.OFFER_ESCALATION
    assert heard in reply and "asesor" in reply and "sí o no" in reply
    assert rule not in reply  # rule ids stay internal
    assert not chat.audit("open_dispute") and not _handoff_ids(chat)


async def test_a_repeat_complainer_is_denied_too():
    chat = _to_card([txn("T1", "30", "Cafe", JUNE_10)], repeat=True)
    await chat.say("No reconozco un cargo")
    await chat.say("sí")
    reply = await chat.say("sí")
    assert (
        (chat.state.rule_id or "").startswith("D08")
        and chat.state.phase == Phase.OFFER_ESCALATION
        and "asesor" in reply
    )


async def test_an_already_disputed_charge_gives_the_reference():
    chat = _to_card([txn("T1", "30", "Cafe", JUNE_10)])
    chat.ctx.cases.create_dispute(customer_id="C1", transaction_id="T1", reason="unrecognized_charge")
    await chat.say("No reconozco un cargo")
    await chat.say("sí")
    reply = await chat.say("sí")
    assert (chat.state.rule_id or "").startswith("D04") and "DSP-" in reply and chat.state.existing_dispute_id


async def test_yes_to_a_person_creates_the_handoff_with_the_summary_and_reports_its_reference():
    chat = _to_card([txn("T1", "900", "TV", JUNE_10)])
    await chat.say("No reconozco un cargo")
    await chat.say("sí")
    await chat.say("sí")
    reply = await chat.say("sí")
    assert chat.state.phase == Phase.DONE
    handoffs = [h for h in (chat.ctx.cases.get_handoff(i) for i in _handoff_ids(chat)) if h]
    assert len(handoffs) == 1 and handoffs[0].handoff_id in reply
    handoff = handoffs[0]
    assert handoff.reason == "customer_requested_after_denial" and (handoff.rule_id or "").startswith("D07")
    facts = handoff.facts
    assert facts["transaction_id"] == "T1"
    context = facts["verified_context"]
    assert context["customer"]["segment"] == "Premium" and context["customer"]["country"] == "Mexico"
    assert context["customer"]["complaints_total"] == 2
    assert context["denial"]["rule_id"].startswith("D07") and "monto" in context["denial"]["told_to_customer"]
    assert context["transaction"]["transaction_id"] == "T1"
    assert facts["narrative"]["source"] == "model" and "not verified" in facts["narrative"]["note"]
    assert "Ana" not in json.dumps(facts)  # no name in the summary


async def test_the_case_queue_gets_the_verified_context_apart_from_what_the_customer_said():
    chat = _to_card([txn("T1", "900", "TV", JUNE_10)])
    for text in ("No reconozco un cargo", "sí", "sí", "sí"):
        await chat.say(text)
    case = chat.ctx.cases.get_case(_handoff_ids(chat)[0])
    assert case is not None
    assert case.facts["context"]["customer"]["segment"] == "Premium"
    assert case.facts["context"]["denial"]["rule_id"].startswith("D07")
    assert "verified_context" not in case.facts["customer_said"]
    assert case.facts["customer_said"]["narrative"]["source"] == "model"
    assert case.facts["verified"]["amount_usd"] == "900"


async def test_a_narrative_with_an_invented_figure_is_replaced_by_the_customers_own_words():
    chat = Chat(
        [txn("T1", "900", "TV", JUNE_10)],
        ScriptedAgent(
            [
                sql("SELECT transaction_id FROM transactions"),
                call("propose_transaction", transaction_id="T1", customer_says_not_me=False),
            ],
            narrative="The customer was charged 4321.55 and wants it back.",
        ),
    )
    for text in ("No reconozco un cargo", "sí", "sí", "sí"):
        await chat.say(text)
    facts = _handoff(chat).facts
    assert facts["narrative"]["source"] == "transcript_excerpt"
    assert "No reconozco un cargo" in facts["narrative"]["text"]


async def test_no_to_a_person_ends_without_a_handoff():
    chat = _to_card([txn("T1", "900", "TV", JUNE_10)])
    for text in ("No reconozco un cargo", "sí", "sí"):
        await chat.say(text)
    reply = await chat.say("no")
    assert chat.state.phase == Phase.DONE and not _handoff_ids(chat) and "Entendido" in reply


# ---------------------------------------------------------------- fraud


async def test_a_customer_who_does_not_recognise_the_charge_gets_the_card_block_offer_and_a_fraud_handoff():
    chat = _to_card([txn("T1", "30", "Cafe", JUNE_10)])
    await chat.say("No reconozco un cargo")
    await chat.say("sí")
    offer = await chat.say("no")  # "do you recognise it?" -> no
    assert chat.state.phase == Phase.CARD_OFFER and chat.state.customer_says_not_me
    assert "bloqueo tu tarjeta" in offer.lower()
    assert not chat.audit("block_card")
    done = await chat.say("sí")
    assert len(chat.audit("block_card")) == 1 and "bloqueada" in done and chat.state.phase == Phase.DONE
    handoff = _handoff(chat)
    assert handoff.reason == "possible_fraud" and handoff.actions == ("card_blocked:P1",)
    assert handoff.facts["verified_context"]["customer"]["segment"] == "Premium"


async def test_when_the_agent_heard_that_it_was_not_the_customer_the_recognition_question_is_skipped():
    chat = _to_card([txn("T1", "30", "Cafe", JUNE_10)], not_me=True)
    await chat.say("Yo no hice esa compra")
    reply = await chat.say("sí")
    assert chat.state.phase == Phase.CARD_OFFER and "bloqueo" in reply.lower()


async def test_the_banks_fraud_flag_routes_to_fraud_even_when_the_customer_recognises_the_charge():
    chat = _to_card([txn("T1", "30", "Cafe", JUNE_10, is_fraud=True)])
    for text in ("No reconozco un cargo", "sí", "sí"):
        await chat.say(text)
    assert chat.state.phase == Phase.CARD_OFFER


async def test_declining_the_block_still_reaches_the_fraud_team_without_claiming_a_block():
    chat = _to_card([txn("T1", "30", "Cafe", JUNE_10, is_fraud=True)])
    for text in ("No reconozco un cargo", "sí", "sí"):
        await chat.say(text)
    reply = await chat.say("no")
    assert not chat.audit("block_card") and "bloqueada" not in reply
    assert _handoff(chat).reason == "possible_fraud_no_block"


async def test_fraud_on_something_that_is_not_a_card_goes_straight_to_the_fraud_team():
    chat = _to_card([txn("T1", "30", "Cafe", JUNE_10, kind="Transfer", is_fraud=True)], is_card=False)
    for text in ("No reconozco un cargo", "sí", "sí"):
        reply = await chat.say(text)
    assert chat.state.phase == Phase.DONE and "referencia" in reply.lower()


# ---------------------------------------------------------------- giving up


async def test_when_the_agent_gives_up_the_customer_is_offered_a_person_and_the_summary_says_why():
    chat = Chat([txn("T1", "10", "Cafe", JUNE_10)], ScriptedAgent([call("give_up", reason="not_found")]))
    offer = await chat.say("Hay un cargo raro en mi cuenta")
    assert chat.state.phase == Phase.OFFER_ESCALATION and "No logré identificar" in offer and "asesor" in offer
    done = await chat.say("sí")
    handoff = _handoff(chat)
    assert handoff.reason == "search_exhausted" and handoff.rule_id is None and handoff.handoff_id in done
    assert handoff.facts["verified_context"]["customer"]["segment"] == "Premium"


async def test_out_of_scope_is_offered_a_person_too():
    chat = Chat([], ScriptedAgent([call("give_up", reason="out_of_scope")]))
    offer = await chat.say("Quiero saber el saldo de mi cuenta")
    assert "no es algo que pueda resolver" in offer
    await chat.say("sí")
    assert _handoff(chat).reason == "out_of_scope"


async def test_an_agent_that_loops_on_queries_is_stopped_and_after_a_few_times_a_person_is_offered():
    loop = [sql("SELECT count(*) FROM transactions") for _ in range(6)]
    chat = Chat([txn("T1", "10", "Cafe", JUNE_10)], ScriptedAgent(loop + loop + loop))
    first = await chat.say("Hay un cargo raro en mi cuenta")
    assert "comercio" in first and chat.state.phase == Phase.SEARCH
    assert len(chat.agent.requests) == 6  # 1 + the budget of 5 tool calls, then stop
    await chat.say("No sé")
    third = await chat.say("De verdad no sé")
    assert chat.state.phase == Phase.OFFER_ESCALATION and "asesor" in third


async def test_the_turn_limit_escalates_with_a_summary(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MINSKY_MAX_TURNS", "1")
    get_settings.cache_clear()
    chat = Chat([txn("T1", "10", "Cafe", JUNE_10)], ScriptedAgent([say(ASK)]))
    await chat.say("Hay un cargo raro en mi cuenta")
    reply = await chat.say("No sé")
    assert _handoff(chat).reason == "max_turns" and "referencia" in reply.lower()


# ---------------------------------------------------------------- session and state guards


async def test_an_expired_session_is_refused():
    chat = Chat([], ScriptedAgent([]))
    chat.ctx = ToolContext(
        session=ToolSession(session_id="s", state=SessionState.EXPIRED, customer_id="C1"),
        db=chat.ctx.db,
        cases=chat.ctx.cases,
    )
    with pytest.raises(PermissionError):
        await chat.say("hola")


async def test_a_conversation_of_another_customer_is_refused():
    chat = Chat([], ScriptedAgent([]))
    chat.state = ConversationState(conversation_id=uuid4(), customer_id="C2", mode="agentic")
    with pytest.raises(PermissionError):
        await chat.say("hola")


async def test_after_the_case_is_settled_nothing_else_is_done():
    chat = _to_card([txn("T1", "900", "TV", JUNE_10)])
    for text in ("No reconozco un cargo", "sí", "sí", "sí"):
        await chat.say(text)
    before = len(chat.ctx.cases.list_audit())
    reply = await chat.say("¿y ahora qué?")
    assert chat.state.phase == Phase.DONE and "ya quedó registrado" in reply
    assert len(chat.ctx.cases.list_audit()) == before


async def test_the_mode_is_fixed_when_the_conversation_starts():
    assert ConversationState(conversation_id=uuid4(), customer_id="C1").mode == "workflow"
    assert get_settings().agent_mode == "workflow"


def _handoff(chat: Chat) -> HandoffRecord:
    record = chat.ctx.cases.get_handoff(_handoff_ids(chat)[0])
    assert record is not None
    return record


def _handoff_ids(chat: Chat) -> list[str]:
    return [c.case_id for c in chat.ctx.cases.list_cases() if c.kind == "handoff"]
