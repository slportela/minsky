"""What the customer reads in agentic mode when code, not the search agent, is speaking (es, pt).

Every sentence here is written from verified facts (the transaction re-read by id, the policy's rule). The
question a "yes" authorizes is always code-written, so the customer consents to exactly what runs (AGENTS rule 1).
"""

from __future__ import annotations

from decimal import Decimal

from minsky_api.agent.wording import (
    confirm_question,
    fallback_sentence,
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
    pt = _pt(language)
    if reason == "out_of_scope":
        if pt:
            return (
                "Isso não é algo que eu consiga resolver por aqui, mas um atendente pode ajudar. "
                "Quer que eu passe a sua consulta para um atendente? Responda sim ou não."
            )
        return (
            "Eso no es algo que pueda resolver por aquí, pero un asesor sí podría ayudarte. "
            "¿Quieres que pase tu consulta a un asesor? Responde sí o no."
        )
    lead = (
        "Não consegui encontrar essa transação com o que você me contou, e sinto muito por não ter conseguido "
        "ajudar mais."
        if pt
        else "No logré encontrar esa transacción con lo que me contaste, y lamento no haber podido ayudarte más."
    )
    return f"{lead} {_escalation_question(language)}"


def repeat_offer(language: str, question: str) -> str:
    """The reply was neither yes nor no and nothing in it changes the offer: say it again, whole, so it is clear."""
    return f"{'Não ficou claro.' if _pt(language) else 'No me quedó claro.'} {question}"


def declined_escalation(language: str) -> str:
    """The customer said no to a person. Nothing was registered, and the door stays open."""
    if _pt(language):
        return (
            "Tudo bem, não vou passar o seu caso para um atendente. Se lembrar de mais alguma coisa sobre a "
            "cobrança ou mudar de ideia, escreva aqui e retomamos."
        )
    return (
        "Está bien, no pasaré tu caso a un asesor. Si recuerdas algo más del cargo o cambias de opinión, "
        "escríbeme y lo retomamos."
    )


def handoff_done(language: str, handoff_id: str, *, card_blocked: bool = False) -> str:
    """A person has the case, read back from the case queue: what they have and what the customer need not do."""
    blocked = f"{fallback_sentence(language, {'card_blocked': True})} " if card_blocked else ""
    if _pt(language):
        return (
            f"{blocked}Um atendente da nossa equipe vai assumir o seu caso e já tem o resumo da nossa conversa, "
            f"então você não precisa repetir nada. A sua referência é {handoff_id}."
        )
    return (
        f"{blocked}Un asesor de nuestro equipo tomará tu caso y ya tiene el resumen de lo que hablamos, así que no "
        f"hace falta que repitas nada. Tu referencia es {handoff_id}."
    )


def ask_again(language: str) -> str:
    return safe_sentence("ask_again", language, {})


def already_done(language: str, case_ref: str) -> str:
    """After a case exists: say so with its real reference. Without one there is nothing to say: the search resumes."""
    if _pt(language):
        return f"O seu caso já está registrado com a referência {case_ref}. Se precisar de algo mais, escreva aqui."
    return f"Tu caso ya quedó registrado con la referencia {case_ref}. Si necesitas algo más, escríbeme aquí."


def need_more_detail(language: str) -> str:
    """The agent produced nothing usable (budget or checks): ask for a detail, in code."""
    return (
        "Não consegui avançar com isso. Pode me dizer o estabelecimento, o valor aproximado ou a data?"
        if _pt(language)
        else "No logré avanzar con eso. ¿Puedes decirme el comercio, el monto aproximado o la fecha?"
    )
