"""One model call chooses the next conversational step and writes the customer text.

Code passes the allowed acts and the verified facts. An act outside that list, a reply that
drops a fact the customer must hear, a completed-action sentence, or a confident reply in
the other language is refused before anything is sent. The sentence that reports a finished
action is rendered from the verified result, not by this call.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from minsky_api.agent.language import default_language_detector
from minsky_api.agent.prompts import render
from minsky_api.llm.client import LLM

# Completed actions only. Offers ("bloquee", "derivo", "abrir") stay out of these patterns.
# This is a backstop. The customer-facing action sentence is rendered from the verified result.
_CLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "card_blocked",
        re.compile(
            r"\bbloque(?:é|ei|amos|ámos)\b"
            r"|\bbloquead[oa]s?\b"
            r"|\b(?:tarjeta|cartão|cartao) bloquead"
        ),
    ),
    (
        "dispute_opened",
        re.compile(
            r"\b(?:abrí|abri)\b.{0,40}\b(?:reclamo|disputa|reclama)\b"
            r"|\babiert[oa]\b.{0,30}\b(?:reclamo|disputa)\b"
            r"|\b(?:reclamo|disputa|reclama)\b.{0,30}\babert"
        ),
    ),
    (
        "refund",
        re.compile(r"\breembols|\bdevolvido\b|\bdevolvida\b|\bdevolv(?:í|i|eu)\b"),
    ),
    (
        "handoff",
        re.compile(r"\bderivé\b|\bencaminhei\b"),
    ),
)
_NEGATION = re.compile(r"(?:no|não|nao|sin|sem)\s+$")
_ID_FACTS = ("transaction_id", "dispute_id", "handoff_id", "rule_id", "existing_dispute_id")
_ACT_FACTS: dict[str, tuple[str, ...]] = {
    "clarify": ("candidates",),
    "confirm_txn": ("merchant", "amount_usd", "when"),
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


def action_claims(text: str) -> frozenset[str]:
    """Completed-action claims in the sentence. A nearby negation drops that match."""
    folded = text.casefold()
    found: set[str] = set()
    for name, pattern in _CLAIM_PATTERNS:
        for match in pattern.finditer(folded):
            window = folded[max(0, match.start() - 16) : match.start()]
            if _NEGATION.search(window):
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


def _unsupported_claim(text: str) -> str | None:
    """The model does not report a finished action. Code renders that sentence."""
    claims = action_claims(text)
    for name in ("card_blocked", "dispute_opened", "refund", "handoff"):
        if name in claims:
            return name
    return None


async def compose_speech(
    llm: LLM,
    *,
    language: str,
    allowed: tuple[str, ...],
    facts: dict[str, object],
) -> Speech:
    """The model's act and wording.

    Raises when the act is not allowed, a required fact is missing from the text, the text
    reports a finished action, or lingua is confident the sentence is in the other language.
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
    unsupported = _unsupported_claim(parsed.text)
    if unsupported is not None:
        raise RuntimeError(f"compose_speech: unverified {unsupported}")
    return parsed
