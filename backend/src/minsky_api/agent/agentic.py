"""Agentic dispute turn: a tool-using agent finds the transaction, code does everything else.

docs/agentic_dispute_agent.md. The model is free in one phase only (SEARCH): it talks with the customer and
queries the customer's own transactions. It ends the phase by proposing one id it has seen. From there every step
is code: the card of verified facts and the yes/no that authorizes opening (agent.consent), the recognition
question, the policy (evaluate_dispute / open_dispute decide on bank facts), the denial message with the offer of
a person, and the escalation with its summary. The agent has no tool to open, block or escalate (AGENTS rules 1, 2).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from minsky_api.agent.agentic_wording import (
    already_done,
    ask_again,
    declined_escalation,
    denial,
    need_more_detail,
    offer_block,
    offer_escalation_without_match,
    recognize_question,
    transaction_card,
)
from minsky_api.agent.language import LanguageDetector, default_language_detector
from minsky_api.agent.orchestrator import _ask, _classify_first_message, _consent, _get_owned_txn, _lang
from minsky_api.agent.prompts import render
from minsky_api.agent.sandbox import SandboxError
from minsky_api.agent.speak import action_claims, ungrounded_number
from minsky_api.agent.state import ConversationState, Phase
from minsky_api.agent.summary import build_handoff_facts
from minsky_api.agent.wording import fallback_sentence
from minsky_api.config import get_settings
from minsky_api.identity.session import SessionState
from minsky_api.llm.client import LLM, ToolCall, ToolSpec
from minsky_api.observability import start_span
from minsky_api.policy.disputes import Route
from minsky_api.tools.bank import block_card, create_handoff, evaluate_dispute, get_dispute, open_dispute
from minsky_api.tools.context import ToolContext
from minsky_api.tools.errors import ToolDenied
from minsky_api.tools.history import TransactionHistory, load_history, query_transactions, result_text
from minsky_api.tools.schemas import (
    BlockCardArgs,
    CreateHandoffArgs,
    EvaluateDisputeArgs,
    GetDisputeArgs,
    OpenDisputeArgs,
    QueryTransactionsArgs,
)

SEARCH_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="query_transactions",
        description="Read-only SQLite SELECT over the customer's own transactions (table transactions).",
        parameters={
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "One SELECT statement."}},
            "required": ["sql"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="propose_transaction",
        description="Propose the transaction the customer wants to dispute; it must come from a query result.",
        parameters={
            "type": "object",
            "properties": {
                "transaction_id": {"type": "string"},
                "customer_says_not_me": {
                    "type": "boolean",
                    "description": "True only if the customer said they did not make it or do not recognise it.",
                },
            },
            "required": ["transaction_id", "customer_says_not_me"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="give_up",
        description="End the search: the request is not a transaction dispute, or the transaction was not found.",
        parameters={
            "type": "object",
            "properties": {"reason": {"type": "string", "enum": ["not_found", "out_of_scope"]}},
            "required": ["reason"],
            "additionalProperties": False,
        },
    ),
)

_MAX_TEXT_RETRIES = 1
_RULE_ID = re.compile(r"\bD0\d\b")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


@dataclass
class _Turn:
    """Per-message scratch: the sandbox is built on the first query and closed when the message is answered."""

    history: TransactionHistory | None = None


def _date_parts(texts: list[str]) -> list[str]:
    """Day, month and year numbers of the ISO dates in the tool results: "10 de junio" is grounded in 2026-06-10."""
    parts: list[str] = []
    for text in texts:
        for year, month, day in _ISO_DATE.findall(text):
            parts += [year, month, str(int(month)), day, str(int(day))]
    return parts


def _grounding(state: ConversationState) -> dict[str, object]:
    """What the agent may state: query results, the customer's own words and the date of the data."""
    results = [str(item["output"]) for item in state.agent_items if item.get("type") == "function_call_output"]
    today = get_settings().today.isoformat()
    return {
        "tool_results": results,
        "customer": [text for role, text in state.messages if role == "user"],
        "today": today,
        "date_parts": _date_parts([*results, today]),
    }


