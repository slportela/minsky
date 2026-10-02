"""Dispute orchestrator: code-owned state machine over tools + policy (docs/solution.md steps 2-8)."""

from __future__ import annotations

import re

from minsky_api.agent import replies
from minsky_api.agent.extract import DisputeDetails, extract_dispute_details
from minsky_api.agent.state import ConversationState, Phase
from minsky_api.config import get_settings
from minsky_api.identity.session import SessionState
from minsky_api.llm.client import LLM
from minsky_api.policy.disputes import Route
from minsky_api.tools.bank import (
    block_card,
    create_handoff,
    evaluate_dispute,
    get_dispute,
    get_transaction,
    get_transactions,
    open_dispute,
)
from minsky_api.tools.context import ToolContext
from minsky_api.tools.errors import ToolDenied
from minsky_api.tools.schemas import (
    BlockCardArgs,
    CreateHandoffArgs,
    EvaluateDisputeArgs,
    GetDisputeArgs,
    GetTransactionArgs,
    GetTransactionsArgs,
    OpenDisputeArgs,
    TransactionView,
)

# No lone "y": too easy to false-confirm on noisy input.
_YES = re.compile(r"^\s*(sí|si|yes|ok|vale|confirmo|confirm[oa])\s*[.!?]?\s*$", re.IGNORECASE)
_NO = re.compile(r"^\s*(no|n|cancel[oa]?|negativo)\s*[.!?]?\s*$", re.IGNORECASE)
_PICK = re.compile(r"^\s*(\d+)\s*$")


def _is_yes(text: str) -> bool:
    return _YES.match(text) is not None


def _is_no(text: str) -> bool:
    return _NO.match(text) is not None


def _has_search_filters(details: DisputeDetails) -> bool:
    return any(
        (
            details.transaction_id,
            details.merchant,
            details.amount is not None,
            details.date_from is not None,
            details.date_to is not None,
        )
    )


def _merge_details(state: ConversationState, incoming: DisputeDetails) -> DisputeDetails:
    # A new id identifies a different transaction; ranges replace together, other slots individually.
    previous = DisputeDetails() if incoming.reset_search or incoming.transaction_id else state.search_details
    updates = incoming.model_dump(exclude_none=True)
    for flag in ("reset_search", "out_of_scope", "customer_says_not_me"):
        updates.pop(flag, None)
    if incoming.date_from is not None or incoming.date_to is not None:
        updates.update(date_from=incoming.date_from, date_to=incoming.date_to)
    state.search_details = previous.model_copy(update=updates)
    return state.search_details


async def _handoff(
    ctx: ToolContext,
    state: ConversationState,
    *,
    reason: str,
    rule_id: str | None = None,
    actions: tuple[str, ...] = (),
    reply: str | None = None,
) -> str:
    result = await create_handoff(
        ctx,
        CreateHandoffArgs(
            idempotency_key=f"{state.conversation_id}:{state.turn_count}",
            reason=reason,
            rule_id=rule_id,
            facts={
                "transaction_id": state.selected_txn_id,
                "route": state.route,
            },
            actions=actions,
        ),
    )
    state.phase = Phase.DONE
    if reply is not None:
        return reply.format(handoff_id=result.handoff.handoff_id)
    return replies.handoff_done(handoff_id=result.handoff.handoff_id, rule_id=rule_id)


async def _get_owned_txn(ctx: ToolContext, transaction_id: str) -> TransactionView | None:
    try:
        got = await get_transaction(ctx, GetTransactionArgs(transaction_id=transaction_id))
    except ToolDenied:
        return None
    return got.transaction


async def _search(ctx: ToolContext, details: DisputeDetails) -> list[TransactionView]:
    if details.transaction_id:
        txn = await _get_owned_txn(ctx, details.transaction_id)
        return [txn] if txn is not None else []
    # Never dump the customer's latest N rows: require at least one narrowing signal.
    if not _has_search_filters(details):
        return []
    found = await get_transactions(
        ctx,
        GetTransactionsArgs(
            limit=10,
            merchant=details.merchant,
            min_amount=details.amount,
            max_amount=details.amount,
            date_from=details.date_from,
            date_to=details.date_to,
        ),
    )
    return list(found.transactions)


