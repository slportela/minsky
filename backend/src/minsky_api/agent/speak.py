"""One model call chooses the next conversational step and writes the customer text.

Code passes the allowed acts and the verified facts. An act outside that list, a reply that
drops a fact the customer must hear, or a completed-action sentence the facts do not support,
is refused before anything is sent.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from minsky_api.agent.prompts import render
from minsky_api.llm.client import LLM

# Completed actions only. Offers ("bloquee", "puedo bloquear", "derivo", "abrir") stay out of these
# patterns; a participle after a future or conditional ("quedará bloqueada") is an offer, not a claim.
_CARD = r"(?:tarjeta|tarjetas|cartão|cartao|cartões|cartoes)"
_CASE = r"(?:reclamo|reclamos|disputa|disputas|reclamação|reclamacao|contestação|contestacao|caso)"
_CLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "card_blocked",
        re.compile(
            r"\bbloque(?:é|ei|amos|aron|ou|aram)\b"
            rf"|\b{_CARD}\b.{{0,40}}\bbloquead[oa]s?\b"
            rf"|\bbloquead[oa]s?\b.{{0,20}}\b{_CARD}\b"
            r"|\bhemos bloqueado\b"
        ),
    ),
    (
        "dispute_opened",
        re.compile(
            rf"\b(?:abrí|abri|abrimos|registré|registrei|registramos)\b.{{0,40}}\b{_CASE}\b"
            rf"|\b{_CASE}\b.{{0,40}}\b(?:abiert[oa]s?|abert[oa]s?|registrad[oa]s?|creado|criad[oa])\b"
            rf"|\b(?:se abrió|foi abert[oa]|se registró|foi registrad[oa])\b.{{0,30}}\b{_CASE}\b"
        ),
    ),
    (
        # First-person completed refunds only. "Ese cargo ya fue revertido" (rule D02) states a bank fact.
        "refund",
        re.compile(r"\b(?:reembolsé|reembolsei|reembolsamos|devolví|devolvi|devolvimos|estornamos|estornei)\b"),
    ),
    (
        "handoff",
        re.compile(r"\b(?:derivé|encaminhei|transferí|transferi|pasé tu caso|passei o seu caso)\b"),
    ),
)
_NEGATION = re.compile(r"(?:no|não|nao|sin|sem|nunca)\s+$")
_FUTURE_WORDS = (
    r"(?:quedará|quedaría|será|sería|estará|estaría|ficará|ficaria|vai ficar|va a quedar|podemos|puedo|posso)"
)
_FUTURE = re.compile(_FUTURE_WORDS + r"\s+$")
_FUTURE_INSIDE = re.compile(rf"\b{_FUTURE_WORDS}\b")
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
            if _NEGATION.search(window) or _FUTURE.search(window) or _FUTURE_INSIDE.search(match.group(0)):
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


def _unsupported_claim(text: str, facts: dict[str, object]) -> str | None:
    claims = action_claims(text)
    if "card_blocked" in claims and facts.get("card_blocked") is not True:
        return "card_blocked"
    dispute_id = facts.get("dispute_id")
    if "dispute_opened" in claims and not (isinstance(dispute_id, str) and dispute_id in text):
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
    unsupported = _unsupported_claim(parsed.text, facts)
    if unsupported is not None:
        raise RuntimeError(f"compose_speech: unverified {unsupported}")
    if parsed.claims_card_blocked and facts.get("card_blocked") is not True:
        raise RuntimeError("compose_speech: unverified card block")
    return parsed
