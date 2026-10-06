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

    def __init__(
        self,
        steps: list[AgentStep],
        *,
        narrative: str = "The customer disputed a charge.",
        classified: dict[str, str] | None = None,
    ) -> None:
        self.classified = classified or {}  # what the classifier model says for a given reply, whatever the words
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
            affirmative = said in {"sí", "si", "sim"} or said.startswith(("sí,", "si,", "sí ", "si ", "sim,", "sim "))
            decision = "yes" if affirmative else "no" if said in {"no", "não"} else "unclear"
            decision = self.classified.get(messages[-1]["content"], decision)
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
    assert chat.state.phase == Phase.CONFIRM_DISPUTE and "«sí» o «no»" in reply  # how to answer, from code
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
    assert chat.state.phase == Phase.DONE and not _handoff_ids(chat)
    assert chat.state.terminal is not None and chat.state.terminal.outcome == "informed"
    assert "No abrí ningún reclamo nuevo" in reply and "nueva conversación" in reply


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


async def test_when_the_agent_gives_up_after_asking_the_customer_is_offered_a_person_and_the_summary_says_why():
    chat = Chat(
        [txn("T1", "10", "Cafe", JUNE_10)],
        ScriptedAgent([say(ASK), call("give_up", reason="not_found")]),
    )
    await chat.say("Hay un cargo raro en mi cuenta")
    offer = await chat.say("No me acuerdo de nada más")
    assert chat.state.phase == Phase.OFFER_ESCALATION and "No logré encontrar" in offer and "asesor" in offer
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


async def test_the_turn_limit_ends_without_a_case_when_nothing_was_confirmed(monkeypatch: pytest.MonkeyPatch):
    """The workflow's rule: a case goes to a person only after the customer confirmed the charge or asked for one."""
    monkeypatch.setenv("MINSKY_MAX_TURNS", "1")
    get_settings.cache_clear()
    chat = Chat([txn("T1", "10", "Cafe", JUNE_10)], ScriptedAgent([say(ASK)]))
    await chat.say("Hay un cargo raro en mi cuenta")
    reply = await chat.say("No sé")
    assert not _handoff_ids(chat) and chat.state.terminal is not None and chat.state.terminal.outcome == "no_case"
    assert "No abrí ningún caso ni te pasé con nadie" in reply


async def test_the_turn_limit_sends_the_case_to_a_person_once_the_charge_was_confirmed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MINSKY_MAX_TURNS", "2")
    get_settings.cache_clear()
    chat = _to_card([txn("T1", "30", "Cafe", JUNE_10)])
    await chat.say("No reconozco un cargo")
    await chat.say("sí")  # confirmed; the recognition question is waiting for its answer
    reply = await chat.say("mmm")  # turn 3 is over the limit
    assert _handoff(chat).reason == "max_turns" and _handoff(chat).handoff_id in reply


async def test_after_the_case_is_settled_nothing_else_is_done():
    chat = _to_card([txn("T1", "900", "TV", JUNE_10)])
    for text in ("No reconozco un cargo", "sí", "sí", "sí"):
        await chat.say(text)
    before, calls = len(chat.ctx.cases.list_audit()), len(chat.agent.requests)
    reply = await chat.say("¿y ahora qué?")
    assert chat.state.phase == Phase.DONE
    assert f"referencia {_handoff(chat).handoff_id}" in reply and "inicia una nueva conversación" in reply
    assert len(chat.ctx.cases.list_audit()) == before and len(chat.agent.requests) == calls  # no tool, no model


async def test_the_mode_is_fixed_when_the_conversation_starts():
    assert ConversationState(conversation_id=uuid4(), customer_id="C1").mode == "workflow"
    assert get_settings().agent_mode == "workflow"


def _handoff(chat: Chat) -> HandoffRecord:
    record = chat.ctx.cases.get_handoff(_handoff_ids(chat)[0])
    assert record is not None
    return record


def _handoff_ids(chat: Chat) -> list[str]:
    return [c.case_id for c in chat.ctx.cases.list_cases() if c.kind == "handoff"]


# ---------------------------------------------------------------- the note on a proposal


