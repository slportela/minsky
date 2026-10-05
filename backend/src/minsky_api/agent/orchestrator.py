"""Dispute orchestrator: code-owned state machine over tools + policy (docs/solution.md steps 2-8)."""

from __future__ import annotations

import re

from minsky_api.agent.consent import explicit_no, explicit_yes
from minsky_api.agent.degraded import is_model_failure
from minsky_api.agent.extract import DisputeDetails, extract_dispute_details
from minsky_api.agent.language import LanguageDetector, default_language_detector
from minsky_api.agent.speak import Speech, compose_speech
from minsky_api.agent.state import ConversationState, Phase
from minsky_api.agent.wording import (
    clarify_fallback,
    confirm_question,
    done_fallback,
    fallback_sentence,
    human_amount,
    human_date,
    inform_fallback,
    policy_reason,
    safe_sentence,
    transaction_noun,
    with_candidates,
    with_yes_no_hint,
)
from minsky_api.config import get_settings
from minsky_api.identity.session import SessionState
from minsky_api.llm.client import LLM, LLMNotConfiguredError, ModelMismatchError
from minsky_api.observability import start_span
from minsky_api.policy.disputes import Route
from minsky_api.router.classifier import classify
from minsky_api.tools.bank import (
    block_card,
    create_handoff,
    evaluate_dispute,
    get_dispute,
    get_transaction,
    get_transactions,
    open_dispute,
)
from minsky_api.tools.confirm import classify_reply
from minsky_api.tools.context import ToolContext
from minsky_api.tools.errors import ToolDenied
from minsky_api.tools.schemas import (
    BlockCardArgs,
    ClassifyReplyArgs,
    CreateHandoffArgs,
    EvaluateDisputeArgs,
    GetDisputeArgs,
    GetTransactionArgs,
    GetTransactionsArgs,
    OpenDisputeArgs,
    TransactionView,
)

# A bare number picks a candidate. Consent is explicit_yes / explicit_no (agent.consent); the model's
# classification of a reply is evidence on the audit trail, not consent.
_PICK = re.compile(r"^\s*(\d+)\s*$")


def _lang(state: ConversationState) -> str:
    if not state.language:
        raise RuntimeError("reply language is unset")
    return state.language


def _public_facts(state: ConversationState, **extra: object) -> dict[str, object]:
    """Facts the model may phrase. The rule id becomes its plain-language reason; ids stay internal."""
    rule_id = extra.pop("rule_id", state.rule_id)
    raw: dict[str, object] = {"reason": policy_reason(rule_id if isinstance(rule_id, str) else None, _lang(state))}
    raw.update(extra)
    return {key: value for key, value in raw.items() if value is not None}


def _txn_facts(txn: TransactionView, language: str) -> dict[str, object]:
    return {
        "kind": transaction_noun(txn.transaction_type, language),
        "merchant": txn.merchant_name,
        "amount": human_amount(txn.amount_usd),
        "when": human_date(txn.transaction_date.date(), language) if txn.transaction_date else None,
    }


_SPEAK_ATTEMPTS = 2  # bounded: one retry when a reply fails the checks, then the error surfaces


async def _speak(state: ConversationState, llm: LLM, allowed: tuple[str, ...], **facts: object) -> Speech:
    public = _public_facts(state, **facts)
    for attempt in range(_SPEAK_ATTEMPTS):
        try:
            return await compose_speech(llm, language=_lang(state), allowed=allowed, facts=public)
        except RuntimeError:
            if attempt == _SPEAK_ATTEMPTS - 1:
                raise
    raise AssertionError("unreachable")