def _text_problem(text: str, state: ConversationState) -> str | None:
    """Why the agent's reply must not reach the customer, or None. Same checks as the workflow's replies."""
    if not text.strip():
        return "empty reply"
    if action_claims(text):
        return "it claims an action was done; you cannot know that"
    if _RULE_ID.search(text):
        return "it names an internal rule"
    detected = default_language_detector().recognize(text)
    if detected is not None and detected != _lang(state):
        return f"it is not written in the customer's language ({_lang(state)})"
    invented = ungrounded_number(text, _grounding(state))
    if invented is not None:
        return f"the number or date {invented} is in no query result and not from the customer"
    return None


async def _run_tool(ctx: ToolContext, state: ConversationState, call: ToolCall, turn: _Turn) -> tuple[str, str | None]:
    """Execute one tool call. Returns (the tool result the model reads, the customer reply if the search ended)."""
    try:
        args: Any = json.loads(call.arguments)
    except json.JSONDecodeError:
        return "error: the arguments are not valid JSON", None
    if not isinstance(args, dict):
        return "error: the arguments must be a JSON object", None

    if call.name == "query_transactions":
        sql = args.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return "error: sql must be a non-empty string", None
        try:
            query_args = QueryTransactionsArgs(sql=sql)
        except ValidationError:
            return "error: the query is too long", None
        if turn.history is None:
            turn.history = await load_history(ctx)
        try:
            result = await query_transactions(ctx, turn.history, query_args)
        except SandboxError as exc:
            return f"error: {exc}", None
        state.queries_run += 1
        state.seen_txn_ids.extend(t for t in result.transaction_ids if t not in state.seen_txn_ids)
        return result_text(result), None

    if call.name == "propose_transaction":
        txn_id = args.get("transaction_id")
        if not isinstance(txn_id, str) or not txn_id:
            return "error: transaction_id must be a string", None
        if txn_id in state.rejected_txn_ids:
            return "error: the customer already said that is not the transaction", None
        if txn_id not in state.seen_txn_ids:
            return "error: that id is in no query result of this conversation; query for it first", None
        txn = await _get_owned_txn(ctx, txn_id)
        if txn is None:
            return "error: that transaction is not available", None
        state.selected_txn_id = txn.transaction_id
        state.selected_product_id = txn.product_id
        state.selected_type = txn.transaction_type
        state.customer_says_not_me = state.customer_says_not_me or args.get("customer_says_not_me") is True
        state.acts.append("confirm_txn")
        card = _ask(state, Phase.CONFIRM_DISPUTE, transaction_card(txn, _lang(state)))
        return "ok: the system is asking the customer to confirm this transaction; wait for the answer", card

    if call.name == "give_up":
        reason = args.get("reason") if args.get("reason") in ("not_found", "out_of_scope") else "not_found"
        return "ok: the system is offering the customer a person", _offer_escalation_without_match(state, str(reason))

    return f"error: unknown tool {call.name}", None


def _offer_escalation_without_match(state: ConversationState, reason: str) -> str:
    state.escalation_reason = "out_of_scope" if reason == "out_of_scope" else "search_exhausted"
    state.acts.append("offer_handoff")
    return _ask(state, Phase.OFFER_ESCALATION, offer_escalation_without_match(_lang(state), reason))


async def _phase_search(
    ctx: ToolContext, state: ConversationState, text: str, llm: LLM, *, note: str | None = None
) -> str:
    state.phase = Phase.SEARCH
    state.pending_question = None
    state.agent_items.append({"role": "user", "content": text})
    if note:
        state.agent_items.append({"role": "user", "content": f"SISTEMA: {note}"})
    instructions = render("agent.search.j2", today=get_settings().today.isoformat())
    budget = get_settings().agent_max_tool_calls
    turn = _Turn()
    tool_calls = retries = 0
    try:
        while tool_calls <= budget:
            step = await llm.step(instructions, state.agent_items, tools=SEARCH_TOOLS)
            if step.tool_calls:
                call = step.tool_calls[0]  # one at a time; an extra call in the same step is not answered
                tool_calls += 1
                if step.text.strip():
                    state.agent_items.append({"role": "assistant", "content": step.text})
                state.agent_items.append(
                    {"type": "function_call", "call_id": call.call_id, "name": call.name, "arguments": call.arguments}
                )
                output, reply = await _run_tool(ctx, state, call, turn)
                state.agent_items.append({"type": "function_call_output", "call_id": call.call_id, "output": output})
                if reply is not None:
                    state.agent_items.append({"role": "assistant", "content": reply})
                    state.search_failures = 0
                    return reply
                continue
            problem = _text_problem(step.text, state)
            if problem is None:
                state.agent_items.append({"role": "assistant", "content": step.text})
                state.search_failures = 0
                state.acts.append("clarify")
                return step.text
            if retries >= _MAX_TEXT_RETRIES:
                break
            retries += 1
            state.agent_items.append(
                {"role": "user", "content": f"SISTEMA: tu respuesta no se envió ({problem}). Escríbela de nuevo."}
            )
    finally:
        if turn.history is not None:
            turn.history.close()
    return await _search_stuck(state)