async def test_a_note_on_how_the_charge_differs_is_shown_before_the_card():
    note = "No encontré 123.00 exacto, pero sí uno de 123.1 en Cafe Sur."
    agent = ScriptedAgent(
        [sql(FIND_123), call("propose_transaction", transaction_id="T1", customer_says_not_me=False, note=note)]
    )
    chat = Chat([txn("T1", "123.10", "Cafe Sur", JUNE_10)], agent)
    card = await chat.say("No reconozco un cargo de 123 dólares")
    assert card.startswith(note) and "123.10 USD" in card and "Responde sí o no" in card
    assert chat.state.phase == Phase.CONFIRM_DISPUTE and chat.state.pending_question == card


async def test_a_proposal_without_a_note_is_the_card_alone():
    agent = ScriptedAgent([sql(FIND_123), call("propose_transaction", transaction_id="T1", customer_says_not_me=False)])
    chat = Chat([txn("T1", "123.10", "Cafe Sur", JUNE_10)], agent)
    card = await chat.say("No reconozco un cargo de 123 dólares")
    assert card.startswith("Encontré este movimiento")


@pytest.mark.parametrize(
    ("note", "why"),
    [
        ("Ya abrí tu reclamo por ese cargo y quedará resuelto pronto.", "claims an action"),
        ("Es un cargo de 999.99 dólares del día de hoy.", "999.99"),
        ("Es de Amazon y no de Cafe Sur.", "Amazon"),
        ("x" * 241, "longer than 240"),
    ],
)
async def test_a_note_that_claims_an_action_or_invents_a_fact_refuses_the_proposal_and_the_agent_can_retry(note, why):
    agent = ScriptedAgent(
        [
            sql(FIND_123),
            call("propose_transaction", transaction_id="T1", customer_says_not_me=False, note=note),
            call("propose_transaction", transaction_id="T1", customer_says_not_me=False),
        ]
    )
    chat = Chat([txn("T1", "123.10", "Cafe Sur", JUNE_10)], agent)
    card = await chat.say("No reconozco un cargo de 123 dólares")
    refusal = next(
        i["output"]
        for i in agent.requests[-1]
        if i.get("type") == "function_call_output" and "note" in str(i["output"])
    )
    assert refusal.startswith("error:") and why in refusal
    assert note not in card  # the refused note never reaches the customer
    assert chat.state.phase == Phase.CONFIRM_DISPUTE  # the retry without a note went through


async def test_markdown_emphasis_in_a_note_is_stripped():
    agent = ScriptedAgent(
        [
            sql(FIND_123),
            call(
                "propose_transaction",
                transaction_id="T1",
                customer_says_not_me=False,
                note="Es de **123.1** en Cafe Sur.",
            ),
        ]
    )
    chat = Chat([txn("T1", "123.10", "Cafe Sur", JUNE_10)], agent)
    card = await chat.say("No reconozco un cargo de 123 dólares")
    assert card.startswith("Es de 123.1 en Cafe Sur.") and "*" not in card


# ---------------------------------------------------------------- a slow query must not freeze other conversations

_HEAVY = "SELECT count(*) FROM transactions a, transactions b, transactions c, transactions d, transactions e"


async def _max_stall_while(awaitable) -> tuple[float, object]:
    """The longest gap between ticks of a 10 ms heartbeat while `awaitable` runs: how long the event loop froze."""
    import asyncio
    import time

    stalls: list[float] = []
    running = True

    async def heartbeat() -> None:
        last = time.monotonic()
        while running:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            stalls.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)  # let the heartbeat settle before the work starts
    try:
        outcome = await asyncio.gather(awaitable, return_exceptions=True)
    finally:
        running = False
        await beat
    return max(stalls), outcome[0]


async def test_a_query_that_runs_into_its_deadline_does_not_freeze_the_event_loop():
    from minsky_api.agent.sandbox import DEADLINE_S

    rows = [txn(f"T{i}", "1.00", "Cafe", JUNE_10) for i in range(150)]
    ctx = ctx_for(rows)
    history = await load_history(ctx)
    stall, outcome = await _max_stall_while(query_transactions(ctx, history, QueryTransactionsArgs(sql=_HEAVY)))
    assert isinstance(outcome, SandboxError)  # the deadline cut it, as before
    assert DEADLINE_S >= 0.5 and stall < DEADLINE_S / 2, f"the event loop froze for {stall:.2f}s"


