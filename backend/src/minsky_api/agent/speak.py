"""One model call chooses the next conversational step and writes the customer text.

Code passes the allowed acts and the verified facts. An act outside that list, a reply that
drops a fact the customer must hear, a completed-action sentence the facts do not support, or a
confident reply in the other language, is refused before anything is sent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from minsky_api.agent.language import default_language_detector
from minsky_api.agent.prompts import render
from minsky_api.llm.client import LLM

# Completed actions only. Offers ("bloquee", "puedo bloquear", "derivo", "abrir") stay out of these
# patterns; a participle after a future or conditional ("quedará bloqueada") is an offer, not a claim.
_CARD = r"(?:tarjeta|tarjetas|cartão|cartao|cartões|cartoes)"
_CASE = r"(?:reclamo|reclamos|disputa|disputas|reclamação|reclamacao|contestação|contestacao|caso)"
_STAFF = r"(?:especialista|asesor|asesora|agente|ejecutivo|ejecutiva|atendente|analista)"
_CLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "card_blocked",
        re.compile(
            r"\bbloque(?:é|ei|amos|aron|ou|aram)\b"
            rf"|\b{_CARD}\b.{{0,40}}\b(?:bloquead[oa]s?|desactivad[oa]s?|desativad[oa]s?|suspendid[oa]s?"
            r"|cancelad[oa]s?|inhabilitad[oa]s?)\b"
            rf"|\b(?:bloquead[oa]s?|desactivad[oa]s?|desativad[oa]s?)\b.{{0,20}}\b{_CARD}\b"
            r"|\bhemos bloqueado\b"
            r"|\bya no (?:podrá|puede|se puede) (?:usar|usarse)\b|\bnão (?:pode|poderá) mais ser usad[oa]\b"
        ),
    ),
    (
        # The assistant says it opened a case now: only the dispute just written supports it.
        "dispute_opened",
        re.compile(
            rf"\b(?:abrí|abri|abrimos|registré|registrei|registramos|ingresé|ingresamos|creé|criei|levanté"
            rf"|levantamos)\b.{{0,40}}\b{_CASE}\b"
            rf"|\b(?:he|hemos|ha|han|tenemos|tenho|temos)\s+(?:abierto|registrado|creado|ingresado|levantado|iniciado"
            rf"|presentado|aberto|criado)\b.{{0,40}}\b{_CASE}\b"
            rf"|\b(?:fiz|fizemos|realizei|realizamos|hice|hicimos)\s+(?:a|la|el|o)\s+(?:abertura|apertura|registro)\b"
            rf"|\b(?:se abrió|se registró|foi abert[oa]|foi registrad[oa])\b.{{0,30}}\b{_CASE}\b"
        ),
    ),
    (
        # A case is open or in progress (a status): the dispute just opened or the one that existed (D04) supports it.
        "dispute_exists",
        re.compile(
            rf"\b{_CASE}\b.{{0,40}}\b(?:abiert[oa]s?|abert[oa]s?|registrad[oa]s?|cread[oa]s?|criad[oa]s?"
            r"|ingresad[oa]s?|en trámite|en proceso|en marcha|en curso|em andamento|em análise"
            r"|siendo (?:revisad[oa]|analizad[oa]))\b"
            rf"|\b(?:quedó|quedaron|está|están|fue|foi|ficou)\s+(?:registrad\w*|abiert\w*|abert\w*|cread\w*|ingresad\w*)"
            rf"\b.{{0,30}}\b{_CASE}\b"
            rf"|\bnúmero de (?:{_CASE})\b"
        ),
    ),
    (
        # Money returned or promised. The system never refunds, so this is only true when the bank already
        # reversed the charge (rule D02, fact charge_reversed).
        "refund",
        re.compile(
            r"\b(?:reembolsé|reembolsei|reembolsamos|devolví|devolvi|devolvimos|estornamos|estornei|reintegramos)\b"
            r"|\b(?:he|hemos|ha|han|tenemos|temos)\s+(?:devuelto|reembolsado|reintegrado|acreditado|abonado|estornado"
            r"|devolvido|reembolsado)\b"
            r"|\b(?:te|le) (?:devolveremos|reembolsaremos|devolvemos|reembolsamos|acreditaremos|reintegraremos)\b"
            r"|\b(?:vamos a|vamos) (?:devolver|reembolsar|estornar|reintegrar)\b"
            r"|\b(?:recibirás|recuperarás|vas a recibir|vas a recuperar) (?:tu|el) dinero\b"
            r"|\b(?:vai receber|vai recuperar|receberá) (?:o|seu|o seu) dinheiro\b"
            r"|\b(?:dinero|dinheiro|monto|importe|valor)\b.{0,30}\b(?:volverá|volvió|regresará|vuelve|voltará|voltou"
            r"|retornará|será devuelto|será reembolsado|será estornado)\b"
            r"|\b(?:valor|cobrança|cargo|monto|importe|dinero|dinheiro|pago|pagamento)\b.{0,25}"
            r"\b(?:foi|fue|ha sido|será|serão|serán)\s+"
            r"(?:estornad|revertid|reembolsad|devuelt|devolvid|creditad|acreditad|reintegrad)\w*"
        ),
    ),
    (
        "handoff",
        re.compile(
            r"\b(?:derivé|encaminhei|transferí|transferi|pasé tu caso|passei o seu caso)\b"
            r"|\b(?:te paso con|te comunico con|te transfiero|te derivo|vou te transferir|vou transferir|encaminho)\b"
            rf"|\b{_STAFF}\b.{{0,40}}\b(?:tomará|atenderá|contactará|te llamará|te escribirá|vai assumir"
            r"|vai atender|entrará em contato|assumirá|ya tiene tu caso|já está com o seu caso)\b"
        ),
    ),
)
# Offers stay out of the card and dispute claims ("si confirmas, quedará bloqueada"); refund promises and
# handoff announcements are claims in any tense.
_TENSE_SENSITIVE = frozenset({"card_blocked", "dispute_opened", "dispute_exists"})
_NEGATION = re.compile(r"(?:no|não|nao|sin|sem|nunca)\s+$")
_FUTURE_WORDS = (
    r"(?:quedará|quedaría|será|sería|estará|estaría|ficará|ficaria|vai ficar|va a quedar|podemos|puedo|posso)"
)
_FUTURE = re.compile(_FUTURE_WORDS + r"\s+$")
_FUTURE_INSIDE = re.compile(rf"\b{_FUTURE_WORDS}\b")
_RULE_ID = re.compile(r"\bD0\d\b")  # rule ids stay internal: the customer hears the reason instead
# References the customer needs to keep. Rule ids stay internal: the customer hears the reason instead.
_ID_FACTS = ("dispute_id", "handoff_id", "existing_dispute_id")
# `clarify` is absent on purpose: the option list is written by code and appended (wording.with_candidates), so a
# model that punctuates the list its own way is not refused. Demanding a verbatim copy rejected correct replies.
_ACT_FACTS: dict[str, tuple[str, ...]] = {
    "confirm_txn": ("merchant", "amount", "when"),
}

Act = Literal[
    "clarify",
    "confirm_txn",
    "confirm_open",
    "offer_block",
    "inform",
    "refuse",
    "handoff",
    "abort",
    "ask_again",
]


class Speech(BaseModel):
    model_config = ConfigDict(extra="forbid")

    act: Act
    text: str = Field(min_length=1)
    claims_card_blocked: bool = False


def action_claims(text: str) -> frozenset[str]:
    """Completed-action claims in the sentence. A nearby negation drops that match."""
    folded = text.casefold()
    found: set[str] = set()
    for name, pattern in _CLAIM_PATTERNS:
        for match in pattern.finditer(folded):
            window = folded[max(0, match.start() - 24) : match.start()]
            if _NEGATION.search(window):
                continue
            if name in _TENSE_SENSITIVE and (_FUTURE.search(window) or _FUTURE_INSIDE.search(match.group(0))):
                continue
            found.add(name)
    return frozenset(found)


def _dropped_fact(act: str, text: str, facts: dict[str, object]) -> str | None:
    keys = _ID_FACTS + _ACT_FACTS.get(act, ())
    for key in keys:
        value = facts.get(key)
        if not isinstance(value, str) or not value:
            continue
        if value not in text:
            return key
    return None


# Numbers that stand alone: amounts, days, years, counts. Digits inside references (DSP-4f..., HO-9c...) are not.
_NUMBER = re.compile(r"(?<![\w-])\d+(?:[.,]\d+)*(?![\w-])")


def _number_forms(token: str) -> set[str]:
    """25.00, 25,00 and 25 are the same amount; 1.249,00 and 1,249.00 too."""
    forms = {token}
    plain = token.replace(",", ".")
    if plain.count(".") > 1:  # thousands separators
        head, _, tail = plain.rpartition(".")
        plain = head.replace(".", "") + "." + tail
    forms.add(plain)
    if "." in plain:
        whole, _, decimals = plain.partition(".")
        trimmed = decimals.rstrip("0")  # 37.3800000000 (a stored decimal) is 37.38
        forms.add(f"{whole}.{trimmed}" if trimmed else whole)
        if len(trimmed) <= 1:
            forms.add(f"{whole}.{trimmed.ljust(2, '0')}")
    return forms


def _fact_numbers(value: object) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_fact_numbers(v) for v in value.values())) if value else set()
    if isinstance(value, list | tuple):
        return set().union(*(_fact_numbers(v) for v in value)) if value else set()
    if isinstance(value, bool) or value is None:
        return set()
    return set().union(*(_number_forms(t) for t in _NUMBER.findall(str(value)))) if str(value) else set()


_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_MONTHS = {
    name: index
    for index, names in enumerate(
        (
            ("enero", "janeiro"), ("febrero", "fevereiro"), ("marzo", "março"), ("abril",), ("mayo", "maio"),
            ("junio", "junho"), ("julio", "julho"), ("agosto",), ("septiembre", "setembro"), ("octubre", "outubro"),
            ("noviembre", "novembro"), ("diciembre", "dezembro"),
        ),
        start=1,
    )
    for name in names
}  # fmt: skip
_HUMAN_DATE = re.compile(r"\b(\d{1,2}) de (\w+) de (\d{4})\b")


def _fact_dates(facts: Mapping[str, object]) -> set[str]:
    """ISO dates the facts state, directly or as '10 de junio de 2026'."""
    text = json.dumps(dict(facts), ensure_ascii=False, default=str)
    dates = set(_ISO_DATE.findall(text))
    for day, month, year in _HUMAN_DATE.findall(text):
        if month.casefold() in _MONTHS:
            dates.add(f"{year}-{_MONTHS[month.casefold()]:02d}-{int(day):02d}")
    return dates


def ungrounded_number(text: str, facts: Mapping[str, object]) -> str | None:
    """The first number or ISO date in the reply that no verified fact contains (an invented amount or deadline)."""
    known_dates = _fact_dates(facts)
    for iso in _ISO_DATE.findall(text):
        if iso not in known_dates:
            return iso
    known = _fact_numbers(facts)
    for token in _NUMBER.findall(text):
        if not _number_forms(token) & known:
            return token
    return None


def _unsupported_claim(text: str, facts: dict[str, object]) -> str | None:
    claims = action_claims(text)
    if "card_blocked" in claims and facts.get("card_blocked") is not True:
        return "card_blocked"
    # "I opened it" needs the dispute just written; "it is open" may cite the one that already existed (D04).
    opened = facts.get("dispute_id")
    existing = facts.get("existing_dispute_id")
    if "dispute_opened" in claims and not (isinstance(opened, str) and opened and opened in text):
        return "dispute_opened"
    if "dispute_exists" in claims and not any(
        isinstance(ref, str) and ref and ref in text for ref in (opened, existing)
    ):
        return "dispute_exists"
    handoff_id = facts.get("handoff_id")
    if "handoff" in claims and not (isinstance(handoff_id, str) and handoff_id in text):
        return "handoff"
    if "refund" in claims and facts.get("charge_reversed") is not True:
        return "refund"
    return None


async def compose_speech(
    llm: LLM,
    *,
    language: str,
    allowed: tuple[str, ...],
    facts: dict[str, object],
) -> Speech:
    """The model's act and wording.

    Raises when the act is not allowed, a required fact is missing from the text, or the text
    reports a block, an open, a refund, or a handoff the facts do not support.
    """
    if not allowed:
        raise RuntimeError("compose_speech: no allowed act")
    payload = json.dumps(
        {"language": language, "allowed": list(allowed), "facts": facts},
        ensure_ascii=False,
        default=str,
        sort_keys=True,
    )
    result = await llm.respond(
        render("agent.speak.j2"),
        [{"role": "user", "content": payload}],
        schema=Speech,
        reasoning_effort="low",
        max_output_tokens=400,
    )
    parsed = result.parsed
    if not isinstance(parsed, Speech):
        raise RuntimeError("compose_speech: model returned no parsed schema")
    if parsed.act not in allowed:
        raise RuntimeError(f"compose_speech: act {parsed.act} is not allowed")
    detected = default_language_detector().recognize(parsed.text)
    if detected is not None and detected != language:
        raise RuntimeError(f"compose_speech: reply language is {detected}")
    dropped = _dropped_fact(parsed.act, parsed.text, facts)
    if dropped is not None:
        raise RuntimeError(f"compose_speech: reply drops {dropped}")
    if _RULE_ID.search(parsed.text):
        raise RuntimeError("compose_speech: reply exposes an internal rule id")
    invented = ungrounded_number(parsed.text, facts)
    if invented is not None:
        raise RuntimeError(f"compose_speech: ungrounded number {invented}")
    unsupported = _unsupported_claim(parsed.text, facts)
    if unsupported is not None:
        raise RuntimeError(f"compose_speech: unverified {unsupported}")
    if parsed.claims_card_blocked and facts.get("card_blocked") is not True:
        raise RuntimeError("compose_speech: unverified card block")
    return parsed