async def _search_stuck(state: ConversationState) -> str:
    """The agent used its budget or failed the checks twice: ask for a detail, and after a few times offer a person."""
    state.search_failures += 1
    if state.search_failures > get_settings().max_clarify_attempts:
        return _offer_escalation_without_match(state, "not_found")
    reply = need_more_detail(_lang(state))
    state.agent_items.append({"role": "assistant", "content": reply})
    state.acts.append("clarify")
    return reply


async def _escalate(
    ctx: ToolContext,
    state: ConversationState,
    llm: LLM,
    *,
    reason: str,
    rule_id: str | None = None,
    actions: tuple[str, ...] = (),
    card_blocked: bool | None = None,
) -> str:
    facts = await build_handoff_facts(ctx, state, llm, rule_id=rule_id)
    if card_blocked is not None:
        facts["card_blocked"] = card_blocked
    result = await create_handoff(
        ctx,
        CreateHandoffArgs(
            idempotency_key=f"{state.conversation_id}:{state.turn_count}",
            reason=reason,
            rule_id=rule_id,
            facts=facts,
            actions=actions,
        ),
    )
    state.phase = Phase.DONE
    state.pending_question = None
    state.acts.append("handoff")
    return fallback_sentence(
        _lang(state), {"handoff_id": result.handoff.handoff_id, "card_blocked": card_blocked is True}
    )


def _repeat_question(state: ConversationState) -> str:
    state.acts.append("ask_again")
    return ask_again(_lang(state))


async def _decide(ctx: ToolContext, state: ConversationState, llm: LLM) -> str:
    """The customer wants to dispute the transaction: the policy decides, on the bank's facts."""
    assert state.selected_txn_id is not None
    decision = await evaluate_dispute(
        ctx,
        EvaluateDisputeArgs(transaction_id=state.selected_txn_id, customer_says_not_me=state.customer_says_not_me),
    )
    state.rule_id = decision.rule_id
    state.route = decision.route
    language = _lang(state)
    if decision.route == Route.OPEN_DISPUTE.value:
        # The yes to "should I open a claim for this?" is the consent; the tool still checks the policy itself.
        opened = await open_dispute(
            ctx,
            OpenDisputeArgs(
                transaction_id=state.selected_txn_id,
                reason=state.dispute_reason,
                confirmed=True,
                customer_says_not_me=state.customer_says_not_me,
            ),
        )
        verified = await get_dispute(ctx, GetDisputeArgs(dispute_id=opened.dispute.dispute_id))
        state.phase = Phase.DONE
        state.pending_question = None
        state.acts.append("inform")
        return fallback_sentence(language, {"dispute_id": verified.dispute.dispute_id})
    if decision.route == Route.ESCALATE_FRAUD.value:
        if not decision.offer_card_block:
            # Nothing to block (e.g. a transfer): straight to the fraud team, as the workflow does.
            return await _escalate(ctx, state, llm, reason="possible_fraud", rule_id=decision.rule_id)
        state.acts.append("offer_block")
        return _ask(state, Phase.CARD_OFFER, offer_block(language, decision.rule_id))
    # Every other rule: the dispute cannot go ahead automatically. Say why, offer a person.
    state.existing_dispute_id = decision.existing_dispute_id
    state.escalation_reason = decision.rule_id
    state.denial_text = denial(language, decision.rule_id, decision.existing_dispute_id)
    state.acts.append("refuse" if decision.route == Route.REFUSE.value else "inform")
    return _ask(state, Phase.OFFER_ESCALATION, state.denial_text)