async def test_several_slow_queries_run_side_by_side_not_one_after_the_other():
    import asyncio
    import time

    rows = [txn(f"T{i}", "1.00", "Cafe", JUNE_10) for i in range(150)]
    ctx = ctx_for(rows)
    histories = [await load_history(ctx) for _ in range(3)]
    started = time.monotonic()
    results = await asyncio.gather(
        *(query_transactions(ctx, h, QueryTransactionsArgs(sql=_HEAVY)) for h in histories), return_exceptions=True
    )
    elapsed = time.monotonic() - started
    assert all(isinstance(r, SandboxError) for r in results)
    assert elapsed < 1.2, f"three 0.5 s deadlines took {elapsed:.2f}s: they ran one after another"


async def test_the_agent_cannot_give_up_on_not_found_before_it_has_asked_the_customer_anything():
    """Live run 4: the agent offered a person without one question in 1 of 3 trials, against its prompt."""
    agent = ScriptedAgent(
        [call("give_up", reason="not_found"), say("No encontré nada con eso. ¿Recuerdas el comercio o el día?")]
    )
    chat = Chat([txn("T1", "10", "Cafe", JUNE_10)], agent)
    reply = await chat.say("Hay un cargo raro en mi cuenta")
    assert reply.startswith("No encontré nada") and chat.state.phase == Phase.SEARCH  # it asked, and nobody was offered
    refusal = agent.requests[-1][-1]["output"]
    assert refusal.startswith("error:") and "asked the customer" in refusal
    assert chat.state.acts[-1] == "clarify" and "offer_handoff" not in chat.state.acts


async def test_after_one_question_a_second_give_up_goes_through_even_in_the_same_conversation():
    agent = ScriptedAgent([say(ASK), call("give_up", reason="not_found")])
    chat = Chat([txn("T1", "10", "Cafe", JUNE_10)], agent)
    await chat.say("Hay un cargo raro en mi cuenta")
    await chat.say("No sé")
    assert chat.state.phase == Phase.OFFER_ESCALATION


async def test_out_of_scope_needs_no_question_first():
    chat = Chat([], ScriptedAgent([call("give_up", reason="out_of_scope")]))
    await chat.say("Quiero saber el saldo de mi cuenta")
    assert chat.state.phase == Phase.OFFER_ESCALATION


# ---------------------------------------------------------------- closing, aligned with the workflow's terminal state
#
# The workflow (main) ends a conversation in a terminal state: from then on every message gets a code-written status,
# the reference and "start a new conversation", with no model and no tool. Nothing reaches a person before the
# customer confirmed a charge or accepted one. The agentic mode follows the same rules.

_NOT_FOUND = [say(ASK), call("give_up", reason="not_found")]


async def _offered_a_person(extra_steps: list | None = None) -> Chat:
    chat = Chat([txn("T1", "10", "Cafe", JUNE_10)], ScriptedAgent([*_NOT_FOUND, *(extra_steps or [])]))
    await chat.say("Hay un cargo raro en mi cuenta")
    await chat.say("No me acuerdo")
    assert chat.state.phase == Phase.OFFER_ESCALATION
    return chat


async def test_the_offer_apologises_and_says_what_a_person_would_do_not_just_that_nothing_was_found():
    offer = (await _offered_a_person()).state.pending_question or ""
    assert "lamento" in offer and "asesor" in offer and offer.endswith("Responde sí o no.")


async def test_accepting_a_person_says_what_they_have_and_closes_with_the_real_reference():
    chat = await _offered_a_person()
    done = await chat.say("sí")
    handoff = _handoff(chat)
    assert handoff.handoff_id in done and "ya tiene el resumen" in done and "no hace falta que repitas nada" in done
    assert chat.state.terminal is not None and chat.state.terminal.outcome == "handoff"
    assert chat.state.terminal.reference == handoff.handoff_id and chat.state.handoff_accepted
    calls = len(chat.agent.requests)
    again = await chat.say("gracias")
    assert f"referencia {handoff.handoff_id}" in again and "inicia una nueva conversación" in again
    assert len(chat.agent.requests) == calls  # terminal: no model


