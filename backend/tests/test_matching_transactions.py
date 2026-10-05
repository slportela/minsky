"""Flexible matching, rule by rule (F1-F6): near amount, currency, date, combined, merchant, kind/category.

The matcher is pure: rows in, tiers out. Each test names the customer's words and what the system may propose.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from minsky_api.matching import (
    Fit,
    SearchConfig,
    SearchRequest,
    Tier,
    find_matches,
    grade,
)
from minsky_api.store.models import Transaction


def _txn(
    transaction_id: str = "T1",
    *,
    amount: str = "123.10",
    currency: str = "USD",
    amount_usd: str | None = None,
    when: datetime = datetime(2026, 6, 17, 10, 0),
    merchant: str | None = "Super Ahorro",
    kind: str = "Purchase",
    category: str | None = "Food",
) -> Transaction:
    return Transaction(
        transaction_id=transaction_id,
        customer_id="C1",
        product_id="P1",
        transaction_date=when,
        transaction_type=kind,
        amount=Decimal(amount),
        currency=currency,
        amount_usd=Decimal(amount_usd or amount),
        amount_usd_source="native_usd",
        merchant_name=merchant,
        merchant_category=category,
        transaction_status="Approved",
    )


def _ids(result) -> list[str]:
    return [match.transaction.transaction_id for match in result.matches]


# --- F1: near amount ---------------------------------------------------------------------------------------------


def test_f1_an_amount_a_few_cents_off_is_near_not_a_miss():
    result = find_matches([_txn(amount="123.10")], SearchRequest(amount=Decimal("123")))
    assert result.tier is Tier.NEAR
    assert result.matches[0].fits[0].fit is Fit.NEAR
    assert result.matches[0].fits[0].found == "123.10 USD"


def test_f1_the_exact_amount_is_exact_and_hides_the_near_ones():
    rows = [_txn("T-NEAR", amount="25.38"), _txn("T-EXACT", amount="25.37")]
    result = find_matches(rows, SearchRequest(amount=Decimal("25.37")))
    assert result.tier is Tier.EXACT
    assert _ids(result) == ["T-EXACT"]


def test_f1_one_percent_is_the_edge_and_ten_percent_needs_the_customer_to_be_loose():
    row = _txn(amount="108.00")  # 8 % off 100
    assert find_matches([row], SearchRequest(amount=Decimal("100"))).tier is Tier.NONE
    loose = find_matches([row], SearchRequest(amount=Decimal("100"), approximate=True))
    assert loose.tier is Tier.NEAR
    assert (
        find_matches([_txn(amount="112.00")], SearchRequest(amount=Decimal("100"), approximate=True)).tier is Tier.NONE
    )


def test_f1_a_small_amount_still_tolerates_a_few_cents():
    assert find_matches([_txn(amount="5.08")], SearchRequest(amount=Decimal("5.00"))).tier is Tier.NEAR
    assert find_matches([_txn(amount="5.25")], SearchRequest(amount=Decimal("5.00"))).tier is Tier.NONE


def test_f1_the_thresholds_are_configuration():
    row = _txn(amount="103.00")
    assert find_matches([row], SearchRequest(amount=Decimal("100"))).tier is Tier.NONE
    wide = SearchConfig(amount_near_pct=Decimal("0.05"))
    assert find_matches([row], SearchRequest(amount=Decimal("100")), wide).tier is Tier.NEAR


# --- F2: the currency the customer means -------------------------------------------------------------------------


def test_f2_usd_is_compared_with_the_usd_amount_of_a_local_currency_charge():
    cop = _txn(amount="500000.00", currency="COP", amount_usd="123.00")
    result = find_matches([cop], SearchRequest(amount=Decimal("123"), currency="USD"))
    assert result.tier is Tier.EXACT
    assert result.matches[0].fits[0].found == "123.00 USD"


def test_f2_the_local_currency_is_compared_with_the_own_amount():
    cop = _txn(amount="500000.00", currency="COP", amount_usd="123.00")
    assert find_matches([cop], SearchRequest(amount=Decimal("500000"), currency="COP")).tier is Tier.EXACT
    # 500000 asked in USD is not this charge: its USD amount is 123.
    assert find_matches([cop], SearchRequest(amount=Decimal("500000"), currency="USD")).tier is Tier.NONE


def test_f2_a_currency_the_charge_does_not_carry_cannot_match():
    cop = _txn(amount="500000.00", currency="COP", amount_usd="123.00")
    fit = grade(cop, SearchRequest(amount=Decimal("500000"), currency="ARS")).fits[0]
    assert fit.fit is Fit.MISS


def test_f2_with_no_currency_either_amount_may_be_meant():
    cop = _txn(amount="500000.00", currency="COP", amount_usd="123.00")
    assert find_matches([cop], SearchRequest(amount=Decimal("500000"))).tier is Tier.EXACT
    assert find_matches([cop], SearchRequest(amount=Decimal("123"))).tier is Tier.EXACT


# --- F3: date only, possibly several charges ---------------------------------------------------------------------


def test_f3_a_day_finds_every_charge_of_that_day():
    rows = [
        _txn("T1", when=datetime(2026, 6, 17, 9, 0)),
        _txn("T2", when=datetime(2026, 6, 17, 20, 0)),
        _txn("T3", when=datetime(2026, 6, 10, 9, 0)),
    ]
    result = find_matches(rows, SearchRequest(date_from=date(2026, 6, 17), date_to=date(2026, 6, 17)))
    assert result.tier is Tier.EXACT
    assert _ids(result) == ["T2", "T1"]  # newest first


def test_f3_a_day_off_is_near_and_two_days_off_is_nothing():
    asked = SearchRequest(date_from=date(2026, 6, 17), date_to=date(2026, 6, 17))
    assert find_matches([_txn(when=datetime(2026, 6, 18, 1, 0))], asked).tier is Tier.NEAR
    assert find_matches([_txn(when=datetime(2026, 6, 19, 1, 0))], asked).tier is Tier.NONE


def test_f3_a_range_holds_its_edges():
    asked = SearchRequest(date_from=date(2026, 6, 10), date_to=date(2026, 6, 12))
    rows = [_txn("A", when=datetime(2026, 6, 10, 0, 0)), _txn("B", when=datetime(2026, 6, 12, 23, 59))]
    assert find_matches(rows, asked).tier is Tier.EXACT


def test_f3_a_single_written_edge_is_a_single_day():
    only_from = SearchRequest(date_from=date(2026, 6, 17))
    assert only_from.date_range == (date(2026, 6, 17), date(2026, 6, 17))


# --- F4: amount and date together, and the closest charge --------------------------------------------------------


def test_f4_about_80_yesterday_proposes_the_83_of_today_and_says_both_differ():
    asked = SearchRequest(
        amount=Decimal("80"), approximate=True, date_from=date(2026, 6, 17), date_to=date(2026, 6, 17)
    )
    result = find_matches([_txn(amount="83.00", when=datetime(2026, 6, 18, 2, 0))], asked)
    assert result.tier is Tier.NEAR
    assert [(fit.criterion, fit.fit) for fit in result.matches[0].fits] == [("amount", Fit.NEAR), ("date", Fit.NEAR)]


def test_f4_one_thing_far_off_is_the_closest_charge():
    asked = SearchRequest(amount=Decimal("50"), date_from=date(2026, 6, 10), date_to=date(2026, 6, 10))
    result = find_matches([_txn(amount="25.00", when=datetime(2026, 6, 10, 9, 0))], asked)
    assert result.tier is Tier.CLOSEST
    assert [(fit.criterion, fit.fit) for fit in result.matches[0].fits] == [("amount", Fit.MISS), ("date", Fit.EXACT)]


def test_f4_two_things_off_is_nothing():
    asked = SearchRequest(amount=Decimal("50"), date_from=date(2026, 6, 1), date_to=date(2026, 6, 1))
    assert find_matches([_txn(amount="25.00", when=datetime(2026, 6, 10, 9, 0))], asked).tier is Tier.NONE


def test_f4_one_thing_alone_that_misses_is_not_a_closest_charge():
    assert find_matches([_txn(amount="25.00")], SearchRequest(amount=Decimal("50"))).tier is Tier.NONE


def test_f4_exact_beats_near_beats_closest():
    asked = SearchRequest(amount=Decimal("100"), merchant="Super Ahorro")
    closest = _txn("CLOSEST", amount="40.00")
    near = _txn("NEAR", amount="100.50")
    assert find_matches([closest, near], asked).tier is Tier.NEAR
    assert _ids(find_matches([closest, near, _txn("EXACT", amount="100.00")], asked)) == ["EXACT"]


def test_f4_near_charges_are_ranked_by_how_close_and_capped():
    rows = [_txn(f"T{i}", amount=f"{100 + i * 0.1:.2f}") for i in range(1, 9)]
    result = find_matches(rows, SearchRequest(amount=Decimal("100")))
    assert result.tier is Tier.NEAR
    assert _ids(result) == ["T1", "T2", "T3", "T4", "T5"]


# --- F5: merchant, normalized and approximate --------------------------------------------------------------------


def test_f5_case_accents_and_word_order_do_not_matter():
    row = _txn(merchant="Clínica Médica")
    for said in ("clinica medica", "CLÍNICA", "medica clinica"):
        assert find_matches([row], SearchRequest(merchant=said)).tier is Tier.EXACT, said


def test_f5_a_substring_of_the_name_is_exact_as_it_always_was():
    assert (
        find_matches([_txn(merchant="Restaurante El Buen Sabor")], SearchRequest(merchant="buen sabor")).tier
        is Tier.EXACT
    )


def test_f5_a_misspelling_is_near():
    result = find_matches([_txn(merchant="Starbucks")], SearchRequest(merchant="starbuks"))
    assert result.tier is Tier.NEAR
    assert result.matches[0].fits[0].found == "Starbucks"


def test_f5_a_different_name_is_not_near():
    assert find_matches([_txn(merchant="Taxi Seguro")], SearchRequest(merchant="Cine Premium")).tier is Tier.NONE


def test_f5_a_short_name_must_match_exactly():
    assert find_matches([_txn(merchant="Uber")], SearchRequest(merchant="ube")).tier is Tier.EXACT  # a substring
    assert find_matches([_txn(merchant="Uber")], SearchRequest(merchant="uno")).tier is Tier.NONE


def test_f5_a_charge_without_merchant_never_matches_a_merchant():
    assert find_matches([_txn(merchant=None, kind="Withdrawal")], SearchRequest(merchant="Cafe")).tier is Tier.NONE


# --- F6: kind and category --------------------------------------------------------------------------------------


def test_f6_kind_narrows_an_amount():
    rows = [_txn("PURCHASE", amount="100.00"), _txn("WITHDRAWAL", amount="100.00", kind="Withdrawal", merchant=None)]
    result = find_matches(rows, SearchRequest(amount=Decimal("100"), transaction_type="Withdrawal"))
    assert _ids(result) == ["WITHDRAWAL"]


def test_f6_category_narrows_a_date():
    rows = [_txn("FOOD", category="Food"), _txn("HEALTH", category="Health")]
    day = date(2026, 6, 17)
    result = find_matches(rows, SearchRequest(date_from=day, date_to=day, category="health"))
    assert _ids(result) == ["HEALTH"]


def test_f6_kind_or_category_alone_finds_nothing():
    rows = [_txn("A"), _txn("B")]
    assert find_matches(rows, SearchRequest(transaction_type="Purchase")).tier is Tier.NONE
    assert find_matches(rows, SearchRequest(category="Food")).tier is Tier.NONE
    assert find_matches(rows, SearchRequest()).tier is Tier.NONE


def test_f6_a_wrong_kind_with_a_right_amount_is_the_closest_charge():
    row = _txn(amount="100.00", kind="Purchase")
    result = find_matches([row], SearchRequest(amount=Decimal("100"), transaction_type="Withdrawal"))
    assert result.tier is Tier.CLOSEST
    assert [(fit.criterion, fit.fit) for fit in result.matches[0].fits] == [("amount", Fit.EXACT), ("kind", Fit.MISS)]


def test_f6_a_kind_that_holds_does_not_justify_a_charge_whose_amount_missed():
    # Both things described, only the weak one holds: any purchase would qualify, so none does.
    result = find_matches([_txn(amount="25.00")], SearchRequest(amount=Decimal("500"), transaction_type="Purchase"))
    assert result.tier is Tier.NONE


# --- What the matcher never does ---------------------------------------------------------------------------------


def test_status_is_not_a_filter_so_a_declined_charge_can_be_found():
    declined = _txn()
    declined.transaction_status = "Declined"
    assert find_matches([declined], SearchRequest(amount=Decimal("123.10"))).tier is Tier.EXACT


def test_no_rows_is_none():
    assert find_matches([], SearchRequest(amount=Decimal("1"))).tier is Tier.NONE
