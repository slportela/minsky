"""Flexible transaction matching: how close is a charge to what the customer described.

Pure functions over rows the store already scoped to one customer: no I/O, no model calls (AGENTS rule 1).
The model only extracts what the customer said; this module decides what counts as "the same charge".

Each thing the customer described (amount, date, merchant, kind, category) is graded against a charge as
`exact`, `near` or `miss`. The result is the first tier that has any charge, never a mix:

  exact    every described thing matches exactly (what the system always did)
  near     nothing misses, something is close: 123 for 123.10, "yesterday" for today, "starbuks"
  closest  exactly one described thing misses and the others hold: the charge exists, one detail differs
  none     nothing to propose

So an exact charge is never shown next to a near one. A near or closest charge is only a proposal: the
orchestrator says what differs and the customer must confirm it, and the policy still decides on the
charge's real facts. Kind and category only narrow a search: without an amount, a date or a merchant there
is nothing to propose (the customer's whole history of purchases is not a candidate list).

The thresholds live in `SearchConfig`; changing one needs an eval delta (docs/evals.md).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Literal

from minsky_api.store.models import Transaction

Criterion = Literal["amount", "date", "merchant", "kind", "category"]
# What can identify a charge by itself. Kind and category are weak: they refine, they never find.
STRONG: frozenset[Criterion] = frozenset({"amount", "date", "merchant"})

_CENT = Decimal("0.01")
_HALF_CENT = Decimal("0.005")


class Fit(StrEnum):
    EXACT = "exact"
    NEAR = "near"
    MISS = "miss"


class Tier(StrEnum):
    EXACT = "exact"
    NEAR = "near"
    CLOSEST = "closest"
    NONE = "none"


@dataclass(frozen=True)
class SearchConfig:
    amount_near_pct: Decimal = Decimal("0.01")  # 123 vs 123.10 is 0.08 %
    amount_near_floor: Decimal = Decimal("0.10")  # so a small amount still tolerates a few cents
    amount_approx_pct: Decimal = Decimal("0.10")  # when the customer said "about", "more or less"
    amount_approx_floor: Decimal = Decimal("1.00")
    date_slack_days: int = 1  # "yesterday" may be posted today
    merchant_similarity: float = 0.8  # difflib ratio; "starbuks" vs "Starbucks" is 0.94
    merchant_min_chars: int = 4  # a shorter name must match exactly: "uno" is not "Uber"
    max_exact: int = 10
    max_near: int = 5
    max_closest: int = 3


DEFAULT_CONFIG = SearchConfig()


@dataclass(frozen=True)
class SearchRequest:
    """What the customer described. Anything left None was not said."""

    amount: Decimal | None = None
    currency: str | None = None  # stated by the customer; None = unknown, either column may match
    approximate: bool = False
    date_from: date | None = None
    date_to: date | None = None
    merchant: str | None = None
    transaction_type: str | None = None
    category: str | None = None

    @property
    def date_range(self) -> tuple[date, date] | None:
        if self.date_from is None and self.date_to is None:
            return None
        start = self.date_from or self.date_to
        end = self.date_to or self.date_from
        assert start is not None and end is not None
        return start, end

    @property
    def criteria(self) -> frozenset[Criterion]:
        found: set[Criterion] = set()
        if self.amount is not None:
            found.add("amount")
        if self.date_range is not None:
            found.add("date")
        if self.merchant:
            found.add("merchant")
        if self.transaction_type:
            found.add("kind")
        if self.category:
            found.add("category")
        return frozenset(found)


@dataclass(frozen=True)
class CriterionFit:
    criterion: Criterion
    fit: Fit
    found: str | None  # the charge's own value for this criterion, as text (amount with its currency, ISO date, ...)
    distance: float = 0.0  # 0 exact; grows with how far off (relative amount, days, 1 - similarity)


@dataclass(frozen=True)
class Match:
    transaction: Transaction
    fits: tuple[CriterionFit, ...]

    @property
    def distance(self) -> float:
        return sum(fit.distance for fit in self.fits)


@dataclass(frozen=True)
class MatchResult:
    tier: Tier
    matches: tuple[Match, ...]


def _fold(text: str) -> str:
    """Lowercase, no accents, no punctuation: 'Clínica  Médica' and 'clinica medica' are the same name."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", plain).strip()


def _money(value: Decimal, currency: str | None) -> str:
    return f"{value.quantize(_CENT)} {currency}".strip()


def _amount_fit(txn: Transaction, request: SearchRequest, config: SearchConfig) -> CriterionFit:
    asked = request.amount
    assert asked is not None
    stated = request.currency.upper() if request.currency else None
    options: list[tuple[Decimal, str | None]] = []
    # The customer sees their own currency, but replies speak in USD: with no currency stated, either may be meant.
    if stated is None or stated == (txn.currency or "").upper():
        if txn.amount is not None:
            options.append((txn.amount, txn.currency))
    if stated is None or stated == "USD":
        options.append((txn.amount_usd, "USD"))
    if not options:
        # Said in a currency this charge does not carry: it cannot be compared, only shown.
        return CriterionFit("amount", Fit.MISS, _money(txn.amount_usd, "USD"), 1.0)
    value, label = min(options, key=lambda option: abs(option[0] - asked))
    diff = abs(value - asked)
    found = _money(value, label)
    relative = float(diff / asked) if asked > 0 else float(diff)
    if diff < _HALF_CENT:
        return CriterionFit("amount", Fit.EXACT, found)
    pct, floor = (
        (config.amount_approx_pct, config.amount_approx_floor)
        if request.approximate
        else (config.amount_near_pct, config.amount_near_floor)
    )
    if diff <= max(asked * pct, floor):
        return CriterionFit("amount", Fit.NEAR, found, relative)
    return CriterionFit("amount", Fit.MISS, found, max(relative, 1.0))