async def _speak_safe(state: ConversationState, llm: LLM, allowed: tuple[str, ...], **facts: object) -> Speech:
    """Like _speak, but a reply refused twice becomes a code-written sentence instead of an error (HTTP 503).

    The sentence is for the first allowed act and says only what code verified (wording.safe_sentence). A
    missing key or a model other than the pinned one still fails loudly: those are not phrasing failures.
    """
    try:
        return await _speak(state, llm, allowed, **facts)
    except (LLMNotConfiguredError, ModelMismatchError):
        raise
    except RuntimeError:
        act = allowed[0]
        # model_validate, not the constructor: an act that is not a valid Act fails loudly instead of being cast.
        return Speech.model_validate(
            {"act": act, "text": safe_sentence(act, _lang(state), _public_facts(state, **facts))}
        )


async def _speak_verified(state: ConversationState, llm: LLM, allowed: tuple[str, ...], **facts: object) -> str:
    """Speak after a verified write. A model failure still reports the read-back ids."""
    language = _lang(state)
    public = _public_facts(state, **facts)
    try:
        speech = await _speak(state, llm, allowed, **facts)
    except Exception:
        if public.get("card_blocked") is True:
            state.claims_card_blocked = True
        state.acts.append(allowed[0])
        return fallback_sentence(language, public)
    return _accept(state, speech)


def _accept(state: ConversationState, speech: Speech) -> str:
    state.acts.append(speech.act)
    if speech.claims_card_blocked:
        state.claims_card_blocked = True
    return speech.text


async def _clarify(state: ConversationState, llm: LLM, candidates: str | None = None) -> str:
    """Ask which transaction. The model writes the question; code owns the option list.

    The list is appended unless the model already carried it exactly, so the customer always sees the real
    options once. If the model cannot phrase a valid question, a code-written one is sent instead of an
    error. A missing key or a model other than the pinned one still fails loudly: those are not phrasing
    failures and a fallback would hide them.
    """
    facts = {"candidates": candidates} if candidates else {}
    try:
        speech = await _speak(state, llm, ("clarify",), **facts)
    except (LLMNotConfiguredError, ModelMismatchError):
        raise
    except RuntimeError:
        state.acts.append("clarify")
        return clarify_fallback(_lang(state), candidates)
    reply = _accept(state, speech)
    return with_candidates(reply, candidates) if candidates else reply


def _ask(state: ConversationState, phase: Phase, text: str) -> str:
    """Remember the question before the next customer message is classified."""
    state.phase = phase
    state.pending_question = text
    state.confirmation = None
    return text


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
    llm: LLM,
    *,
    reason: str,
    rule_id: str | None = None,
    actions: tuple[str, ...] = (),
) -> str:
    result = await create_handoff(
        ctx,
        CreateHandoffArgs(
            idempotency_key=f"{state.conversation_id}:{state.turn_count}",
            reason=reason,
            rule_id=rule_id,
            facts=_handoff_facts(state),
            actions=actions,
        ),
    )
    state.phase = Phase.DONE
    state.pending_question = None
    return await _speak_verified(state, llm, ("handoff",), handoff_id=result.handoff.handoff_id, rule_id=rule_id)


def _handoff_facts(state: ConversationState, **extra: object) -> dict[str, object]:
    """What the agent needs: the selected transaction (verified again by the tool) and what the customer said.

    Customer statements are labeled as claims; the tool adds the verified transaction facts itself.
    """
    details = state.search_details.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    facts: dict[str, object] = {
        "transaction_id": state.selected_txn_id,
        "route": state.route,
        "customer_request": next((text for role, text in state.messages if role == "user"), None),
        "customer_says_not_me": state.customer_says_not_me or None,
        "customer_search_details": details or None,
        "language": state.language,
        "dispute_type_suggested": _DISPUTE_TYPE.get(state.router_label or ""),
        "router_confidence": state.router_confidence if state.router_label else None,
    }
    facts.update(extra)
    return {key: value for key, value in facts.items() if value is not None}


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


def _candidate_list(txns: list[TransactionView], language: str) -> str:
    lines = []
    for index, txn in enumerate(txns, start=1):
        when = human_date(txn.transaction_date.date(), language) if txn.transaction_date else "?"
        lines.append(f"{index}. {txn.merchant_name or '?'}, {human_amount(txn.amount_usd)}, {when}")
    return "\n".join(lines)


