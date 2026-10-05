"""Customer-facing wording that code owns: dates, amounts, policy reasons and fallback sentences (es, pt).

The model writes most replies, but anything that must be exact or must survive a model failure is
written here. Rule ids stay internal (audit, traces, console); customers hear the reason in plain words.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from minsky_api.agent.state import Terminal

from minsky_api.tools.schemas import FindTransactionsResult, MatchedTransactionView, TransactionView

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


_ONLY_YES_NO = {
    "es": "En esta parte del proceso solo puedes responder «sí» o «no».",
    "pt": "Nesta parte do processo você só pode responder «sim» ou «não».",
}
# A closing "Responde sí o no." the model (or the original question) already carries: the notice says it better.
_CLOSING_YES_NO = re.compile(
    r"\s*(?:responde|responda)\s+(?:sí|si|sim)\s+(?:o|ou)\s+(?:no|não|nao)\s*[.!]?\s*$", re.IGNORECASE
)


def with_only_yes_no(text: str, language: str) -> str:
    """The reply asked the customer again: code always says that only a yes or a no works at this step.

    The model rephrases the pending question; it is not trusted to say how to answer, which is exactly what
    went missing when a customer answered "lo reconozco" and was asked for more details instead. A closing
    "Responde sí o no" is replaced by the notice, and a text that already ends with the notice is left alone.
    """
    notice = _ONLY_YES_NO[_lang(language)]
    body = text.rstrip()
    if body.endswith(notice):
        return body
    body = _CLOSING_YES_NO.sub("", body).rstrip()
    return f"{body} {notice}" if body else notice


def with_candidates(text: str, candidates: str) -> str:
    """The model asks which transaction; code lists the options, so the customer always sees them exactly.

    A reply that already names every option exactly, however it punctuates them, is left alone, so the
    options never appear twice. Otherwise the code-written list is appended.
    """
    options = [line.split(". ", 1)[-1] for line in candidates.splitlines()]
    return text if all(option in text for option in options) else f"{text.rstrip()}\n{candidates}"


def clarify_fallback(language: str, candidates: str | None) -> str:
    """A complete clarification when the model cannot phrase one: ask which transaction, or ask for details."""
    pt = _lang(language) == "pt"
    if candidates:
        head = (
            "Encontrei mais de uma transação que corresponde. Qual delas você quer revisar?"
            if pt
            else "Encontré más de una transacción que coincide. ¿Cuál es la que quieres revisar?"
        )
        return f"{head}\n{candidates}"
    if pt:
        return "Não encontrei essa transação. Pode me dizer o estabelecimento, o valor ou a data?"
    return "No encontré esa transacción. ¿Puedes decirme el comercio, el monto o la fecha?"


def safe_sentence(act: str, language: str, facts: dict[str, object]) -> str:
    """A code-written sentence for an act when the model cannot phrase a valid one.

    It says only what the code already knows from `facts` (the reason a policy rule gives, the transaction the
    customer must confirm). It never reports an action. An act with no sentence here is a bug and raises.
    """
    pt = _lang(language) == "pt"
    candidates = facts.get("candidates")
    if act == "clarify":
        return clarify_fallback(language, candidates if isinstance(candidates, str) else None)
    if act == "ask_again":
        pending = facts.get("pending_question")
        lead = "Não ficou claro." if pt else "No me quedó claro."
        return f"{lead} {pending}" if isinstance(pending, str) and pending else lead
    if act == "abort":
        return (
            "Entendido, não vou fazer nada com este caso. Se precisar de algo mais, escreva aqui."
            if pt
            else "Entendido, no haré nada con este caso. Si necesitas algo más, escríbeme aquí."
        )
    if act in ("confirm_open", "offer_block"):
        reason = facts.get("reason")
        if isinstance(reason, str) and reason:
            return reason[:1].upper() + reason[1:] + "."
        return "Com o que você me contou, posso seguir." if pt else "Con lo que me contaste, puedo seguir."
    if act == "confirm_txn":
        return confirm_txn_question(language, facts)
    raise ValueError(f"no code-written sentence for act {act!r}")


def _feminine(noun: str, language: str) -> bool:
    """Gender of a transaction noun, read from the same table as its demonstrative ("esta transferencia")."""
    for known, this in _NOUNS[_lang(language)].values():
        if known == noun:
            return this.startswith("esta ")
    return False


def confirm_txn_question(language: str, facts: dict[str, object]) -> str:
    """The question that says which transaction the conversation is about. Code writes it, whole.

    It used to be the model's, and the model wrote "¿Reconoces este cargo…?". That contradicts a customer who says
    "no reconozco este cargo" (a "no" sounds right and is read as rejecting the charge) and one who says the amount is
    wrong. This asks only whether it is the charge the customer means, with the facts the code verified.
    """
    pt = _lang(language) == "pt"
    noun = facts.get("kind")
    noun = noun if isinstance(noun, str) and noun else transaction_noun(None, language)
    feminine = _feminine(noun, language)
    merchant, amount, when = facts.get("merchant"), facts.get("amount"), facts.get("when")
    parts = [str(merchant) if merchant else "", str(amount) if amount else ""]
    if when:
        parts.append(f"em {when}" if pt else f"del {when}")
    details = ", ".join(part for part in parts if part)
    suffix = f": {details}" if details else ""
    if pt:
        this, article = ("esta", "a") if feminine else ("este", "o")
        return f"É {this} {article} {noun} a que você se refere{suffix}? Responda sim ou não."
    this, article, relative = ("esta", "la", "a la que") if feminine else ("este", "el", "al que")
    return f"¿Es {this} {article} {noun} {relative} te refieres{suffix}? Responde sí o no."


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


_NEW_CONVERSATION = {
    "es": "Esta conversación terminó. Si quieres disputar otro cargo, inicia una nueva conversación.",
    "pt": "Esta conversa terminou. Se quiser contestar outra cobrança, inicie uma nova conversa.",
}


def terminal_reply(language: str, terminal: Terminal | None) -> str:
    """Every message after the case is settled gets this, whatever it says. Code writes it, whole.

    The model used to answer these messages and improvised: it offered "other options with the bank" to a customer
    whose case had just gone to a specialist, and asked for details on a case that was already closed. A closed
    conversation says its status, gives the reference, and says how to start another one.
    """
    pt = _lang(language) == "pt"
    closing = _NEW_CONVERSATION["pt" if pt else "es"]  # the unavailable outcome overrides it
    if terminal is None:
        return done_fallback(language)
    parts: list[str] = []
    if terminal.outcome == "dispute_opened":
        parts.append(
            f"A sua contestação está aberta com a referência {terminal.reference}."
            if pt
            else f"Tu reclamo está abierto con la referencia {terminal.reference}."
        )
    elif terminal.outcome == "handoff":
        if terminal.card_blocked:
            parts.append("O seu cartão está bloqueado." if pt else "Tu tarjeta está bloqueada.")
        if terminal.dispute_id:
            parts.append(
                f"A sua contestação {terminal.dispute_id} está aberta."
                if pt
                else f"Tu reclamo {terminal.dispute_id} está abierto."
            )
        parts.append(
            f"O seu caso está com um especialista, referência {terminal.reference}; a equipe vai avisar você."
            if pt
            else f"Tu caso está con un especialista, referencia {terminal.reference}; el equipo te avisará."
        )
    elif terminal.outcome == "informed":
        parts.append("Não abri nenhuma contestação nova." if pt else "No abrí ningún reclamo nuevo.")
        if terminal.dispute_id:
            parts.append(
                f"A contestação já aberta é {terminal.dispute_id}."
                if pt
                else f"El reclamo ya abierto es {terminal.dispute_id}."
            )
    elif terminal.outcome == "unavailable":
        if terminal.dispute_id:
            parts.append(
                f"A sua contestação {terminal.dispute_id} já foi registrada."
                if pt
                else f"Tu reclamo {terminal.dispute_id} ya quedó registrado."
            )
        parts.append(
            "O site está em manutenção neste momento. Por favor, consulte o serviço técnico."
            if pt
            else "El sitio está en mantenimiento en este momento. Por favor, consulta con el servicio técnico."
        )
        closing = (
            "Quando o site voltar a funcionar, inicie uma nova conversa."
            if pt
            else "Cuando el sitio vuelva a estar disponible, inicia una nueva conversación."
        )
    elif terminal.outcome == "no_case":
        parts.append(
            "Não abri nenhum caso nem passei você para ninguém." if pt else "No abrí ningún caso ni te pasé con nadie."
        )
    else:
        parts.append("Não fiz nenhuma alteração." if pt else "No hice ningún cambio.")
    return " ".join([*parts, closing])


def handoff_offer_question(language: str) -> str:
    """Offered when no charge could be identified. A handoff only happens if the customer says yes to this."""
    if _lang(language) == "pt":
        return (
            "Só posso ajudar se você me indicar uma cobrança concreta: o valor, o estabelecimento ou a data. "
            "Quer que eu passe você para um atendente humano? Responda sim ou não."
        )
    return (
        "Solo puedo ayudarte si me indicas un cargo concreto: el monto, el comercio o la fecha. "
        "¿Quieres que te pase con un asesor humano? Responde sí o no."
    )


def handoff_offer_declined(language: str) -> str:
    if _lang(language) == "pt":
        return "Entendido, não vou passar você para ninguém. Diga o valor, o estabelecimento ou a data da cobrança."
    return "Entendido, no te paso con nadie. Dime el monto, el comercio o la fecha del cargo."


def done_fallback(language: str) -> str:
    """After the conversation's case is settled: no new facts, only what to do next."""
    if _lang(language) == "pt":
        return "O seu caso já está registrado com a referência que enviei. Se precisar de outra coisa, escreva aqui."
    return "Tu caso ya quedó registrado con la referencia que te envié. Si necesitas algo más, escríbeme aquí."