def _date_fit(txn: Transaction, request: SearchRequest, config: SearchConfig) -> CriterionFit:
    window = request.date_range
    assert window is not None
    if txn.transaction_date is None:
        return CriterionFit("date", Fit.MISS, None, 1.0)
    day = txn.transaction_date.date()
    start, end = window
    if start <= day <= end:
        return CriterionFit("date", Fit.EXACT, day.isoformat())
    off = (start - day).days if day < start else (day - end).days
    fit = Fit.NEAR if off <= config.date_slack_days else Fit.MISS
    return CriterionFit("date", fit, day.isoformat(), float(off))


def _merchant_fit(txn: Transaction, request: SearchRequest, config: SearchConfig) -> CriterionFit:
    name = txn.merchant_name
    asked = _fold(request.merchant or "")
    if not name or not asked:
        return CriterionFit("merchant", Fit.MISS, name, 1.0)
    folded = _fold(name)
    name_tokens = folded.split()
    asked_tokens = asked.split()
    # What a case-insensitive substring match always accepted, now also without accents and in any word order.
    if asked in folded or all(token in name_tokens for token in asked_tokens):
        return CriterionFit("merchant", Fit.EXACT, name)
    if len(asked) < config.merchant_min_chars:
        return CriterionFit("merchant", Fit.MISS, name, 1.0)
    whole = SequenceMatcher(None, asked, folded).ratio()
    per_token = [
        max((SequenceMatcher(None, token, other).ratio() for other in name_tokens), default=0.0)
        for token in asked_tokens
    ]
    best = max(whole, min(per_token, default=0.0))
    if best >= config.merchant_similarity:
        return CriterionFit("merchant", Fit.NEAR, name, 1.0 - best)
    return CriterionFit("merchant", Fit.MISS, name, 1.0 - best)


def _label_fit(criterion: Criterion, value: str | None, asked: str) -> CriterionFit:
    same = value is not None and value.casefold() == asked.casefold()
    return CriterionFit(criterion, Fit.EXACT if same else Fit.MISS, value, 0.0 if same else 1.0)


def grade(txn: Transaction, request: SearchRequest, config: SearchConfig = DEFAULT_CONFIG) -> Match:
    """Grade one charge against everything the customer described."""
    fits: list[CriterionFit] = []
    if request.amount is not None:
        fits.append(_amount_fit(txn, request, config))
    if request.date_range is not None:
        fits.append(_date_fit(txn, request, config))
    if request.merchant:
        fits.append(_merchant_fit(txn, request, config))
    if request.transaction_type:
        fits.append(_label_fit("kind", txn.transaction_type, request.transaction_type))
    if request.category:
        fits.append(_label_fit("category", txn.merchant_category, request.category))
    return Match(txn, tuple(fits))


def _newest_first(matches: Sequence[Match]) -> list[Match]:
    return sorted(matches, key=lambda m: m.transaction.transaction_date or datetime.min, reverse=True)


def find_matches(
    rows: Sequence[Transaction], request: SearchRequest, config: SearchConfig = DEFAULT_CONFIG
) -> MatchResult:
    """The first tier with any charge among `rows` (one customer's transactions)."""
    if not request.criteria & STRONG:
        return MatchResult(Tier.NONE, ())
    graded = [grade(row, request, config) for row in rows]

    exact = [m for m in graded if all(f.fit is Fit.EXACT for f in m.fits)]
    if exact:
        return MatchResult(Tier.EXACT, tuple(_newest_first(exact)[: config.max_exact]))

    near = [m for m in graded if all(f.fit is not Fit.MISS for f in m.fits)]
    if near:
        ranked = sorted(_newest_first(near), key=lambda m: m.distance)  # ties: the newest charge
        return MatchResult(Tier.NEAR, tuple(ranked[: config.max_near]))

    if len(request.criteria) >= 2:
        # One thing differs. It must not be the only thing that held, or any charge of that kind would qualify.
        closest = [
            m
            for m in graded
            if sum(f.fit is Fit.MISS for f in m.fits) == 1
            and any(f.fit is not Fit.MISS and f.criterion in STRONG for f in m.fits)
        ]
        if closest:
            ranked = sorted(
                _newest_first(closest), key=lambda m: sum(f.distance for f in m.fits if f.fit is not Fit.MISS)
            )
            return MatchResult(Tier.CLOSEST, tuple(ranked[: config.max_closest]))
    return MatchResult(Tier.NONE, ())
