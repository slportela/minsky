"""Customer-facing wording that code owns: dates, amounts, policy reasons and fallback sentences (es, pt).

The model writes most replies, but anything that must be exact or must survive a model failure is
written here. Rule ids stay internal (audit, traces, console); customers hear the reason in plain words.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

_MONTHS = {
    "es": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
           "noviembre", "diciembre"),
    "pt": ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
           "novembro", "dezembro"),
}  # fmt: skip

# What the customer hears for each policy rule. Keys are rule-id prefixes (the id's text part may change).
_REASONS = {
    "es": {
        "D01": "el pago fue rechazado, así que el dinero nunca salió de tu cuenta",
        "D02": "ese cargo ya fue revertido y el dinero volvió a tu cuenta",
        "D03": "el cargo todavía está pendiente; se puede reclamar cuando quede registrado",
        "D04": "ya hay un reclamo abierto para ese cargo",
        "D05": "el cargo tiene más de 120 días, que es el plazo para reclamar en este canal",
        "D06": "si no hiciste esta compra, alguien podría estar usando tu tarjeta",
        "D07": "por el monto, un especialista debe revisar el caso",
        "D08": "como tienes otro reclamo reciente, un especialista revisará el caso",
        "D09": "el cargo cumple las condiciones para abrir el reclamo ahora mismo",
    },
    "pt": {
        "D01": "o pagamento foi recusado, então o dinheiro nunca saiu da sua conta",
        "D02": "essa cobrança já foi estornada e o dinheiro voltou para a sua conta",
        "D03": "a cobrança ainda está pendente; você pode contestá-la quando for registrada",
        "D04": "já existe uma contestação aberta para essa cobrança",
        "D05": "a cobrança tem mais de 120 dias, que é o prazo para contestar por este canal",
        "D06": "se você não fez essa compra, alguém pode estar usando o seu cartão",
        "D07": "pelo valor, um especialista precisa analisar o caso",
        "D08": "como você tem outra reclamação recente, um especialista vai analisar o caso",
        "D09": "a cobrança cumpre as condições para abrir a contestação agora mesmo",
    },
}


def _lang(language: str) -> str:
    return "pt" if language == "pt" else "es"


def human_date(value: date, language: str) -> str:
    months = _MONTHS[_lang(language)]
    return f"{value.day} de {months[value.month - 1]} de {value.year}"


def human_amount(value: Decimal | float | str) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01'))} USD"


def policy_reason(rule_id: str | None, language: str) -> str | None:
    if not rule_id:
        return None
    return _REASONS[_lang(language)].get(rule_id[:3])


def fallback_sentence(language: str, facts: dict[str, object]) -> str:
    """A complete reply about a write that already happened, for when the model cannot phrase it."""
    pt = _lang(language) == "pt"
    dispute_id = facts.get("dispute_id") or facts.get("existing_dispute_id")
    handoff_id = facts.get("handoff_id")
    parts: list[str] = []
    if facts.get("card_blocked") is True:
        parts.append(
            "Seu cartão já está bloqueado para proteger o seu dinheiro."
            if pt
            else "Tu tarjeta ya está bloqueada para proteger tu dinero."
        )
    if isinstance(dispute_id, str) and dispute_id:
        parts.append(
            f"Sua contestação {dispute_id} está registrada; nossa equipe vai analisá-la e avisaremos cada avanço."
            if pt
            else f"Tu reclamo {dispute_id} quedó registrado; nuestro equipo lo revisará y te avisaremos de cada avance."
        )
    if isinstance(handoff_id, str) and handoff_id:
        parts.append(
            f"Um especialista da nossa equipe vai assumir o seu caso. Sua referência é {handoff_id}."
            if pt
            else f"Un especialista de nuestro equipo tomará tu caso. Tu referencia es {handoff_id}."
        )
    if not parts:
        parts.append("Pronto, registramos a sua solicitação." if pt else "Listo, registramos tu solicitud.")
    return " ".join(parts)


# What the customer calls the transaction: a transfer or a withdrawal is not a "cargo". (noun, "this <noun>")
_NOUNS = {
    "es": {
        "Purchase": ("cargo", "este cargo"),
        "Transfer": ("transferencia", "esta transferencia"),
        "Withdrawal": ("retiro", "este retiro"),
        "Payment": ("pago", "este pago"),
        "Adjustment": ("ajuste", "este ajuste"),
        "Deposit": ("depósito", "este depósito"),
    },
    "pt": {
        "Purchase": ("cobrança", "esta cobrança"),
        "Transfer": ("transferência", "esta transferência"),
        "Withdrawal": ("saque", "este saque"),
        "Payment": ("pagamento", "este pagamento"),
        "Adjustment": ("ajuste", "este ajuste"),
        "Deposit": ("depósito", "este depósito"),
    },
}


def transaction_noun(transaction_type: str | None, language: str) -> str:
    return _NOUNS[_lang(language)].get(transaction_type or "", _NOUNS[_lang(language)]["Purchase"])[0]


def _this(transaction_type: str | None, language: str) -> str:
    return _NOUNS[_lang(language)].get(transaction_type or "", _NOUNS[_lang(language)]["Purchase"])[1]


# The question a "yes" authorizes is written by code, never by the model, so the customer always
# consents to exactly the action that runs (AGENTS rule 1). Appended to the model's sentence.
def confirm_question(act: str, language: str, transaction_type: str | None = None) -> str:
    this = _this(transaction_type, language)
    if _lang(language) == "pt":
        if act == "confirm_open":
            return f"Posso abrir a contestação de {this}? Responda sim ou não."
        return "Posso bloquear o seu cartão agora? Responda sim ou não."
    if act == "confirm_open":
        return f"¿Abro el reclamo por {this}? Responde sí o no."
    return "¿Bloqueo tu tarjeta ahora? Responde sí o no."


def inform_fallback(language: str, rule_id: str | None, existing_dispute_id: str | None) -> str:
    """A complete, safe answer when the model cannot phrase a policy explanation (for example D04)."""
    reason = policy_reason(rule_id, language) or ""
    text = reason[:1].upper() + reason[1:] + "." if reason else ""
    if existing_dispute_id:
        text += (
            f" A referência da sua contestação é {existing_dispute_id}."
            if _lang(language) == "pt"
            else f" La referencia de tu reclamo es {existing_dispute_id}."
        )
    return text.strip() or done_fallback(language)


def done_fallback(language: str) -> str:
    """After the conversation's case is settled: no new facts, only what to do next."""
    if _lang(language) == "pt":
        return "O seu caso já está registrado com a referência que enviei. Se precisar de outra coisa, escreva aqui."
    return "Tu caso ya quedó registrado con la referencia que te envié. Si necesitas algo más, escríbeme aquí."