async def _phase_confirm_dispute(ctx: ToolContext, state: ConversationState, text: str, llm: LLM) -> str:
    decision = await _consent(ctx, state, text, llm)
    if decision == "yes":
        if state.customer_says_not_me:
            return await _decide(ctx, state, llm)  # already said they did not make it: no need to ask again
        return _ask(state, Phase.RECOGNIZE, recognize_question(_lang(state)))
    if decision == "no":
        rejected = state.selected_txn_id
        if rejected:
            state.rejected_txn_ids.append(rejected)
        state.selected_txn_id = state.selected_product_id = state.selected_type = None
        note = f"el cliente dijo que la transacción {rejected} no es la que busca; no la propongas otra vez."
        return await _phase_search(ctx, state, text, llm, note=note)
    return _repeat_question(state)


async def _phase_recognize(ctx: ToolContext, state: ConversationState, text: str, llm: LLM) -> str:
    decision = await _consent(ctx, state, text, llm)
    if decision == "yes":  # recognises it: a dispute about the amount or the service, not about who made it
        return await _decide(ctx, state, llm)
    if decision == "no":
        state.customer_says_not_me = True
        state.dispute_reason = "unrecognized_charge"
        return await _decide(ctx, state, llm)
    return _repeat_question(state)


async def _phase_card_offer(ctx: ToolContext, state: ConversationState, text: str, llm: LLM) -> str:
    decision = await _consent(ctx, state, text, llm)
    if decision == "yes":
        actions: list[str] = []
        blocked = False
        if state.selected_product_id:
            try:
                result = await block_card(ctx, BlockCardArgs(product_id=state.selected_product_id, confirmed=True))
                actions.append(f"card_blocked:{result.block.product_id}")
                blocked = True
            except ToolDenied:
                blocked = False  # never claim a block that was not read back
        return await _escalate(
            ctx,
            state,
            llm,
            reason="possible_fraud",
            rule_id=state.rule_id,
            actions=tuple(actions),
            card_blocked=blocked,
        )
    if decision == "no":
        return await _escalate(ctx, state, llm, reason="possible_fraud_no_block", rule_id=state.rule_id)
    return _repeat_question(state)


async def _phase_offer_escalation(ctx: ToolContext, state: ConversationState, text: str, llm: LLM) -> str:
    decision = await _consent(ctx, state, text, llm)
    if decision == "yes":
        if state.denial_text:
            return await _escalate(ctx, state, llm, reason="customer_requested_after_denial", rule_id=state.rule_id)
        return await _escalate(ctx, state, llm, reason=state.escalation_reason or "search_exhausted")
    if decision == "no":
        state.phase = Phase.DONE
        state.pending_question = None
        state.acts.append("abort")
        return declined_escalation(_lang(state))
    return _repeat_question(state)


async def run_agentic_turn(
    state: ConversationState,
    text: str,
    ctx: ToolContext,
    llm: LLM,
    detector: LanguageDetector | None = None,
) -> tuple[ConversationState, str]:
    """One customer message → updated state + reply. Same contract as orchestrator.run_turn."""
    if ctx.session.state != SessionState.VALID:
        raise PermissionError("session must be valid")
    if not ctx.session.customer_id or ctx.session.customer_id != state.customer_id:
        raise PermissionError("conversation_customer_mismatch")
    stripped = text.strip()
    if not stripped:
        raise ValueError("text must not be blank")

    with start_span("chat.turn", conversation_id=str(state.conversation_id), phase=state.phase.value, mode="agentic"):
        state.turn_count += 1
        state.messages.append(("user", stripped))
        if state.language is None:
            state.language = (detector or default_language_detector()).detect(stripped)

        if state.phase == Phase.DONE:
            reply = already_done(_lang(state))
        elif state.turn_count > get_settings().max_turns:
            reply = await _escalate(ctx, state, llm, reason="max_turns")
        elif state.phase in (Phase.UNDERSTAND, Phase.SEARCH):
            if state.phase == Phase.UNDERSTAND:
                _classify_first_message(state, stripped)
            reply = await _phase_search(ctx, state, stripped, llm)
        elif state.phase == Phase.CONFIRM_DISPUTE:
            reply = await _phase_confirm_dispute(ctx, state, stripped, llm)
        elif state.phase == Phase.RECOGNIZE:
            reply = await _phase_recognize(ctx, state, stripped, llm)
        elif state.phase == Phase.CARD_OFFER:
            reply = await _phase_card_offer(ctx, state, stripped, llm)
        elif state.phase == Phase.OFFER_ESCALATION:
            reply = await _phase_offer_escalation(ctx, state, stripped, llm)
        else:
            raise RuntimeError(f"unknown phase for agentic mode: {state.phase}")

        state.messages.append(("agent", reply))
        return state, reply
