"""Published token prices for the models we may serve, in one place.

`evals.runner` refuses a paid run unless both token prices are passed explicitly
(`--input-usd-per-million`, `--output-usd-per-million`), so this table is not a default the
runner reads behind your back: it is the single place the Makefile and a report take the
numbers from, so a price lives in one file instead of in every command line and ledger entry.

Recorded from the provider's pricing page on 2026-10-04 for the interim provider of ADR 0008.
Prices change; re-check before a run that matters and update the date. USD per million tokens.

Two dimensions price a call, and this table covers one value of the first:

1. **Service tier** - the page offers Standard, Batch, Flex, Fast and Ultrafast. **Only Standard
   is recorded here**, because that is what the synchronous `responses` call in
   `minsky_api.llm.client` uses. The others are not transcribed, so nothing may price a run
   against them from this file. Worth checking before a large eval run: an offline eval is the
   workload a batch tier exists for, and it is usually the cheapest.
2. **Prompt size** - `SHORT_CONTEXT` below 272K tokens, `LONG_CONTEXT` at or above it, costing
   2x the input and 1.5x the output.

`SHORT_CONTEXT` is the default and the applicable one here, not merely the cheaper one: the
orchestrator caps a message at `max_message_chars` (4000) over `max_turns` (12) turns, about 48K
characters of conversation at the very worst, so a prompt cannot reach the long-context
threshold. Use `tier_for_prompt` rather than choosing by hand if that ever stops being true.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

#: Prompt size at which the provider switches to its long-context prices.
LONG_CONTEXT_FROM_TOKENS: Final = 272_000


class Tier(StrEnum):
    SHORT_CONTEXT = "short-context"  # prompt under LONG_CONTEXT_FROM_TOKENS
    LONG_CONTEXT = "long-context"  # prompt at or over it: 2x input, 1.5x output


@dataclass(frozen=True)
class TokenPrices:
    """USD per million tokens."""

    input: Decimal
    cached_input: Decimal
    cache_write: Decimal
    output: Decimal


def _p(input_: str, cached_input: str, cache_write: str, output: str) -> TokenPrices:
    return TokenPrices(Decimal(input_), Decimal(cached_input), Decimal(cache_write), Decimal(output))


#: The service tier these prices are for; see the module docstring before adding another.
SERVICE_TIER: Final = "standard"

# Model id as the provider's API expects it (`MINSKY_LLM_MODEL`), not a Bedrock inference profile.
PRICES: Final[dict[str, dict[Tier, TokenPrices]]] = {
    "gpt-6-luna": {
        Tier.SHORT_CONTEXT: _p("0.10", "0.01", "0.125", "0.50"),
        Tier.LONG_CONTEXT: _p("0.20", "0.02", "0.25", "0.75"),
    },
    "gpt-6.1-sol": {
        Tier.SHORT_CONTEXT: _p("2.00", "0.10", "2.50", "10.00"),
        Tier.LONG_CONTEXT: _p("4.00", "0.20", "5.00", "15.00"),
    },
    "gpt-6-astra": {
        Tier.SHORT_CONTEXT: _p("10.00", "1.00", "12.50", "50.00"),
        Tier.LONG_CONTEXT: _p("20.00", "2.00", "25.00", "75.00"),
    },
}

PRICES_AS_OF: Final = "2026-10-04"


class UnknownModelPrice(KeyError):
    """No published price recorded for this model: add it rather than guessing."""


def tier_for_prompt(input_tokens: int) -> Tier:
    """Which tier a prompt of this size is billed at."""
    return Tier.LONG_CONTEXT if input_tokens >= LONG_CONTEXT_FROM_TOKENS else Tier.SHORT_CONTEXT


def prices_for(model: str, tier: Tier = Tier.SHORT_CONTEXT) -> TokenPrices:
    """The recorded prices for `model`. Raises rather than falling back to a guess."""
    try:
        return PRICES[model][tier]
    except KeyError as exc:
        known = ", ".join(sorted(PRICES))
        raise UnknownModelPrice(f"no price recorded for {model!r} at tier {tier}; known models: {known}") from exc


def _main(argv: list[str] | None = None) -> int:
    """Print the prices for a model so a command line states them instead of repeating them.

    `--flags` emits the two runner options; the runner still receives them explicitly, which is
    what `evals.runner` requires before a paid run.
    """
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="model id (default: the one pinned in config)")
    ap.add_argument("--tier", type=Tier, choices=list(Tier), default=Tier.SHORT_CONTEXT)
    ap.add_argument("--flags", action="store_true", help="print the evals.runner price options")
    args = ap.parse_args(argv)

    model = args.model
    if model is None:
        from minsky_api.config import get_settings  # imported late: only this path needs the app config

        model = get_settings().llm_model
    p = prices_for(model, args.tier)
    if args.flags:
        print(f"--input-usd-per-million {p.input} --output-usd-per-million {p.output}")
    else:
        print(
            f"{model} ({SERVICE_TIER}/{args.tier}, as of {PRICES_AS_OF}) USD/1M: in {p.input} "
            f"· cached {p.cached_input} "
            f"· cache-write {p.cache_write} · out {p.output}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
