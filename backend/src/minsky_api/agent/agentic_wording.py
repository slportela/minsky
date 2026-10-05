"""What the customer reads in agentic mode when code, not the search agent, is speaking (es, pt).

Every sentence here is written from verified facts (the transaction re-read by id, the policy's rule). The
question a "yes" authorizes is always code-written, so the customer consents to exactly what runs (AGENTS rule 1).
"""

from __future__ import annotations

from decimal import Decimal

from minsky_api.agent.wording import (
    confirm_question,
    done_fallback,
    human_amount,
    human_date,
    policy_reason,
    safe_sentence,
    transaction_noun,
)
from minsky_api.tools.schemas import TransactionView


def _pt(language: str) -> bool:
    return language == "pt"


def _money(txn: TransactionView) -> str:
    """The amount as the customer saw it: in its own currency, with the dollar value when that is another one."""
    usd = human_amount(txn.amount_usd)
    if txn.amount is None or not txn.currency or txn.currency == "USD":
        return usd
    return f"{Decimal(txn.amount).quantize(Decimal('0.01'))} {txn.currency} (≈ {usd})"


def transaction_card(txn: TransactionView, language: str) -> str:
    """The transaction the agent proposed, read back from the bank, and the question that authorizes opening."""
    kind = transaction_noun(txn.transaction_type, language)
    when = human_date(txn.transaction_date.date(), language) if txn.transaction_date else None
    pt = _pt(language)
    where = txn.merchant_name or ("sem estabelecimento" if pt else "sin comercio")
    on = f" em {when}" if (pt and when) else (f" el {when}" if when else "")
    head = (
        f"Encontrei esta transação: {kind} — {where}, {_money(txn)}{on}."
        if pt
        else f"Encontré este movimiento: {kind} — {where}, {_money(txn)}{on}."
    )
    ask = (
        "Quer que eu abra uma contestação por ela? Responda sim ou não."
        if pt
        else "¿Quieres que abra un reclamo por este movimiento? Responde sí o no."
    )
    return f"{head} {ask}"


def recognize_question(language: str) -> str:
    return (
        "Antes de seguir: você reconhece ter feito essa transação? Responda sim ou não."
        if _pt(language)
        else "Antes de seguir: ¿reconoces haber hecho este movimiento? Responde sí o no."
    )


def offer_block(language: str, rule_id: str) -> str:
    reason = policy_reason(rule_id, language) or ""
    return f"{reason[:1].upper() + reason[1:]}. {confirm_question('offer_block', language)}".strip()


def denial(language: str, rule_id: str, existing_dispute_id: str | None) -> str:
    """Why the dispute cannot go ahead now, from the policy's rule, and the offer of a person."""
    reason = policy_reason(rule_id, language) or ""
    pt = _pt(language)
    if pt:
        text = (
            f"Não consigo abrir essa contestação automaticamente: {reason}."
            if reason
            else "Não consigo abrir essa contestação automaticamente."
        )
        if existing_dispute_id:
            text += f" A referência da contestação é {existing_dispute_id}."
        return f"{text} {_escalation_question(language)}"
    text = (
        f"No puedo abrir este reclamo automáticamente: {reason}."
        if reason
        else "No puedo abrir este reclamo automáticamente."
    )
    if existing_dispute_id:
        text += f" La referencia del reclamo es {existing_dispute_id}."
    return f"{text} {_escalation_question(language)}"


def _escalation_question(language: str) -> str:
    return (
        "Quer que eu passe o seu caso para um atendente, para que ele o revise? Responda sim ou não."
        if _pt(language)
        else "¿Quieres que pase tu caso a un asesor para que lo revise? Responde sí o no."
    )


def offer_escalation_without_match(language: str, reason: str) -> str:
    """The search ended without a transaction to dispute (not found, or outside what the assistant does)."""
    if reason == "out_of_scope":
        lead = (
            "Isso não é algo que eu possa resolver por aqui."
            if _pt(language)
            else "Eso no es algo que pueda resolver por aquí."
        )
    else:
        lead = "Não consegui identificar a transação." if _pt(language) else "No logré identificar la transacción."
    return f"{lead} {_escalation_question(language)}"


def declined_escalation(language: str) -> str:
    return safe_sentence("abort", language, {})


def ask_again(language: str) -> str:
    return safe_sentence("ask_again", language, {})


def already_done(language: str) -> str:
    return done_fallback(language)


def need_more_detail(language: str) -> str:
    """The agent produced nothing usable (budget or checks): ask for a detail, in code."""
    return (
        "Não consegui avançar com isso. Pode me dizer o estabelecimento, o valor aproximado ou a data?"
        if _pt(language)
        else "No logré avanzar con eso. ¿Puedes decirme el comercio, el monto aproximado o la fecha?"
    )