async def _after_candidates(
    ctx: ToolContext,
    state: ConversationState,
    txns: list[TransactionView],
    llm: LLM,
) -> str:
    if len(txns) == 0:
        state.clarify_count += 1
        if state.clarify_count > get_settings().max_clarify_attempts:
            return await _handoff(ctx, state, llm, reason="clarify_exhausted")
        reply = await _clarify(state, llm)
        state.phase = Phase.CLARIFY
        state.candidate_txn_ids = []
        state.pending_question = None
        return reply
    if len(txns) > 1:
        state.clarify_count += 1
        if state.clarify_count > get_settings().max_clarify_attempts:
            return await _handoff(ctx, state, llm, reason="clarify_exhausted")
        reply = await _clarify(state, llm, _candidate_list(txns, _lang(state)))
        state.phase = Phase.CLARIFY
        state.candidate_txn_ids = [t.transaction_id for t in txns]
        state.pending_question = None
        return reply
    txn = txns[0]
    state.selected_txn_id = txn.transaction_id
    state.selected_product_id = txn.product_id
    state.selected_type = txn.transaction_type
    state.candidate_txn_ids = [txn.transaction_id]
    speech = await _speak_safe(state, llm, ("confirm_txn",), **_txn_facts(txn, _lang(state)))
    return _ask(state, Phase.CONFIRM_TXN, with_yes_no_hint(_accept(state, speech), _lang(state)))


async def _apply_policy(ctx: ToolContext, state: ConversationState, llm: LLM) -> str:
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
        speech = await _speak_safe(state, llm, ("confirm_open",), rule_id=decision.rule_id)
        question = f"{_accept(state, speech)} {confirm_question('confirm_open', _lang(state), state.selected_type)}"
        return _ask(state, Phase.CONFIRM_ACT, question)
    if route == Route.ESCALATE_FRAUD.value and not decision.offer_card_block:
        # Not a card charge (e.g. a transfer): nothing to block, straight to the fraud team.
        return await _handoff(ctx, state, llm, reason="possible_fraud", rule_id=decision.rule_id)
    if route == Route.ESCALATE_FRAUD.value:
        speech = await _speak_safe(state, llm, ("offer_block",), rule_id=decision.rule_id)
        question = f"{_accept(state, speech)} {confirm_question('offer_block', _lang(state))}"
        return _ask(state, Phase.CARD_OFFER, question)
    if route == Route.ESCALATE_AGENT.value:
        return await _handoff(ctx, state, llm, reason="policy_escalate_agent", rule_id=decision.rule_id)
    if route == Route.REFUSE.value:
        result = await create_handoff(
            ctx,
            CreateHandoffArgs(
                idempotency_key=f"{state.conversation_id}:{state.turn_count}",
                reason="policy_refuse",
                rule_id=decision.rule_id,
                facts=_handoff_facts(state),
                actions=(),
            ),
        )
        state.phase = Phase.DONE
        state.pending_question = None
        return await _speak_verified(
            state,
            llm,
            ("refuse",),
            handoff_id=result.handoff.handoff_id,
            rule_id=decision.rule_id,
        )
    state.phase = Phase.DONE
    state.pending_question = None
    try:
        speech = await _speak(
            state,
            llm,
            ("inform",),
            rule_id=decision.rule_id,
            existing_dispute_id=decision.existing_dispute_id,
            charge_reversed=decision.rule_id.startswith("D02") or None,  # the bank's reversal supports "it came back"
        )
    except RuntimeError:
        # A refused or failed reply must not leave the customer without an answer (no 503): code says it.
        state.acts.append("inform")
        return inform_fallback(_lang(state), decision.rule_id, decision.existing_dispute_id)
    return _accept(state, speech)


