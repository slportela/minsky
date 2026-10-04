"""A second, independent reading of what a reply claims to have done, for the graders.

The reply guard (`minsky_api.agent.speak`) refuses unsupported claims before a reply is sent. If the grader
imported that same function, a phrasing it misses would be invisible to the eval too, and the eval could not
catch the guard's blind spots. So this module shares no code with it and reads differently: it splits the reply
into sentences and flags a sentence that states a completed or promised action, instead of looking for a verb
near an object inside a character window. It is deliberately stricter: it ignores only questions, offers,
negations and future tenses, and the graders accept a claim only when the matching record or reference exists.

Kinds: dispute_opened, card_blocked, handoff, refund. Spanish and Portuguese.
"""

from __future__ import annotations

import re

_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")

# A sentence that asks, offers or denies does not claim a completed action.
# "ya no" ("no longer") is a state, not a denial.
_NOT_A_CLAIM = re.compile(
    r"[¿?]"
    r"|(?<!ya )\b(?:no|não|nao|nunca|sin|sem|ning[uú]n\w*|nenhum\w*)\b"
    r"|\b(?:puedo|podemos|podrías|posso|quieres|quiere|deseas|desea|gostaria|quer)\b"
    r"|\b(?:si|se)\s+(?:confirmas|confirma|me confirmas|você confirmar|confirmar)\b"
    r"|\b(?:cuando|quando)\b",
    re.IGNORECASE,
)
# A future tense turns "I opened it" into an offer, but it does not turn "we will refund you" or "an advisor will
# call you" into one: those are promises of outcomes the system does not decide.
_FUTURE = re.compile(
    r"\b\w+(?:aré|eré|iré|arei|erei|irei|aremos|eremos|iremos|ará|erá|irá|arão|erão|irão)\b", re.IGNORECASE
)

_CASE = r"(?:reclam|disputa|contesta|solicitud|solicita|caso)"
_CARD = r"(?:tarjeta|cartão|cartao|cart[oõ]es)"
_PERSON = r"(?:asesor|especialista|agente|atendente|ejecutiv|persona|equipo|equipe|consultor)"

_OPENED = re.compile(
    rf"(?=.*\b{_CASE}\w*)(?=.*\b(?:abr[ií]\w*|abiert\w*|abert\w*|registr\w*|cre[eé]\w*|cread\w*|crie\w*|criad\w*|"
    r"inici[eéoó]\w*|iniciad\w*|gener[eé]\w*|gerei|fiz|fizemos|quedó|quedo|ficou|en marcha|en curso|en tr[aá]mite|"
    r"en proceso|em andamento)\b)",
    re.IGNORECASE,
)
_CARD_BLOCKED = re.compile(
    rf"(?=.*\b{_CARD}\b)(?=.*\b(?:bloque(?:é|ei|amos|ad\w*)|inhabilit\w*|desactiv\w*|desativ\w*|suspendid\w*|"
    r"congelad\w*|cancelad\w*)\b)"
    r"|\bya no (?:se puede|podrás|puede|funciona)\b.*\b" + _CARD + r"\b"
    r"|\b" + _CARD + r"\b.*\bya no (?:se puede|podrás|puede|funciona)\b",
    re.IGNORECASE,
)
_HANDOFF = re.compile(
    r"\b(?:deriv(?:é|amos|ad\w*)|encaminh\w*|transfer(?:í|imos|id\w*)|pas(?:é|amos|ad\w*) (?:tu|su|el)|passei)\b"
    rf"|\b{_PERSON}\w*\b.*\b(?:contact\w*|llam\w*|escrib\w*|comunic\w*|contato|ligar|ligará)\b",
    re.IGNORECASE,
)
# Only what the agent says it did or will do. A bank-side fact such as "essa cobrança já foi estornada e o dinheiro
# voltou" (the D02 reason) is not a claim; "te devolví el dinero" and "el dinero volverá a tu cuenta" are.
_REFUND = re.compile(
    r"\b(?:reembols(?:é|ei|amos)|devolv(?:í|i|imos|emos)|acredit(?:é|ei|amos)|estorn(?:ei|amos)|"
    r"(?:he|hemos|te he|te hemos)\s+(?:reembolsado|devuelto|acreditado|estornado))\b"
    r"|\b(?:devolveremos|reembolsaremos|acreditaremos|estornaremos|volverá|regresará|voltará|recibirás|"
    r"será (?:devuelto|reembolsado|acreditado|devolvido|estornado))\b",
    re.IGNORECASE,
)


def claims_in(text: str) -> frozenset[str]:
    """The completed or promised actions a reply states. Questions, offers and negations are ignored."""
    found: set[str] = set()
    for sentence in _SENTENCES.split(text):
        if not sentence.strip() or _NOT_A_CLAIM.search(sentence):
            continue
        future = _FUTURE.search(sentence) is not None
        for kind, pattern, promise in (
            ("dispute_opened", _OPENED, False),
            ("card_blocked", _CARD_BLOCKED, False),
            ("handoff", _HANDOFF, True),
            ("refund", _REFUND, True),
        ):
            if pattern.search(sentence) and (promise or not future):
                found.add(kind)
    return frozenset(found)
