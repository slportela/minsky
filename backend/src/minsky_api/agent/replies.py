"""Spanish reply templates (Jinja2 under prompts/). Facts come from tools / rule ids only."""

from __future__ import annotations

from minsky_api.agent.prompts import render
from minsky_api.tools.schemas import TransactionView


def ask_confirm_txn(txn: TransactionView) -> str:
    merchant = txn.merchant_name or "comercio desconocido"
    when = txn.transaction_date.date().isoformat() if txn.transaction_date else "fecha desconocida"
    return render(
        "agent.reply.confirm_txn.j2",
        merchant=merchant,
        amount_usd=txn.amount_usd,
        when=when,
        transaction_id=txn.transaction_id,
    )


def ask_clarify_none() -> str:
    return render("agent.reply.clarify_none.j2")


def ask_clarify_many(txns: list[TransactionView]) -> str:
    rows = [
        {
            "merchant": t.merchant_name or "comercio desconocido",
            "amount_usd": t.amount_usd,
            "when": t.transaction_date.date().isoformat() if t.transaction_date else "?",
            "transaction_id": t.transaction_id,
        }
        for t in txns
    ]
    return render("agent.reply.clarify_many.j2", rows=rows)


def ask_confirm_open(*, rule_id: str, txn_id: str) -> str:
    return render("agent.reply.confirm_open.j2", rule_id=rule_id, txn_id=txn_id)


def ask_card_block(*, rule_id: str) -> str:
    return render("agent.reply.card_offer.j2", rule_id=rule_id)


def opened_dispute(*, dispute_id: str, rule_id: str) -> str:
    return render("agent.reply.opened.j2", dispute_id=dispute_id, rule_id=rule_id)


def policy_inform(*, rule_id: str, existing_dispute_id: str | None = None) -> str:
    return render(
        "agent.reply.policy_inform.j2",
        rule_id=rule_id,
        existing_dispute_id=existing_dispute_id,
    )


def policy_refuse(*, rule_id: str) -> str:
    return render("agent.reply.policy_refuse.j2", rule_id=rule_id)


def handoff_done(*, handoff_id: str, rule_id: str | None = None) -> str:
    return render("agent.reply.handoff.j2", handoff_id=handoff_id, rule_id=rule_id)


def card_blocked_handoff(*, handoff_id: str, rule_id: str | None = None) -> str:
    return render("agent.reply.card_blocked_handoff.j2", handoff_id=handoff_id, rule_id=rule_id)


def turn_limit() -> str:
    return render("agent.reply.turn_limit.j2")


def clarify_limit() -> str:
    return render("agent.reply.clarify_limit.j2")


def out_of_scope_handoff(*, handoff_id: str) -> str:
    return render("agent.reply.out_of_scope.j2", handoff_id=handoff_id)


def already_done() -> str:
    return render("agent.reply.already_done.j2")


def aborted() -> str:
    return render("agent.reply.aborted.j2")


def need_yes_or_no() -> str:
    return render("agent.reply.need_yes_no.j2")