# Router label -> the dispute type recorded on the case. Only used when the router is confident; it never
# changes the route (the policy decides) and never replaces the model's reading of "it wasn't me".
_DISPUTE_TYPE = {
    "not_me": "unrecognized_charge",
    "wrong_amount": "wrong_amount",
    "duplicate": "duplicate_charge",
    "not_received": "not_received",
}


def _classify_first_message(state: ConversationState, text: str) -> None:
    prediction = classify(text)
    state.router_label = None if prediction.abstained else prediction.label
    state.router_confidence = round(prediction.confidence, 3)
    if state.router_label in _DISPUTE_TYPE:
        state.dispute_reason = _DISPUTE_TYPE[state.router_label]


async def _phase_understand(ctx: ToolContext, state: ConversationState, text: str, llm: LLM) -> str:
    _classify_first_message(state, text)
    details = await extract_dispute_details(llm, text)
    state.customer_says_not_me = details.customer_says_not_me
    if details.out_of_scope:
        return await _handoff(ctx, state, llm, reason="out_of_scope")
    txns = await _search(ctx, _merge_details(state, details))
    return await _after_candidates(ctx, state, txns, llm)


async def _phase_clarify(ctx: ToolContext, state: ConversationState, text: str, llm: LLM) -> str:
    pick = _PICK.match(text.strip())
    if pick and state.candidate_txn_ids:
        index = int(pick.group(1)) - 1
        if 0 <= index < len(state.candidate_txn_ids):
            txn = await _get_owned_txn(ctx, state.candidate_txn_ids[index])
            if txn is None:
                return await _after_candidates(ctx, state, [], llm)
            state.selected_txn_id = txn.transaction_id
            state.selected_product_id = txn.product_id
            state.selected_type = txn.transaction_type
            speech = await _speak_safe(state, llm, ("confirm_txn",), **_txn_facts(txn, _lang(state)))
            return _ask(state, Phase.CONFIRM_TXN, with_yes_no_hint(_accept(state, speech), _lang(state)))
    details = await extract_dispute_details(llm, text)
    state.customer_says_not_me = state.customer_says_not_me or details.customer_says_not_me
    if details.out_of_scope:
        return await _handoff(ctx, state, llm, reason="out_of_scope")
    if details.transaction_id:
        txn = await _get_owned_txn(ctx, details.transaction_id)
        return await _after_candidates(ctx, state, [txn] if txn is not None else [], llm)
    txns = await _search(ctx, _merge_details(state, details))
    return await _after_candidates(ctx, state, txns, llm)


async def _remember_decision(ctx: ToolContext, state: ConversationState, text: str, llm: LLM) -> str:
    """Classify through the tool before any other tool. The result is evidence, not consent."""
    if not state.pending_question:
        state.confirmation = "unclear"
        return "unclear"
    result = await classify_reply(
        ctx,
        ClassifyReplyArgs(question=state.pending_question, text=text),
        llm,
    )
    state.confirmation = result.decision
    return result.decision


async def _consent(ctx: ToolContext, state: ConversationState, text: str, llm: LLM) -> str:
    """Classify for the audit trail. Only an explicit yes may authorize a write."""
    if not state.pending_question:
        state.confirmation = "unclear"
        return "unclear"
    decision = await _remember_decision(ctx, state, text, llm)
    if explicit_no(text):
        return "no"
    if explicit_yes(text):
        return "yes"
    if decision == "no":
        return "no"
    return "unclear"


async def _ask_again(state: ConversationState, llm: LLM) -> str:
    speech = await _speak_safe(state, llm, ("ask_again",))
    return _accept(state, speech)


async def _phase_confirm_txn(ctx: ToolContext, state: ConversationState, text: str, llm: LLM) -> str:
    decision = await _consent(ctx, state, text, llm)
    if decision == "yes":
        return await _apply_policy(ctx, state, llm)
    if decision == "no":
        speech = await _speak_safe(state, llm, ("clarify", "abort"))
        if speech.act == "abort":
            state.phase = Phase.DONE
            state.pending_question = None
            return _accept(state, speech)
        state.phase = Phase.CLARIFY
        state.pending_question = None
        state.selected_txn_id = None
        state.selected_product_id = None
        state.candidate_txn_ids = []
        return _accept(state, speech)
    return await _ask_again(state, llm)