async def test_a_block_that_was_read_back_is_said_before_the_handoff_and_one_that_was_not_is_not():
    chat = _to_card([txn("T1", "30", "Cafe", JUNE_10)], not_me=True)
    for text in ("No reconozco un cargo", "sí"):
        await chat.say(text)
    done = await chat.say("sí")
    assert done.startswith("Tu tarjeta ya está bloqueada") and _handoff(chat).handoff_id in done
    assert chat.state.terminal is not None and chat.state.terminal.card_blocked
    declined = _to_card([txn("T1", "30", "Cafe", JUNE_10)], not_me=True)
    for text in ("No reconozco un cargo", "sí"):
        await declined.say(text)
    done = await declined.say("no")
    assert "bloqueada" not in done and "ya tiene el resumen" in done


async def test_declining_the_first_offer_asks_for_the_charge_once_more_and_nothing_is_registered():
    """The workflow's rule: a no goes back to asking for the charge; the offer may come once more."""
    chat = await _offered_a_person([say("Claro. ¿En qué comercio fue el cargo?")])
    reply = await chat.say("no")
    assert reply == "Entendido, no te paso con nadie. Dime el monto, el comercio o la fecha del cargo."
    assert chat.state.phase == Phase.SEARCH and chat.state.terminal is None and not _handoff_ids(chat)
    assert chat.state.handoff_offers == 1
    resumed = await chat.say("gracias")  # a later message goes to the agent: nothing was closed
    assert resumed == "Claro. ¿En qué comercio fue el cargo?"


async def test_declining_the_last_offer_ends_without_a_case_with_the_status_from_code():
    chat = await _offered_a_person([call("give_up", reason="not_found")])
    await chat.say("no")  # offer 1 declined: back to the search
    second = await chat.say("no sé")
    assert chat.state.handoff_offers == 2 and "asesor" in second  # the second and last offer
    ended = await chat.say("no")
    assert chat.state.terminal is not None and chat.state.terminal.outcome == "no_case" and not _handoff_ids(chat)
    assert ended.startswith("No abrí ningún caso ni te pasé con nadie.")
    assert ended.endswith("Si quieres disputar otro cargo, inicia una nueva conversación.")
    calls = len(chat.agent.requests)
    assert await chat.say("espera") == ended and len(chat.agent.requests) == calls


async def test_declining_a_person_after_a_denial_ends_the_conversation_informed():
    chat = _to_card([txn("T1", "30", "Cafe", JUNE_10)])
    chat.ctx.cases.create_dispute(customer_id="C1", transaction_id="T1", reason="unrecognized_charge")
    for text in ("No reconozco un cargo", "sí", "sí"):
        await chat.say(text)
    assert chat.state.denial_text and chat.state.existing_dispute_id
    ended = await chat.say("no")
    assert chat.state.terminal is not None and chat.state.terminal.outcome == "informed"
    assert "No abrí ningún reclamo nuevo" in ended and chat.state.existing_dispute_id in ended
    assert not _handoff_ids(chat)


async def test_a_dispute_that_was_opened_is_the_terminal_reference():
    chat = Chat(
        [txn("T1", "123.10", "Cafe Sur", JUNE_10)],
        ScriptedAgent([sql(FIND_123), call("propose_transaction", transaction_id="T1", customer_says_not_me=False)]),
    )
    for text in ("No reconozco un cargo de 123 dólares", "sí", "sí"):
        await chat.say(text)
    terminal = chat.state.terminal
    assert (
        terminal is not None and terminal.outcome == "dispute_opened" and (terminal.reference or "").startswith("DSP-")
    )
    assert f"Tu reclamo está abierto con la referencia {terminal.reference}" in await chat.say("gracias")


async def test_a_handoff_is_refused_before_the_customer_confirmed_a_charge_or_accepted_a_person():
    from minsky_api.agent.agentic import _escalate

    chat = Chat([txn("T1", "10", "Cafe", JUNE_10)], ScriptedAgent([]))
    chat.state.language = "es"
    with pytest.raises(RuntimeError, match="before the customer confirmed"):
        await _escalate(chat.ctx, chat.state, chat.agent, reason="anything")  # type: ignore[arg-type]
    assert not _handoff_ids(chat)


