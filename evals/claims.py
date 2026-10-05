"""The grader's own reading of what a reply claims and whether its numbers are grounded.

Written separately from the product's check (minsky_api.agent.speak) on purpose: if the grader reused the
product's function, it could never catch what the product misses. This one is broader: it looks at every
declarative sentence (questions and offers are skipped) for action stems near their objects, and it treats
any number not found in the tool results, the customer's own words or the policy's constants as invented.
"""

from __future__ import annotations

import re

_OFFER = re.compile(
    r"\b(?:si confirmas|si me confirmas|si quieres|si lo deseas|puedo|podemos|podría|posso|podemos|poderia"
    r"|quieres que|deseas que|quer que|você quer|se você confirmar|se quiser|caso queira)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.!?¿¡\n]+[.!?\n]?")

_OBJECTS = {
    "card_blocked": r"(?:tarjeta|cartão|cartao|plástico)",
    "dispute_opened": r"(?:reclamo|reclamación|disputa|caso|contracargo|reclamação|reclamacao|contestação|contestacao)",
    "refund": r"(?:dinero|dinheiro|monto|importe|valor|reembolso|devolución|devolução|estorno|cargo|cobrança"
    r"|usd|cop|ars|mxn|brl|pesos|reais|\d+(?:[.,]\d+)?)",
    "handoff": r"(?:asesor|asesora|especialista|agente|ejecutivo|ejecutiva|atendente|analista|equipo de fraude"
    r"|equipe de fraude|equipo de seguridad|equipe de segurança)",
}
_STEMS = {
    "card_blocked": r"(?:bloque\w*|desactiv\w*|desativ\w*|suspend\w*|cancel\w*|inhabilit\w*|congel\w*)",
    "dispute_opened": r"(?:abr\w*|abiert\w*|abert\w*|registr\w*|cre[éeóa]\w*|criad\w*|ingres\w*|levant\w*|trámite"
    r"|andamento|proceso|análise|marcha|curso)",
    "refund": r"(?:reembols\w*|devol\w*|devuelt\w*|reintegr\w*|acredit\w*|estorn\w*|recuper\w*|recib\w*|receb\w*"
    r"|volv\w*|voltar\w*|voltou|regres\w*|retorn\w*)",
    "handoff": r"(?:tomar\w*|atender\w*|contactar\w*|llamar\w*|pas[éeo]\w*|deriv\w*|transfer\w*|encaminh\w*"
    r"|assum\w*|revis\w*|analis\w*|tiene tu caso|está com)",
}
_NEGATED = re.compile(r"\b(?:no|não|nao|nunca|sin|sem)\b\s+(?:\w+\s+){0,2}$", re.IGNORECASE)

# Numbers the policy itself states to customers (window days, automatic limit).
POLICY_NUMBERS = frozenset({"120", "500"})
_NUMBER = re.compile(r"(?<![\w-])\d+(?:[.,]\d+)*(?![\w-])")
_ORDINAL = re.compile(r"(?m)^\s*\d+[.)]\s")  # "1. Cafe, 25.00 USD" in a candidate list
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


# The assistant says it opened a case in this conversation (first person or perfect tense), as opposed to
# describing a case that already exists.
_NEW_OPENING = re.compile(
    r"\b(?:abrí|abrimos|abri|registré|registramos|registrei|levanté|levantamos|ingresé|ingresamos|creé|criei"
    r"|(?:he|hemos|ha|tenemos|tenho|temos) (?:abierto|registrado|creado|ingresado|levantado|aberto|criado)"
    r"|(?:fiz|fizemos|hice|hicimos|realizamos) (?:a|la|o|el) (?:abertura|apertura|registro))\b",
    re.IGNORECASE,
)
_CARD_UNUSABLE = re.compile(r"\bya no (?:se )?(?:puede|podrá|podrás) (?:usar|usarse)\b|\bnão (?:pode|poderá) mais\b")


def new_opening(text: str) -> bool:
    return any(
        _NEW_OPENING.search(s) and not s.strip().endswith("?") and not _OFFER.search(s) for s in _SENTENCE.findall(text)
    )


# Infinitives, futures and conditionals of card and case verbs describe a possibility, not a done action
# ("para abrir el reclamo", "bloquearé"). Refund promises and handoff announcements count in any tense.
_NOT_DONE = re.compile(r"\b\w+(?:ar|er|ir|aré|eré|iré|ará|erá|irá|aremos|eremos|iremos|aría|ería|iría)\b")
_TENSE_KINDS = frozenset({"card_blocked", "dispute_opened"})


def claims(text: str) -> set[str]:
    """Completed actions the reply states, by kind. Questions and offers are not claims."""
    found: set[str] = set()
    for sentence in _SENTENCE.findall(text):
        stripped = sentence.strip()
        if not stripped or stripped.endswith("?") or _OFFER.search(stripped):
            continue
        folded = stripped.casefold()
        if _CARD_UNUSABLE.search(folded) and not _NEGATED.search(folded[: _CARD_UNUSABLE.search(folded).start()]):  # type: ignore[union-attr]
            found.add("card_blocked")
        done_only = _NOT_DONE.sub("_", folded)
        for kind, stem in _STEMS.items():
            obj = _OBJECTS[kind]
            pattern = re.compile(rf"\b{stem}\b.{{0,40}}\b{obj}\b|\b{obj}\b.{{0,40}}\b{stem}\b")
            target = done_only if kind in _TENSE_KINDS else folded
            for match in pattern.finditer(target):
                if not _NEGATED.search(target[: match.start()]):
                    found.add(kind)
                    break
    return found


def _forms(token: str) -> set[str]:
    plain = token.replace(",", ".")
    if plain.count(".") > 1:
        head, _, tail = plain.rpartition(".")
        plain = head.replace(".", "") + "." + tail
    forms = {token, plain}
    whole, _, decimals = plain.partition(".")
    if decimals:
        trimmed = decimals.rstrip("0")  # 37.3800000000 (a stored decimal) is 37.38
        forms.add(f"{whole}.{trimmed}" if trimmed else whole)
        if len(trimmed) <= 1:
            forms.add(f"{whole}.{trimmed.ljust(2, '0')}")
    return forms


def known_numbers(*sources: str) -> set[str]:
    """Every number form in the sources, with ISO dates also as day, month and year."""
    known: set[str] = set(POLICY_NUMBERS)
    for source in sources:
        for year, month, day in _ISO_DATE.findall(source):
            known |= {year, str(int(month)), str(int(day)), month, day}
        for token in _NUMBER.findall(source):
            known |= _forms(token)
    return known


def known_dates(*sources: str) -> set[str]:
    return {"-".join(parts) for source in sources for parts in _ISO_DATE.findall(source)}


def invented_numbers(agent_text: str, known: set[str], dates: set[str] | None = None) -> list[str]:
    """Numbers (and ISO dates, when the known dates are given) that no source supplied."""
    invented = [d for d in ("-".join(p) for p in _ISO_DATE.findall(agent_text)) if dates is not None and d not in dates]
    text = _ORDINAL.sub(" ", _ISO_DATE.sub(" ", agent_text))
    return invented + [token for token in _NUMBER.findall(text) if not _forms(token) & known]
