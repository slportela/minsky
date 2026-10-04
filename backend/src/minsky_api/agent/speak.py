"""One model call chooses the next conversational step and writes the customer text.

Code passes the allowed acts and the verified facts. An act outside that list, a reply that
drops a fact the customer must hear, or a completed-action sentence the facts do not support,
is refused before anything is sent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
        "dispute_opened",
        re.compile(
            rf"\b(?:abrí|abri|abrimos|registré|registrei|registramos|ingresé|ingresamos|creé|criei)\b.{{0,40}}\b{_CASE}\b"
            rf"|\b{_CASE}\b.{{0,40}}\b(?:abiert[oa]s?|abert[oa]s?|registrad[oa]s?|cread[oa]s?|criad[oa]s?"
            r"|ingresad[oa]s?|en trámite|en proceso|em andamento|em análise)\b"
            rf"|\b(?:se abrió|foi abert[oa]|se registró|foi registrad[oa])\b.{{0,30}}\b{_CASE}\b"
            rf"|\bnúmero de (?:{_CASE})\b"
        ),
    ),
    (
        # Refunds done or promised: the system never refunds. "Ese cargo ya fue revertido" (rule D02) states a
        # bank fact and stays out.
        "refund",
        re.compile(
            r"\b(?:reembolsé|reembolsei|reembolsamos|devolví|devolvi|devolvimos|estornamos|estornei)\b"
            r"|\b(?:te|le) (?:devolveremos|reembolsaremos|devolvemos|reembolsamos)\b"
            r"|\b(?:vamos a|vamos) (?:devolver|reembolsar|estornar)\b"
            r"|\b(?:recibirás|recuperarás|vas a recibir|vas a recuperar) (?:tu|el) dinero\b"
            r"|\b(?:vai receber|vai recuperar|receberá) (?:o|seu|o seu) dinheiro\b"
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
_TENSE_SENSITIVE = frozenset({"card_blocked", "dispute_opened"})
_NEGATION = re.compile(r"(?:no|não|nao|sin|sem|nunca)\s+$")
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


def ungrounded_number(text: str, facts: Mapping[str, object]) -> str | None:
    """The first number in the reply that no verified fact contains (an invented amount, date or deadline)."""
    known = _fact_numbers(facts)
    for token in _NUMBER.findall(text):
        if not _number_forms(token) & known:
            return token
    return None


def _unsupported_claim(text: str, facts: dict[str, object]) -> str | None:
    claims = action_claims(text)
    if "card_blocked" in claims and facts.get("card_blocked") is not True:
        return "card_blocked"
    # An open dispute is supported by the one just opened or by the one that already existed (rule D04).
    references = [facts.get("dispute_id"), facts.get("existing_dispute_id")]
    if "dispute_opened" in claims and not any(isinstance(ref, str) and ref and ref in text for ref in references):
        return "dispute_opened"
    handoff_id = facts.get("handoff_id")
    if "handoff" in claims and not (isinstance(handoff_id, str) and handoff_id in text):
        return "handoff"
    if "refund" in claims:
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