# ---- replies that are neither yes nor no


async def test_a_yes_with_extra_words_to_the_offer_says_how_to_answer():
    chat = await _offered_a_person()
    reply = await chat.say("sí, por favor")
    assert chat.state.phase == Phase.OFFER_ESCALATION and not _handoff_ids(chat)
    assert reply.startswith("No me quedó claro.") and reply.endswith(
        "En esta parte del proceso solo puedes responder «sí» o «no»."
    )


async def test_three_replies_in_a_row_that_are_not_yes_or_no_to_the_offer_end_without_a_case():
    chat = await _offered_a_person()
    for text in ("sí, por favor", "sí claro"):  # an affirmative with extra words is not a yes
        assert "solo puedes responder" in await chat.say(text)
    ended = await chat.say("sí dale")
    assert chat.state.terminal is not None and chat.state.terminal.outcome == "no_case" and not _handoff_ids(chat)
    assert "No abrí ningún caso" in ended


async def test_three_free_text_replies_to_the_card_offer_a_person_instead_of_going_round_for_ever():
    agent = ScriptedAgent(
        [sql(FIND_123)] + [call("propose_transaction", transaction_id="T1", customer_says_not_me=False)] * 3
    )
    chat = Chat([txn("T1", "123.10", "Cafe Sur", JUNE_10)], agent)
    await chat.say("No reconozco un cargo de 123 dólares")
    await chat.say("tal vez")  # 1: the agent takes it and proposes again
    await chat.say("no sé")  # 2
    offer = await chat.say("quizás")  # 3: the limit; the agent is not called again
    assert chat.state.phase == Phase.OFFER_ESCALATION and "asesor" in offer and not _handoff_ids(chat)
    assert chat.state.escalation_reason == "unclear_confirmation"
    done = await chat.say("sí")  # only a plain yes sends it to a person
    assert _handoff(chat).reason == "unclear_confirmation" and _handoff(chat).handoff_id in done


async def test_a_plain_answer_resets_the_count_of_free_text_replies_to_the_card():
    agent = ScriptedAgent(
        [sql(FIND_123)] + [call("propose_transaction", transaction_id="T1", customer_says_not_me=False)] * 2
    )
    chat = Chat([txn("T1", "123.10", "Cafe Sur", JUNE_10)], agent)
    await chat.say("No reconozco un cargo de 123 dólares")
    await chat.say("tal vez")
    assert chat.state.unclear_detours == 1
    await chat.say("sí")
    assert chat.state.unclear_detours == 0


async def test_unclear_replies_after_the_charge_was_confirmed_send_the_case_with_the_unanswered_question():
    chat = _to_card([txn("T1", "30", "Cafe", JUNE_10)])
    await chat.say("No reconozco un cargo")
    await chat.say("sí")  # confirmed; now "do you recognise it?"
    first = await chat.say("no sé")
    assert "solo puedes responder" in first
    await chat.say("mmm")
    done = await chat.say("ya")
    handoff = _handoff(chat)
    assert handoff.reason == "unclear_confirmation" and handoff.handoff_id in done
    assert "reconoces" in str(handoff.facts["unanswered_question"]) and handoff.facts["last_customer_replies"] == [
        "no sé",
        "mmm",
        "ya",
    ]


# ---- the closing in Portuguese


async def _offered_a_person_in_portuguese(extra_steps: list | None = None) -> Chat:
    steps = [
        say("Não encontrei nada com isso. Você lembra do estabelecimento ou do dia da cobrança?"),
        call("give_up", reason="not_found"),
        *(extra_steps or []),
    ]
    chat = Chat([txn("T1", "10", "Cafe", JUNE_10)], ScriptedAgent(steps))
    chat.state.language = "pt"
    await chat.say("Tem uma cobrança estranha na minha conta")
    await chat.say("Não lembro")
    assert chat.state.phase == Phase.OFFER_ESCALATION
    return chat


