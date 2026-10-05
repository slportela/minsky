"""Metrics for eval reports: reliability over repeated trials, intervals, rates with denominators.

Conventions (from the Factored brief and Anthropic's agent-eval guidance):
- pass^k (all k trials succeed) is the headline reliability metric for a customer-facing agent;
  pass@k is reported alongside for contrast.
- Every rate carries its numerator, denominator and a 95% interval.
- Zero observed unsafe outcomes is reported with an upper bound, never as "zero risk".
- A rate with an empty denominator is "not defined" (None), never 0.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased P(at least one of k trials succeeds) from n trials with c successes."""
    _check(n, c, k)
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """Unbiased P(all k trials succeed) from n trials with c successes (tau-bench pass^k)."""
    _check(n, c, k)
    return math.comb(c, k) / math.comb(n, k)


def mean_over_tasks(per_task: Sequence[tuple[int, int]], k: int, metric=pass_hat_k) -> float | None:
    """Average a per-task metric over tasks given (n_trials, n_successes) per task."""
    if not per_task:
        return None
    return sum(metric(n, c, k) for n, c in per_task) / len(per_task)


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for a binomial proportion; None when n == 0."""
    if n == 0:
        return None
    if not 0 <= successes <= n:
        raise ValueError("successes must be between 0 and n")
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def zero_event_upper_bound(n: int, alpha: float = 0.05) -> float | None:
    """Exact one-sided upper bound on a rate when 0 events were seen in n trials (~3/n)."""
    if n == 0:
        return None
    return 1 - alpha ** (1 / n)


@dataclass(frozen=True)
class Rate:
    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson_interval(self.numerator, self.denominator)

    def __str__(self) -> str:
        if self.value is None:
            return f"not defined (0/{self.denominator})"
        lo, hi = self.interval or (0.0, 0.0)
        text = f"{self.value:.1%} ({self.numerator}/{self.denominator}, 95% CI {lo:.1%}-{hi:.1%})"
        if self.numerator == 0:
            text += f", upper bound {zero_event_upper_bound(self.denominator):.1%}"
        return text


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100); None for no values."""
    if not values:
        return None
    if not 0 <= q <= 100:
        raise ValueError("q must be between 0 and 100")
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def language_rates(rows: Sequence[tuple[str, int, int]]) -> dict[str, Rate]:
    """Pass rates by language. Each row is (language, passed, graded). Zero graded is not defined."""
    totals: dict[str, list[int]] = {}
    for language, passed, graded in rows:
        bucket = totals.setdefault(language, [0, 0])
        bucket[0] += passed
        bucket[1] += graded
    return {language: Rate(passed, graded) for language, (passed, graded) in totals.items()}


def cost_per(total_cost: float, count: int) -> float | None:
    """Cost per attempted case or per successful resolution; None ('not defined') if count == 0."""
    return total_cost / count if count else None


def _check(n: int, c: int, k: int) -> None:
    if not (0 <= c <= n and 1 <= k <= n):
        raise ValueError(f"need 0 <= c <= n and 1 <= k <= n (got n={n}, c={c}, k={k})")