async def _phase_confirm_act(ctx: ToolContext, state: ConversationState, text: str, llm: LLM) -> str:
    decision = await _consent(ctx, state, text, llm)
    if decision == "no":
        speech = await _speak_safe(state, llm, ("abort",))
        state.phase = Phase.DONE
        state.pending_question = None
        return _accept(state, speech)
    if decision != "yes":
        return await _ask_again(state, llm)
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
    state.pending_question = None
    return await _speak_verified(
        state,
        llm,
        ("inform",),
        dispute_id=verified.dispute.dispute_id,
        rule_id=state.rule_id or "D09-eligible",
    )


async def _phase_card_offer(ctx: ToolContext, state: ConversationState, text: str, llm: LLM) -> str:
    decision = await _consent(ctx, state, text, llm)
    if decision == "yes":
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
                facts=_handoff_facts(state, card_blocked=blocked_ok),
                actions=tuple(actions),
            ),
        )
        state.phase = Phase.DONE
        state.pending_question = None
        return await _speak_verified(
            state,
            llm,
            ("handoff",),
            handoff_id=result.handoff.handoff_id,
            card_blocked=blocked_ok,
            rule_id=state.rule_id,
        )
    if decision == "no":
        state.pending_question = None
        return await _handoff(ctx, state, llm, reason="possible_fraud_no_block", rule_id=state.rule_id)
    return await _ask_again(state, llm)


async def run_turn(
    state: ConversationState,
    text: str,
    ctx: ToolContext,
    llm: LLM,
    detector: LanguageDetector | None = None,
) -> tuple[ConversationState, str]:
    """One customer message → updated state + agent reply. Never reports a write before tool read-back."""
    if ctx.session.state != SessionState.VALID:
        raise PermissionError("session must be valid")
    if not ctx.session.customer_id or ctx.session.customer_id != state.customer_id:
        raise PermissionError("conversation_customer_mismatch")
    stripped = text.strip()
    if not stripped:
        raise ValueError("text must not be blank")

    with start_span(
        "chat.turn",
        conversation_id=str(state.conversation_id),
        phase=state.phase.value,
    ):
        state.turn_count += 1
        state.messages.append(("user", stripped))
        if state.language is None:
            state.language = (detector or default_language_detector()).detect(stripped)

        if state.phase == Phase.DONE:
            try:
                reply = _accept(state, await _speak(state, llm, ("inform",)))
            except Exception as exc:
                # Settled case: template on speech/schema faults and provider outages. Never open a
                # second assistant_unavailable handoff for a conversation that is already closed.
                if not (isinstance(exc, RuntimeError) or is_model_failure(exc)):
                    raise
                state.acts.append("inform")
                reply = done_fallback(_lang(state))
            state.messages.append(("agent", reply))
            return state, reply

        if state.turn_count > get_settings().max_turns:
            reply = await _handoff(ctx, state, llm, reason="max_turns")
            state.messages.append(("agent", reply))
            return state, reply

        if state.phase == Phase.UNDERSTAND:
            reply = await _phase_understand(ctx, state, stripped, llm)
        elif state.phase == Phase.CLARIFY:
            reply = await _phase_clarify(ctx, state, stripped, llm)
        elif state.phase == Phase.CONFIRM_TXN:
            reply = await _phase_confirm_txn(ctx, state, stripped, llm)
        elif state.phase == Phase.CONFIRM_ACT:
            reply = await _phase_confirm_act(ctx, state, stripped, llm)
        elif state.phase == Phase.CARD_OFFER:
            reply = await _phase_card_offer(ctx, state, stripped, llm)
        else:
            raise RuntimeError(f"unknown phase: {state.phase}")

        state.messages.append(("agent", reply))
        return state, reply
