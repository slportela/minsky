"""Turn a verified write (a dispute opened, a handoff created) into a back-office case.

The case carries what an agent needs without reading the transcript: verified transaction facts read
from bank.* (never from the conversation), what the customer said (labeled as their claim), what the
system did, the open questions, the triage and the bank's historical resolution time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from minsky_api.policy.triage import CaseKind, triage
from minsky_api.store.benchmarks import BenchmarkStore
from minsky_api.store.cases_memory import CaseRecord
from minsky_api.store.errors import StoreError
from minsky_api.store.models import Transaction
from minsky_api.tools.context import ToolContext

# Benchmarks are by complaint category; disputed charges are filed under "Transactions" today.
_BENCHMARK_CATEGORY = "Transactions"

_OPEN_QUESTIONS: dict[str, tuple[str, ...]] = {
    "D06": (
        "Did the customer make or authorize this charge? They say they did not.",
        "Are there other recent charges on this card the customer does not recognize?",
        "Does the customer need a replacement card?",
    ),
    "D07": (
        "Is there evidence from the merchant or the counterparty for this amount?",
        "Should the bank grant a provisional credit while the case is investigated?",
    ),
    "D08": ("Is this charge related to the customer's other recent complaint?",),
    "D05": ("The charge is older than the 120-day window: is there a reason to accept it anyway?",),
    "clarify_exhausted": ("Which transaction does the customer mean? The assistant could not find a unique match.",),
    "unclear_confirmation": (
        "What does the customer want? Their last replies to a yes-or-no question were not a plain yes or no, "
        "so the assistant could not confirm what to do.",
    ),
    "out_of_scope": ("What does the customer need? The request is outside dispute intake.",),
    "max_turns": ("The conversation hit the turn limit: what is still unresolved?",),
}
_D09_QUESTIONS = ("Merchant response and evidence for the disputed charge.",)


def verified_transaction_facts(txn: Transaction) -> dict[str, Any]:
    return {
        "transaction_id": txn.transaction_id,
        "merchant": txn.merchant_name,
        "amount": str(txn.amount) if txn.amount is not None else None,
        "currency": txn.currency,
        "amount_usd": str(txn.amount_usd),
        "transaction_date": txn.transaction_date.isoformat() if txn.transaction_date else None,
        "transaction_type": txn.transaction_type,
        "transaction_status": txn.transaction_status,
        "channel": txn.channel,
    }


_DISAGREEMENT = (
    "The classifier read this as an unrecognized charge, but the customer did not say they did not make it: "
    "confirm with the customer whether they made the charge."
)


def _open_questions(
    kind: CaseKind, reason: str, rule_id: str | None, customer_facts: dict[str, Any] | None = None
) -> tuple[str, ...]:
    facts = customer_facts or {}
    disagree = facts.get("dispute_type") == "unrecognized_charge" and not facts.get("customer_says_not_me")
    if kind == CaseKind.DISPUTE:
        return _D09_QUESTIONS + ((_DISAGREEMENT,) if disagree else ())
    if rule_id and rule_id[:3] in _OPEN_QUESTIONS:
        return _OPEN_QUESTIONS[rule_id[:3]]
    return _OPEN_QUESTIONS.get(reason, ("Review the conversation summary and contact the customer.",))


def _summary(kind: CaseKind, reason: str, rule_id: str | None, facts: dict[str, Any], card_blocked: bool) -> str:
    charge = ""
    if facts.get("merchant") or facts.get("amount_usd"):
        when = (facts.get("transaction_date") or "")[:10]
        charge = f" the {facts.get('amount_usd')} USD charge at {facts.get('merchant') or 'an unknown merchant'}"
        charge += f" on {when}" if when else ""
    kind_of = str(facts.get("dispute_type") or facts.get("dispute_type_suggested") or "").replace("_", " ")
    type_note = f" Type: {kind_of} (learned router, suggestion)." if kind_of else ""
    if kind == CaseKind.DISPUTE:
        return (
            f"Customer disputes{charge}. Dispute opened automatically under rule {rule_id or 'D09-eligible'}."
            + type_note
        )
    says_not_me = facts.get("customer_says_not_me") is True
    lead = {
        "possible_fraud": "Possible fraud: the customer says they did not make",
        "possible_fraud_no_block": "Possible fraud, card not blocked at the customer's request:",
        "policy_escalate_agent": "Dispute that needs an agent:",
        "policy_refuse": "Dispute outside the automatic window; the customer was offered an agent for",
        "clarify_exhausted": "The assistant could not identify the charge the customer means",
        "unclear_confirmation": "The customer did not answer a yes-or-no question with a plain yes or no",
        "out_of_scope": "Request outside dispute intake",
        "max_turns": "Conversation reached the turn limit",
    }.get(reason, f"Handoff ({reason})")
    text = f"{lead}{charge}." if charge else f"{lead}."
    if says_not_me and reason != "possible_fraud":
        text += " The customer says they did not make it."
    if card_blocked:
        text += " Card blocked with the customer's explicit confirmation."
    if rule_id:
        text += f" Policy rule {rule_id}."
    return text + type_note


async def _expected_days(ctx: ToolContext, priority: str) -> float | None:
    try:
        row = await BenchmarkStore(ctx.db).get(_BENCHMARK_CATEGORY, priority)
    except StoreError:
        return None  # informational only: the case is still queued, just without the historical estimate
    return row.median_resolution_days if row is not None else None


async def enqueue_case(
    ctx: ToolContext,
    *,
    kind: CaseKind,
    case_id: str,
    customer_id: str,
    reason: str,
    rule_id: str | None,
    txn: Transaction | None,
    customer_facts: dict[str, Any],
    actions: tuple[str, ...],
    created_at: datetime,
) -> CaseRecord:
    verified = verified_transaction_facts(txn) if txn is not None else {}
    card_blocked = any(action.startswith("card_blocked:") for action in actions)
    decision = triage(
        kind=kind,
        rule_id=rule_id,
        amount_usd=float(txn.amount_usd) if txn is not None else None,
        card_blocked=card_blocked,
        created_at=created_at,
    )
    facts = {
        "verified": verified,
        "customer_said": {k: v for k, v in customer_facts.items() if k not in ("transaction_id", "route")},
    }
    record = CaseRecord(
        case_id=case_id,
        kind=kind.value,
        customer_id=customer_id,
        rule_id=rule_id,
        reason=reason,
        priority=decision.priority.value,
        queue=decision.queue.value,
        triage_reason=decision.reason,
        due_at=decision.due_at,
        status="new",
        summary=_summary(kind, reason, rule_id, {**verified, **customer_facts}, card_blocked),
        facts=facts,
        actions=actions,
        open_questions=_open_questions(kind, reason, rule_id, customer_facts),
        expected_resolution_days=await _expected_days(ctx, decision.priority.value),
        created_at=created_at,
        updated_at=created_at,
    )
    return ctx.cases.enqueue_case(record)