async def test_the_closing_is_in_portuguese_when_the_conversation_is():
    chat = await _offered_a_person_in_portuguese([say("Claro, em qual estabelecimento foi a cobrança?")])
    offer = chat.state.pending_question or ""
    assert "sinto muito" in offer and "atendente" in offer and offer.endswith("Responda sim ou não.")
    unclear = await chat.say("sim, por favor")
    assert "Nesta parte do processo você só pode responder" in unclear
    other = await _offered_a_person_in_portuguese()
    done = await other.say("sim")
    assert other.state.terminal is not None and other.state.terminal.reference is not None
    assert other.state.terminal.reference in done
    assert "já tem o resumo" in done and "não precisa repetir nada" in done
    again = await other.say("obrigado")
    assert f"referência {other.state.terminal.reference}" in again and "inicie uma nova conversa" in again


# ---------------------------------------------------------------- only a plain no rejects the transaction


async def test_a_reply_the_classifier_reads_as_a_no_but_is_not_a_plain_no_does_not_reject_the_transaction():
    """Live run on the merged main: "lo reconozco" to the card was classified as a no, and the agent answered "no
    volveré a sugerir ese cargo", the opposite of what the customer said."""
    agent = ScriptedAgent(
        [
            sql(FIND_123),
            call("propose_transaction", transaction_id="T1", customer_says_not_me=False),
            say("Gracias. ¿Quieres que abra un reclamo por ese cargo de Cafe Sur?"),
        ],
        classified={"lo reconozco": "no"},
    )
    chat = Chat([txn("T1", "123.10", "Cafe Sur", JUNE_10)], agent)
    await chat.say("No reconozco un cargo de 123 dólares")
    reply = await chat.say("lo reconozco")
    assert chat.state.rejected_txn_ids == []  # nothing was rejected
    assert reply.startswith("Gracias") and chat.state.unclear_detours == 1
    note = agent.requests[-1][-1]["content"]
    assert (
        note.startswith("SISTEMA") and "con otra cosa en vez de sí o no" in note and "no la propongas otra vez" in note
    )


async def test_a_plain_no_still_rejects_the_transaction():
    agent = ScriptedAgent(
        [sql(FIND_123), call("propose_transaction", transaction_id="T1", customer_says_not_me=False), say(ASK)]
    )
    chat = Chat([txn("T1", "123.10", "Cafe Sur", JUNE_10)], agent)
    await chat.say("No reconozco un cargo de 123 dólares")
    await chat.say("no")
    assert chat.state.rejected_txn_ids == ["T1"] and chat.state.unclear_detours == 0


async def test_three_such_replies_offer_a_person_as_the_workflow_does():
    agent = ScriptedAgent(
        [sql(FIND_123)] + [call("propose_transaction", transaction_id="T1", customer_says_not_me=False)] * 3,
        classified={"lo reconozco": "no", "ese mismo": "no", "claro que sí": "no"},
    )
    chat = Chat([txn("T1", "123.10", "Cafe Sur", JUNE_10)], agent)
    await chat.say("No reconozco un cargo de 123 dólares")
    await chat.say("lo reconozco")
    await chat.say("ese mismo")
    offer = await chat.say("claro que sí")
    assert chat.state.phase == Phase.OFFER_ESCALATION and "asesor" in offer and chat.state.rejected_txn_ids == []
    done = await chat.say("sí")
    assert _handoff(chat).reason == "unclear_confirmation" and _handoff(chat).handoff_id in done


@pytest.mark.parametrize("replies", [("tal vez", "sí, ese mismo", "no sé"), ("sí, ese mismo", "tal vez", "sí, claro")])
async def test_all_ambiguous_card_replies_share_one_limit(replies):
    agent = ScriptedAgent(
        [sql(FIND_123)] + [call("propose_transaction", transaction_id="T1", customer_says_not_me=False)] * 3
    )
    chat = Chat([txn("T1", "123.10", "Cafe Sur", JUNE_10)], agent)
    await chat.say("Quiero disputar el monto de 123 dólares")
    reply = ""
    for text in replies:
        reply = await chat.say(text)
    assert chat.state.phase == Phase.OFFER_ESCALATION and "asesor" in reply
    assert not chat.audit("open_dispute") and not _handoff_ids(chat)
    await chat.say("sí")
    assert _handoff(chat).reason == "unclear_confirmation"