# --- Near and closest matches: what was asked, what exists, what differs -----------------------------------------
# Written by code, not the model: every figure and date is the customer's own words or a verified row, and the
# customer must see exactly what differs before saying yes. The model is not asked to phrase this.

_A_KIND = {
    "es": {
        "Purchase": "un cargo",
        "Transfer": "una transferencia",
        "Withdrawal": "un retiro",
        "Payment": "un pago",
        "Adjustment": "un ajuste",
        "Deposit": "un depósito",
    },
    "pt": {
        "Purchase": "uma cobrança",
        "Transfer": "uma transferência",
        "Withdrawal": "um saque",
        "Payment": "um pagamento",
        "Adjustment": "um ajuste",
        "Deposit": "um depósito",
    },
}
_CATEGORY = {
    "es": {
        "Food": "comida",
        "Services": "servicios",
        "Transport": "transporte",
        "Entertainment": "entretenimiento",
        "Health": "salud",
        "Other": "otros",
    },
    "pt": {
        "Food": "alimentação",
        "Services": "serviços",
        "Transport": "transporte",
        "Entertainment": "entretenimento",
        "Health": "saúde",
        "Other": "outros",
    },
}
_FIT_TAGS = {
    "es": {
        ("amount", "near"): "monto cercano",
        ("amount", "miss"): "otro monto",
        ("date", "near"): "fecha cercana",
        ("date", "miss"): "otra fecha",
        ("merchant", "near"): "comercio parecido",
        ("merchant", "miss"): "otro comercio",
        ("kind", "miss"): "otro tipo de movimiento",
        ("category", "miss"): "otra categoría",
    },
    "pt": {
        ("amount", "near"): "valor próximo",
        ("amount", "miss"): "outro valor",
        ("date", "near"): "data próxima",
        ("date", "miss"): "outra data",
        ("merchant", "near"): "estabelecimento parecido",
        ("merchant", "miss"): "outro estabelecimento",
        ("kind", "miss"): "outro tipo de movimentação",
        ("category", "miss"): "outra categoria",
    },
}


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _requested(found: FindTransactionsResult, language: str) -> str:
    """'un cargo de aproximadamente 80 USD del 17 de junio de 2026 en Super Ahorro': what the customer described."""
    pt = _lang(language) == "pt"
    request = found.request
    noun = _A_KIND[_lang(language)].get(request.transaction_type or "", _A_KIND[_lang(language)]["Purchase"])
    parts = [noun]
    if request.category:
        name = _CATEGORY[_lang(language)].get(request.category, request.category)
        parts.append(f"da categoria {name}" if pt else f"de la categoría {name}")
    if request.amount is not None:
        amount = _plain(request.amount) + (f" {request.currency.upper()}" if request.currency else "")
        parts.append(f"de aproximadamente {amount}" if request.approximate else f"de {amount}")
    if request.date_from is not None or request.date_to is not None:
        start = request.date_from or request.date_to
        end = request.date_to or request.date_from
        assert start is not None and end is not None
        if start == end:
            parts.append(f"em {human_date(start, language)}" if pt else f"del {human_date(start, language)}")
        elif pt:
            parts.append(f"entre {human_date(start, language)} e {human_date(end, language)}")
        else:
            parts.append(f"entre el {human_date(start, language)} y el {human_date(end, language)}")
    if request.merchant:
        parts.append(f"em {request.merchant}" if pt else f"en {request.merchant}")
    return " ".join(parts)


