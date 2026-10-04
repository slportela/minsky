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