async def _after_candidates(
    ctx: ToolContext,
    state: ConversationState,
    txns: list[TransactionView],
) -> str:
    if len(txns) == 0:
        state.clarify_count += 1
        if state.clarify_count > get_settings().max_clarify_attempts:
            return await _handoff(
                ctx,
                state,
                reason="clarify_exhausted",
                reply=replies.clarify_limit() + " Referencia de traspaso: {handoff_id}.",
            )
        state.phase = Phase.CLARIFY
        state.candidate_txn_ids = []
        state.search_details = DisputeDetails()
        return replies.ask_clarify_none()
    if len(txns) > 1:
        state.clarify_count += 1
        if state.clarify_count > get_settings().max_clarify_attempts:
            return await _handoff(
                ctx,
                state,
                reason="clarify_exhausted",
                reply=replies.clarify_limit() + " Referencia de traspaso: {handoff_id}.",
            )
        state.phase = Phase.CLARIFY
        state.candidate_txn_ids = [t.transaction_id for t in txns]
        return replies.ask_clarify_many(txns)
    txn = txns[0]
    state.selected_txn_id = txn.transaction_id
    state.selected_product_id = txn.product_id
    state.candidate_txn_ids = [txn.transaction_id]
    state.phase = Phase.CONFIRM_TXN
    return replies.ask_confirm_txn(txn)


async def _apply_policy(ctx: ToolContext, state: ConversationState) -> str:
    assert state.selected_txn_id is not None
    decision = await evaluate_dispute(
        ctx,
        EvaluateDisputeArgs(
            transaction_id=state.selected_txn_id,
            customer_says_not_me=state.customer_says_not_me,
        ),
    )
    state.rule_id = decision.rule_id
    state.route = decision.route
    route = decision.route
    if route == Route.OPEN_DISPUTE.value:
        state.phase = Phase.CONFIRM_ACT
        return replies.ask_confirm_open(rule_id=decision.rule_id, txn_id=state.selected_txn_id)
    if route == Route.ESCALATE_FRAUD.value:
        state.phase = Phase.CARD_OFFER
        return replies.ask_card_block(rule_id=decision.rule_id)
    if route == Route.ESCALATE_AGENT.value:
        return await _handoff(ctx, state, reason="policy_escalate_agent", rule_id=decision.rule_id)
    if route == Route.REFUSE.value:
        text = replies.policy_refuse(rule_id=decision.rule_id)
        handoff_text = await _handoff(ctx, state, reason="policy_refuse", rule_id=decision.rule_id)
        return f"{text} {handoff_text}"
    # inform / abstain (include existing dispute ref when D04)
    state.phase = Phase.DONE
    return replies.policy_inform(rule_id=decision.rule_id, existing_dispute_id=decision.existing_dispute_id)


async def _phase_understand(ctx: ToolContext, state: ConversationState, text: str, llm: LLM) -> str:
    details = await extract_dispute_details(llm, text)
    state.customer_says_not_me = details.customer_says_not_me
    if details.out_of_scope:
        result = await create_handoff(
            ctx,
            CreateHandoffArgs(
                idempotency_key=f"{state.conversation_id}:{state.turn_count}",
                reason="out_of_scope",
                rule_id=None,
                facts={},
                actions=(),
            ),
        )
        state.phase = Phase.DONE
        return replies.out_of_scope_handoff(handoff_id=result.handoff.handoff_id)
    txns = await _search(ctx, _merge_details(state, details))
    return await _after_candidates(ctx, state, txns)


async def _phase_clarify(ctx: ToolContext, state: ConversationState, text: str, llm: LLM) -> str:
    pick = _PICK.match(text.strip())
    if pick and state.candidate_txn_ids:
        index = int(pick.group(1)) - 1
        if 0 <= index < len(state.candidate_txn_ids):
            txn = await _get_owned_txn(ctx, state.candidate_txn_ids[index])
            if txn is None:
                return await _after_candidates(ctx, state, [])
            state.selected_txn_id = txn.transaction_id
            state.selected_product_id = txn.product_id
            state.phase = Phase.CONFIRM_TXN
            return replies.ask_confirm_txn(txn)
    details = await extract_dispute_details(llm, text)
    state.customer_says_not_me = state.customer_says_not_me or details.customer_says_not_me
    if details.out_of_scope:
        return await _handoff(ctx, state, reason="out_of_scope")
    if details.transaction_id:
        txn = await _get_owned_txn(ctx, details.transaction_id)
        return await _after_candidates(ctx, state, [txn] if txn is not None else [])
    txns = await _search(ctx, _merge_details(state, details))
    return await _after_candidates(ctx, state, txns)