def _charge_money(txn: TransactionView) -> str:
    """The amount as the customer knows it: own currency first, with the USD figure the rest of the chat uses."""
    if txn.currency and txn.currency != "USD" and txn.amount is not None:
        return f"{txn.amount.quantize(Decimal('0.01'))} {txn.currency} ({human_amount(txn.amount_usd)})"
    return human_amount(txn.amount_usd)


def _charge_line(match: MatchedTransactionView, language: str) -> str:
    txn = match.transaction
    name = txn.merchant_name or transaction_noun(txn.transaction_type, language)
    when = human_date(txn.transaction_date.date(), language) if txn.transaction_date else "?"
    tags = [
        _FIT_TAGS[_lang(language)][(fit.criterion, fit.fit)]
        for fit in match.fits
        if (fit.criterion, fit.fit) in _FIT_TAGS[_lang(language)]
    ]
    suffix = f" ({', '.join(tags)})" if tags else ""
    return f"{name}, {_charge_money(txn)}, {when}{suffix}"


def near_match_candidates(found: FindTransactionsResult, language: str) -> str:
    """The numbered options, each saying what differs from what the customer described."""
    return "\n".join(f"{index}. {_charge_line(match, language)}" for index, match in enumerate(found.matches, start=1))


def near_match_reply(found: FindTransactionsResult, language: str) -> str:
    """No exact charge but a close one: what was not found, what was, how it differs. Never picks for the customer."""
    pt = _lang(language) == "pt"
    head = f"{'Não encontrei' if pt else 'No encontré'} {_requested(found, language)}."
    if len(found.matches) == 1:
        line = _charge_line(found.matches[0], language)
        if pt:
            return f"{head} Encontrei um parecido: {line}. É esse que você quer revisar? Responda sim ou não."
        return f"{head} Sí encontré uno parecido: {line}. ¿Es ese el que quieres revisar? Responde sí o no."
    options = near_match_candidates(found, language)
    if pt:
        return f"{head} Encontrei estes parecidos. Indique o número do que você quer revisar:\n{options}"
    return f"{head} Encontré estos parecidos. Indica el número del que quieres revisar:\n{options}"
