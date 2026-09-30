"""Unit tests for bank.* SQLModel row shapes (no database)."""

from datetime import date, datetime
from decimal import Decimal

from minsky_api.store.models import (
    Customer,
    CustomerComplaintStats,
    DisputeScenario,
    Product,
    ResolutionBenchmark,
    Transaction,
)


def test_customer_maps_to_bank_schema():
    assert str(Customer.__tablename__) == "customers"
    assert Customer.__table_args__["schema"] == "bank"
    row = Customer(
        customer_id="C1",
        first_name="Ana",
        country="Mexico",
        segment="Mass",
        customer_status="Active",
        detected_accent="mexican",
    )
    assert row.customer_id == "C1"
    assert row.detected_accent == "mexican"


def test_product_and_transaction_carry_customer_id_as_data():
    product = Product(
        product_id="P1",
        customer_id="C1",
        product_type="Credit Card",
        is_card=True,
        product_number_last4="4242",
        currency="MXN",
        product_status="Active",
        opening_date=date(2024, 1, 1),
        expiration_date=date(2028, 1, 1),
        has_linked_app=True,
    )
    txn = Transaction(
        transaction_id="T1",
        customer_id="C1",
        product_id="P1",
        transaction_date=datetime(2026, 1, 2, 12, 0, 0),
        process_date=date(2026, 1, 2),
        amount=Decimal("100.00"),
        currency="USD",
        amount_usd=Decimal("100.00"),
        amount_usd_source="native_usd",
        transaction_status="Approved",
        is_fraud=False,
        fraud_score=Decimal("1.00"),
    )
    assert str(product.__tablename__) == "products"
    assert str(txn.__tablename__) == "transactions"
    assert product.customer_id == txn.customer_id == "C1"
    assert txn.amount_usd == Decimal("100.00")


def test_complaint_stats_and_benchmark_shapes():
    stats = CustomerComplaintStats(
        customer_id="C1",
        complaints_total=2,
        complaints_last_90d=1,
        last_complaint_at=None,
        is_repeat_complainer=True,
    )
    bench = ResolutionBenchmark(
        category="Transactions",
        priority="all",
        cases=100,
        resolved_cases=40,
        median_resolution_days=15.0,
        p75_resolution_days=22.0,
        sla_breach_rate=0.2,
        rejection_rate=0.01,
    )
    assert str(stats.__tablename__) == "customer_complaint_stats"
    assert str(bench.__tablename__) == "resolution_benchmarks"
    assert stats.is_repeat_complainer is True
    assert bench.priority == "all"


def test_dispute_scenario_composite_key_fields():
    row = DisputeScenario(
        rule_id="D09-eligible",
        customer_says_not_me=False,
        transaction_id="T1",
        route="open_dispute",
        offer_card_block=False,
        customer_id="C1",
        amount_usd=Decimal("50.00"),
        fraud_score=Decimal("1.25"),
    )
    assert str(row.__tablename__) == "dispute_scenarios"
    assert row.rule_id == "D09-eligible"
    assert row.amount_usd == Decimal("50.00")


def test_table_args_are_not_shared_across_models():
    assert Customer.__table_args__ is not Product.__table_args__
    assert Customer.__table_args__ == {"schema": "bank"}
