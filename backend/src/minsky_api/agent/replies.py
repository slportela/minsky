"""Reply templates (Jinja2 under prompts/). Wording comes from the phrase catalog; facts come from tools."""

from __future__ import annotations

from minsky_api.agent.prompts import render
from minsky_api.tools.schemas import TransactionView

# Same keys in every language. A new language is one new entry. A missing key fails at render.
PHRASES: dict[str, dict[str, str]] = {
    "es": {
        "unknown_merchant": "comercio desconocido",
        "unknown_when": "fecha desconocida",
        "yes_hint": "Responde sí para confirmar, o no para cancelar.",
        "found_one": "Encontré este cargo",
        "confirm_this": "¿Es este el que quieres disputar?",
        "found_many": "Encontré varios cargos. Indica el número del que quieres disputar:",
        "clarify_none": (
            "No encontré un cargo que coincida. ¿Puedes darme el comercio, el monto o la fecha con más detalle?"
        ),
        "policy_label": "Según la política",
        "can_open": "puedo abrir el reclamo del cargo",
        "can_open_tail": "de forma automática.",
        "confirm_open": "¿Confirmas que lo abra?",
        "opened": "Listo: abrí el reclamo",
        "rule_word": "regla",
        "opened_more": "Te avisaremos del avance. ¿Necesitas algo más sobre este caso?",
        "fraud_case": "Este caso parece fraude",
        "block_ask": "¿Quieres que bloquee la tarjeta ahora? Solo lo haré con un sí explícito.",
        "cannot_open": "No puedo abrir el reclamo automáticamente",
        "human_advisor": "Te derivo con un asesor humano.",
        "handoff_intro": "Te derivo con un asesor",
        "handoff_ref": "Referencia de traspaso",
        "card_blocked": "Bloqueé la tarjeta y te derivo al equipo de fraude",
        "clarify_limit": "No logré identificar el cargo con claridad. Te derivo con un asesor.",
        "turn_limit": "Llegamos al límite de mensajes de esta conversación. Te derivo con un asesor.",
        "out_of_scope": "Eso está fuera de lo que puedo resolver aquí (reclamos de cargos). Te derivo con un asesor.",
        "reference_short": "Referencia",
        "already_done": "Esta conversación ya terminó. Si necesitas otro reclamo, inicia una conversación nueva.",
        "aborted": "De acuerdo, no haré ningún cambio. Si quieres intentar de nuevo, describe el cargo.",
        "not_understood": "No entendí.",
        "d01": "Ese intento fue rechazado: no se cobró nada, no hay nada que disputar.",
        "d02": "Ese cargo ya fue revertido: el dinero ya volvió.",
        "d03": "Ese cargo sigue pendiente: se puede disputar cuando quede registrado.",
        "d04": "Ya hay un reclamo abierto para ese cargo",
        "d04_tail": "; te paso con un asesor si hace falta.",
        "policy_fallback": "No hay nada que disputar automáticamente",
    },
    "pt": {
        "unknown_merchant": "comércio desconhecido",
        "unknown_when": "data desconhecida",
        "yes_hint": "Responda sim para confirmar, ou não para cancelar.",
        "found_one": "Encontrei esta cobrança",
        "confirm_this": "É esta que você quer disputar?",
        "found_many": "Encontrei várias cobranças. Indique o número da que você quer disputar:",
        "clarify_none": (
            "Não encontrei uma cobrança que coincida. Pode me dar o comércio, o valor ou a data com mais detalhe?"
        ),
        "policy_label": "Segundo a política",
        "can_open": "posso abrir a reclamação da cobrança",
        "can_open_tail": "de forma automática.",
        "confirm_open": "Confirma que eu abra?",
        "opened": "Pronto: abri a reclamação",
        "rule_word": "regra",
        "opened_more": "Avisaremos sobre o andamento. Precisa de algo mais sobre este caso?",
        "fraud_case": "Este caso parece fraude",
        "block_ask": "Quer que eu bloqueie o cartão agora? Só farei isso com um sim explícito.",
        "cannot_open": "Não posso abrir a reclamação automaticamente",
        "human_advisor": "Te transfiro para um assessor humano.",
        "handoff_intro": "Te transfiro para um assessor",
        "handoff_ref": "Referência",
        "card_blocked": "Bloqueei o cartão e te transfiro para a equipe de fraude",
        "clarify_limit": "Não consegui identificar a cobrança com clareza. Te transfiro para um assessor.",
        "turn_limit": "Chegamos ao limite de mensagens desta conversa. Te transfiro para um assessor.",
        "out_of_scope": (
            "Isso está fora do que posso resolver aqui (reclamações de cobranças). Te transfiro para um assessor."
        ),
        "reference_short": "Referência",
        "already_done": "Esta conversa já terminou. Se precisar de outra reclamação, inicie uma conversa nova.",
        "aborted": "De acordo, não farei nenhuma alteração. Se quiser tentar de novo, descreva a cobrança.",
        "not_understood": "Não entendi.",
        "d01": "Essa tentativa foi recusada: nada foi cobrado, não há o que disputar.",
        "d02": "Essa cobrança já foi estornada: o dinheiro já voltou.",
        "d03": "Essa cobrança ainda está pendente: pode ser disputada quando for registrada.",
        "d04": "Já há uma reclamação aberta para essa cobrança",
        "d04_tail": "; te transfiro para um assessor se for preciso.",
        "policy_fallback": "Não há nada para disputar automaticamente",
    },
}


