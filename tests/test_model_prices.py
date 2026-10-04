"""The price table is evidence that ends up in a ledger entry, so its shape is tested."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from evals.budget import SpendBudget
from evals.model_prices import (
    LONG_CONTEXT_FROM_TOKENS,
    PRICES,
    Tier,
    TokenPrices,
    UnknownModelPrice,
    _main,
    prices_for,
    tier_for_prompt,
)


def test_every_model_has_both_tiers_with_positive_prices():
    for model, tiers in PRICES.items():
        assert set(tiers) == set(Tier), f"{model} is missing a tier"
        for tier, p in tiers.items():
            for field, value in vars(p).items():
                assert isinstance(value, Decimal), f"{model}/{tier}.{field} must be Decimal, not float"
                assert value > 0, f"{model}/{tier}.{field} must be positive"


def test_long_context_is_never_cheaper_than_short():
    for model, tiers in PRICES.items():
        short, long_ = tiers[Tier.SHORT_CONTEXT], tiers[Tier.LONG_CONTEXT]
        for field in vars(short):
            assert getattr(long_, field) >= getattr(short, field), f"{model}.{field}"


def test_the_long_context_surcharge_is_the_published_one():
    """2x input and 1.5x output; a row that breaks this is a transcription error."""
    for model, tiers in PRICES.items():
        short, long_ = tiers[Tier.SHORT_CONTEXT], tiers[Tier.LONG_CONTEXT]
        assert long_.input == short.input * 2, model
        assert long_.cached_input == short.cached_input * 2, model
        assert long_.cache_write == short.cache_write * 2, model
        assert long_.output == short.output * Decimal("1.5"), model


def test_the_tier_is_chosen_by_prompt_size_at_the_published_threshold():
    assert tier_for_prompt(0) is Tier.SHORT_CONTEXT
    assert tier_for_prompt(LONG_CONTEXT_FROM_TOKENS - 1) is Tier.SHORT_CONTEXT
    assert tier_for_prompt(LONG_CONTEXT_FROM_TOKENS) is Tier.LONG_CONTEXT


def test_the_orchestrator_cannot_reach_the_long_context_tier():
    """Why SHORT_CONTEXT is the right default and not just the cheap one."""
    from minsky_api.config import Settings

    s = Settings()
    worst_case_chars = s.max_message_chars * s.max_turns
    assert worst_case_chars < LONG_CONTEXT_FROM_TOKENS, (
        "the conversation cap now allows a long-context prompt: price with tier_for_prompt"
    )


def test_cached_input_is_cheaper_than_input_and_cache_writes_are_not():
    """Caching only pays off in that direction; an inverted row means a transcription error."""
    for model, tiers in PRICES.items():
        for tier, p in tiers.items():
            assert p.cached_input < p.input, f"{model}/{tier}: cached input is not cheaper"
            assert p.cache_write > p.input, f"{model}/{tier}: a cache write is not dearer than a plain input"
            assert p.output > p.input, f"{model}/{tier}: output is not dearer than input"


def test_the_pinned_interim_model_is_priced():
    """ADR 0008 serves gpt-6-luna; a run of it must never need a guessed price."""
    p = prices_for("gpt-6-luna")
    assert (p.input, p.output) == (Decimal("0.10"), Decimal("0.50"))


def test_an_unpriced_model_raises_instead_of_guessing():
    with pytest.raises(UnknownModelPrice) as exc:
        prices_for("gpt-6-does-not-exist")
    assert "known models" in str(exc.value)


def test_a_tier_missing_for_a_known_model_also_raises():
    with pytest.raises(UnknownModelPrice):
        prices_for("gpt-6-luna", "no-such-tier")  # type: ignore[arg-type]


def test_the_prices_drive_a_budget_the_way_a_run_would():
    p = prices_for("gpt-6-luna")
    budget = SpendBudget(Decimal(1), p.input, p.output)
    # 1M in + 1M out at luna's standard tier is 0.10 + 0.50.
    assert budget.cost(1_000_000, 1_000_000) == Decimal("0.60")


def test_flags_output_is_what_the_runner_parses(capsys):
    """The runner refuses a paid run without both flags, so --flags must emit exactly those two."""
    assert _main(["--model", "gpt-6-luna", "--flags"]) == 0
    emitted = capsys.readouterr().out.split()
    assert emitted[0] == "--input-usd-per-million"
    assert emitted[2] == "--output-usd-per-million"
    # The runner reads both with Decimal(); these are the values it would get.
    assert Decimal(emitted[1]) == prices_for("gpt-6-luna").input
    assert Decimal(emitted[3]) == prices_for("gpt-6-luna").output


def test_token_prices_is_frozen_so_a_caller_cannot_edit_the_table():
    p = prices_for("gpt-6-luna")
    with pytest.raises(FrozenInstanceError):
        p.input = Decimal("999")  # type: ignore[misc]
    assert isinstance(p, TokenPrices)
