"""One model call chooses the next conversational step and writes the customer text.

Code passes the allowed acts and the verified facts. An act outside that list, a reply that
drops a fact the customer must hear, or a completed-action sentence the facts do not support,
or a number the facts do not carry (a fee, a date, a deadline), is refused before anything is sent.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from minsky_api.agent.prompts import render
from minsky_api.llm.client import LLM

# What the reply may not claim without the matching fact. Offers ("puedo bloquear", "¿abro el reclamo?"),
# questions and negations stay out; a participle after a future ("quedará bloqueada") is an offer, not a claim.
# Five kinds: dispute_opened (the agent just opened one), dispute_state (a case is open or in progress),
# card_blocked, refund (done or promised), handoff (done or promised). Spanish and Portuguese.
_CARD = r"(?:tarjeta|tarjetas|cartão|cartao|cartões|cartoes)"
_CASE = (
    r"(?:reclamo|reclamos|disputa|disputas|reclamación|reclamacion|solicitud|caso|"
    r"reclamação|reclamacao|contestação|contestacao|solicitação|solicitacao)"
)
_OPEN_PARTICIPLES = r"(?:abiert[oa]s?|registrad[oa]s?|cread[oa]s?|iniciad[oa]s?|generad[oa]s?|abert[oa]s?|criad[oa]s?)"
_STOPPED = (
    r"(?:bloquead[oa]s?|inhabilitad[oa]s?|desactivad[oa]s?|suspendid[oa]s?|congelad[oa]s?|cancelad[oa]s?|"
    r"desativad[oa]s?|suspens[oa]s?)"
)
_PERSON = r"(?:asesor|asesora|especialista|agente|ejecutivo|ejecutiva|persona|equipo|atendente|consultor|equipe)"

# (kind, pattern, promise). A promise states an outcome the system does not decide (a refund, a call back), so
# the "future" exemption does not apply to it.
_CLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str], bool], ...] = tuple(
    (kind, re.compile(pattern), promise)
    for kind, pattern, promise in (
        (
            "dispute_opened",
            r"\b(?:abrí|abri|abrimos|registré|registre|registramos|registrei|creé|crie|criei|criamos|"
            r"inicié|iniciei|iniciamos|generé|gerei)\b.{0,40}\b" + _CASE + r"\b"
            r"|\b(?:he|hemos)\s+(?:abierto|registrado|creado|iniciado|generado)\b.{0,40}\b" + _CASE + r"\b"
            r"|\b(?:fiz|fizemos)\s+(?:a\s+)?(?:abertura|o\s+registro)\b"
            r"|\b(?:se abrió|foi abert[oa]|se registró|foi registrad[oa])\b.{0,30}\b" + _CASE + r"\b"
            r"|\b"
            + _CASE
            + r"\b.{0,30}\b(?:fue|foi|ha sido|quedó|quedo|ha quedado|ficou)\s+(?:ya\s+)?"
            + _OPEN_PARTICIPLES
            + r"\b"
            r"|\b(?:quedó|quedo|ha quedado|ficou)\b.{0,20}\b" + _OPEN_PARTICIPLES + r"\b.{0,30}\b" + _CASE + r"\b",
            False,
        ),
        (
            "dispute_state",
            r"\b" + _CASE + r"\b.{0,40}\b(?:" + _OPEN_PARTICIPLES[3:-1] + r"|em andamento|en marcha|en curso|"
            r"en tr[aá]mite|en proceso)\b",
            False,
        ),
        (
            "card_blocked",
            r"\bbloque(?:é|ei|amos|aron|ou|aram)\b"
            r"|\b(?:he|hemos)\s+bloqueado\b"
            r"|\b" + _CARD + r"\b.{0,40}\b" + _STOPPED + r"\b"
            r"|\b" + _STOPPED + r"\b.{0,20}\b" + _CARD + r"\b"
            r"|\b" + _CARD + r"\b.{0,30}\bya no (?:se puede|podrás|puede|funciona)\b",
            False,
        ),
        (
            "refund",
            r"\b(?:reembolsé|reembolsamos|reembolsei|devolví|devolvi|devolvimos|acredité|acreditamos|"
            r"estornei|estornamos)\b"
            r"|\b(?:he|hemos)\s+(?:reembolsado|devuelto|acreditado)\b",
            False,
        ),
        (
            "refund",
            r"\b(?:devolveremos|reembolsaremos|acreditaremos|estornaremos)\b"
            r"|\b(?:el dinero|tu dinero|el importe|el monto|o dinheiro|o valor)\b.{0,30}\b(?:volverá|regresará|"
            r"será devuelto|será reembolsado|será acreditado|voltará|será devolvido|será estornado)\b"
            r"|\brecibirás\s+(?:tu|el)\s+(?:reembolso|dinero)\b",
            True,
        ),
        (
            "handoff",
            r"\b(?:derivé|derivamos|transferí|transferi|encaminhei|encaminhamos|pasé tu caso|passei o seu caso)\b"
            r"|\b(?:he|hemos)\s+(?:derivado|pasado|transferido|escalado)\b.{0,30}\b" + _CASE + r"\b",
            False,
        ),
        (
            "handoff",
            r"\b" + _PERSON + r"\b.{0,40}\b(?:te contactará|te llamará|te escribirá|se pondrá en contacto|"
            r"te atenderá|te contactarán|se comunicará|entrará em contato|vai entrar em contato|ligará|vai ligar)\b",
            True,
        ),
    )
)
_NEGATION_INSIDE = re.compile(r"(?<!ya )\b(?:no|não|nao|nunca|sin|sem)\b")
_NEGATION = re.compile(r"(?:no|não|nao|sin|sem|nunca|ning[uú]n|ninguna|nenhum|nenhuma)\s+$")
_FUTURE_WORDS = (
    r"(?:quedará|quedaría|será|sería|estará|estaría|ficará|ficaria|vai ficar|va a quedar|podemos|puedo|posso)"
)
_FUTURE = re.compile(_FUTURE_WORDS + r"\s+$")
_FUTURE_INSIDE = re.compile(rf"\b{_FUTURE_WORDS}\b")
_RULE_ID = re.compile(r"\bD0\d\b")  # rule ids stay internal: the customer hears the reason instead
# References the customer needs to keep. Rule ids stay internal: the customer hears the reason instead.
_ID_FACTS = ("dispute_id", "handoff_id", "existing_dispute_id")
_ACT_FACTS: dict[str, tuple[str, ...]] = {
    "clarify": ("candidates",),
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
    """Completed or promised actions in the text. A nearby negation drops that match."""
    folded = text.casefold()
    found: set[str] = set()
    for kind, pattern, promise in _CLAIM_PATTERNS:
        for match in pattern.finditer(folded):
            window = folded[max(0, match.start() - 24) : match.start()]
            if _NEGATION.search(window) or _NEGATION_INSIDE.search(match.group(0)):
                continue
            if not promise and (_FUTURE.search(window) or _FUTURE_INSIDE.search(match.group(0))):
                continue
            found.add(kind)
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


def _has(text: str, value: object) -> bool:
    return isinstance(value, str) and bool(value) and value in text


def _unsupported_claim(text: str, facts: dict[str, object]) -> str | None:
    # The policy reason is a verified fact written by code: a claim it already makes needs no further support.
    claims = action_claims(text) - action_claims(str(facts.get("reason") or ""))
    if "card_blocked" in claims and facts.get("card_blocked") is not True:
        return "card_blocked"
    if "dispute_opened" in claims and not _has(text, facts.get("dispute_id")):
        return "dispute_opened"
    # A case that is open or in progress is described with the reference the customer holds: the one just
    # opened (dispute_id) or the one that already existed (existing_dispute_id, rule D04).
    if "dispute_state" in claims and not (
        _has(text, facts.get("dispute_id")) or _has(text, facts.get("existing_dispute_id"))
    ):
        return "dispute_state"
    if "handoff" in claims and not _has(text, facts.get("handoff_id")):
        return "handoff"
    if "refund" in claims:
        return "refund"
    return None


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_LIST_MARKER = re.compile(r"(?m)(?:^|(?<=\s))\d{1,2}[.)](?=\s)")


def _numbers(text: str) -> set[str]:
    """Comparable forms of the numbers in a text: grouping removed, decimal comma read as a point."""
    found: set[str] = set()
    for token in _NUMBER.findall(text):
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", token):
            token = token.replace(",", "")
        elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", token):
            token = token.replace(".", "").replace(",", ".")
        parts = [token.replace(",", ".")]
        try:
            Decimal(parts[0])
        except InvalidOperation:  # 10.06.2026: read it as its parts
            parts = re.split(r"[.,]", token)
        for part in parts:
            found.add(format(Decimal(part).normalize(), "f"))
    return found


def _ungrounded_figure(text: str, facts: dict[str, object]) -> str | None:
    """A number in the reply that no fact carries: an invented fee, date, deadline or amount."""
    allowed = _numbers(json.dumps(facts, ensure_ascii=False, default=str))
    extra = _numbers(_LIST_MARKER.sub(" ", text)) - allowed
    return sorted(extra)[0] if extra else None


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
    dropped = _dropped_fact(parsed.act, parsed.text, facts)
    if dropped is not None:
        raise RuntimeError(f"compose_speech: reply drops {dropped}")
    if _RULE_ID.search(parsed.text):
        raise RuntimeError("compose_speech: reply exposes an internal rule id")
    unsupported = _unsupported_claim(parsed.text, facts)
    if unsupported is not None:
        raise RuntimeError(f"compose_speech: unverified {unsupported}")
    figure = _ungrounded_figure(parsed.text, facts)
    if figure is not None:
        raise RuntimeError(f"compose_speech: reply states a figure the facts do not support: {figure}")
    if parsed.claims_card_blocked and facts.get("card_blocked") is not True:
        raise RuntimeError("compose_speech: unverified card block")
    return parsed