def phrases_for(language: str) -> dict[str, str]:
    try:
        chosen = PHRASES[language]
    except KeyError as error:
        raise KeyError(f"no reply phrases for language {language!r}") from error
    missing = set(PHRASES["es"]) - chosen.keys()
    if missing:
        raise KeyError(f"{language} reply phrases missing {sorted(missing)}")
    return chosen


def _render(template: str, language: str, **kwargs: object) -> str:
    return render(template, **phrases_for(language), **kwargs)


def ask_confirm_txn(txn: TransactionView, *, language: str = "es") -> str:
    words = phrases_for(language)
    merchant = txn.merchant_name or words["unknown_merchant"]
    when = txn.transaction_date.date().isoformat() if txn.transaction_date else words["unknown_when"]
    return _render(
        "agent.reply.confirm_txn.j2",
        language,
        merchant=merchant,
        amount_usd=txn.amount_usd,
        when=when,
        transaction_id=txn.transaction_id,
    )


def ask_clarify_none(*, language: str = "es") -> str:
    return _render("agent.reply.clarify_none.j2", language)


def ask_clarify_many(txns: list[TransactionView], *, language: str = "es") -> str:
    words = phrases_for(language)
    rows = [
        {
            "merchant": t.merchant_name or words["unknown_merchant"],
            "amount_usd": t.amount_usd,
            "when": t.transaction_date.date().isoformat() if t.transaction_date else "?",
            "transaction_id": t.transaction_id,
        }
        for t in txns
    ]
    return _render("agent.reply.clarify_many.j2", language, rows=rows)


def ask_confirm_open(*, rule_id: str, txn_id: str, language: str = "es") -> str:
    return _render("agent.reply.confirm_open.j2", language, rule_id=rule_id, txn_id=txn_id)


def ask_card_block(*, rule_id: str, language: str = "es") -> str:
    return _render("agent.reply.card_offer.j2", language, rule_id=rule_id)


def opened_dispute(*, dispute_id: str, rule_id: str, language: str = "es") -> str:
    return _render("agent.reply.opened.j2", language, dispute_id=dispute_id, rule_id=rule_id)


def policy_inform(*, rule_id: str, existing_dispute_id: str | None = None, language: str = "es") -> str:
    return _render(
        "agent.reply.policy_inform.j2",
        language,
        rule_id=rule_id,
        existing_dispute_id=existing_dispute_id,
    )


def policy_refuse(*, rule_id: str, language: str = "es") -> str:
    return _render("agent.reply.policy_refuse.j2", language, rule_id=rule_id)


def handoff_done(*, handoff_id: str, rule_id: str | None = None, language: str = "es") -> str:
    return _render("agent.reply.handoff.j2", language, handoff_id=handoff_id, rule_id=rule_id)


def card_blocked_handoff(*, handoff_id: str, rule_id: str | None = None, language: str = "es") -> str:
    return _render("agent.reply.card_blocked_handoff.j2", language, handoff_id=handoff_id, rule_id=rule_id)


def turn_limit(*, handoff_id: str, language: str = "es") -> str:
    return _render("agent.reply.turn_limit.j2", language, handoff_id=handoff_id)


def clarify_limit(*, handoff_id: str, language: str = "es") -> str:
    return _render("agent.reply.clarify_limit.j2", language, handoff_id=handoff_id)


def out_of_scope_handoff(*, handoff_id: str, language: str = "es") -> str:
    return _render("agent.reply.out_of_scope.j2", language, handoff_id=handoff_id)


def already_done(*, language: str = "es") -> str:
    return _render("agent.reply.already_done.j2", language)


def aborted(*, language: str = "es") -> str:
    return _render("agent.reply.aborted.j2", language)


def need_yes_or_no(*, language: str = "es") -> str:
    return _render("agent.reply.need_yes_no.j2", language)