async def _phase_confirm_txn(ctx: ToolContext, state: ConversationState, text: str) -> str:
    if _is_yes(text):
        return await _apply_policy(ctx, state)
    if _is_no(text):
        state.phase = Phase.CLARIFY
        state.selected_txn_id = None
        state.selected_product_id = None
        state.candidate_txn_ids = []
        state.search_details = DisputeDetails()
        return replies.ask_clarify_none()
    return replies.need_yes_or_no()


async def _phase_confirm_act(ctx: ToolContext, state: ConversationState, text: str) -> str:
    if _is_no(text):
        state.phase = Phase.DONE
        return replies.aborted()
    if not _is_yes(text):
        return replies.need_yes_or_no()
    assert state.selected_txn_id is not None
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
    return replies.opened_dispute(dispute_id=verified.dispute.dispute_id, rule_id=state.rule_id or "D09-eligible")


async def _phase_card_offer(ctx: ToolContext, state: ConversationState, text: str) -> str:
    if _is_yes(text):
        actions: list[str] = []
        blocked_ok = False
        if state.selected_product_id:
            try:
                blocked = await block_card(
                    ctx,
                    BlockCardArgs(product_id=state.selected_product_id, confirmed=True),
                )
                actions.append(f"card_blocked:{blocked.block.product_id}")
                blocked_ok = True
            except ToolDenied:
                blocked_ok = False
        result = await create_handoff(
            ctx,
            CreateHandoffArgs(
                idempotency_key=f"{state.conversation_id}:{state.turn_count}",
                reason="possible_fraud",
                rule_id=state.rule_id,
                facts={"transaction_id": state.selected_txn_id, "card_blocked": blocked_ok},
                actions=tuple(actions),
            ),
        )
        state.phase = Phase.DONE
        if blocked_ok:
            return replies.card_blocked_handoff(handoff_id=result.handoff.handoff_id, rule_id=state.rule_id)
        # Never claim a block we did not verify.
        return replies.handoff_done(handoff_id=result.handoff.handoff_id, rule_id=state.rule_id)
    if _is_no(text):
        return await _handoff(ctx, state, reason="possible_fraud_no_block", rule_id=state.rule_id)
    return replies.need_yes_or_no()


async def run_turn(
    state: ConversationState,
    text: str,
    ctx: ToolContext,
    llm: LLM,
) -> tuple[ConversationState, str]:
    """One customer message → updated state + agent reply. Never reports a write before tool read-back."""
    if ctx.session.state != SessionState.VALID:
        raise PermissionError("session must be valid")
    if not ctx.session.customer_id or ctx.session.customer_id != state.customer_id:
        raise PermissionError("conversation_customer_mismatch")
    stripped = text.strip()
    if not stripped:
        raise ValueError("text must not be blank")

    state.turn_count += 1
    state.messages.append(("user", stripped))

    if state.phase == Phase.DONE:
        reply = replies.already_done()
        state.messages.append(("agent", reply))
        return state, reply

    if state.turn_count > get_settings().max_turns:
        reply = await _handoff(
            ctx,
            state,
            reason="max_turns",
            reply=replies.turn_limit() + " Referencia de traspaso: {handoff_id}.",
        )
        state.messages.append(("agent", reply))
        return state, reply

    if state.phase == Phase.UNDERSTAND:
        reply = await _phase_understand(ctx, state, stripped, llm)
    elif state.phase == Phase.CLARIFY:
        reply = await _phase_clarify(ctx, state, stripped, llm)
    elif state.phase == Phase.CONFIRM_TXN:
        reply = await _phase_confirm_txn(ctx, state, stripped)
    elif state.phase == Phase.CONFIRM_ACT:
        reply = await _phase_confirm_act(ctx, state, stripped)
    elif state.phase == Phase.CARD_OFFER:
        reply = await _phase_card_offer(ctx, state, stripped)
    else:
        raise RuntimeError(f"unknown phase: {state.phase}")

    state.messages.append(("agent", reply))
    return state, reply
