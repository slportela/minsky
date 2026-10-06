"""Unit tests for bank.* stores with a fake AsyncSession (no Postgres)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from minsky_api.store.benchmarks import BenchmarkStore
from minsky_api.store.complaint_stats import ComplaintStatsStore
from minsky_api.store.customers import CustomerStore
from minsky_api.store.dispute_scenarios import DisputeScenarioStore
from minsky_api.store.errors import StoreError
from minsky_api.store.models import (
    Customer,
    CustomerComplaintStats,
    DisputeScenario,
    Product,
    ResolutionBenchmark,
    Transaction,
)
from minsky_api.store.products import ProductStore
from minsky_api.store.transactions import TransactionStore


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    def __init__(self, *, get_result: Any = None, exec_rows: list[Any] | None = None) -> None:
        self.get_result = get_result
        self.exec_rows = exec_rows or []
        self.get_calls: list[tuple[type, Any]] = []
        self.exec_statements: list[Any] = []
        self.fail_get = False

    async def get(self, model: type, identity: Any) -> Any:
        self.get_calls.append((model, identity))
        if self.fail_get:
            raise OperationalError("select", {}, Exception("down"))
        return self.get_result

    async def exec(self, statement: Any) -> _FakeResult:
        self.exec_statements.append(statement)
        return _FakeResult(self.exec_rows)


def _sql(statement: Any) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_customer_get_by_pk():
    customer = Customer(customer_id="C1", first_name="Ana")
    session = FakeSession(get_result=customer)
    store = CustomerStore(session)  # type: ignore[arg-type]
    assert await store.get("C1") is customer
    assert session.get_calls == [(Customer, "C1")]


@pytest.mark.asyncio
async def test_customer_get_miss_returns_none():
    session = FakeSession(get_result=None)
    store = CustomerStore(session)  # type: ignore[arg-type]
    assert await store.get("missing") is None


@pytest.mark.asyncio
async def test_get_wraps_driver_errors():
    session = FakeSession()
    session.fail_get = True
    store = CustomerStore(session)  # type: ignore[arg-type]
    with pytest.raises(StoreError, match="failed to get Customer"):
        await store.get("C1")


@pytest.mark.asyncio
async def test_product_get_and_list_by_customer():
    product = Product(product_id="P1", customer_id="C1")
    session = FakeSession(get_result=product, exec_rows=[product])
    store = ProductStore(session)  # type: ignore[arg-type]
    assert await store.get("P1") is product
    rows = await store.list_by_customer("C1")
    assert rows == (product,)
    sql = _sql(session.exec_statements[0])
    assert "bank.products" in sql
    assert "customer_id" in sql


@pytest.mark.asyncio
async def test_transaction_list_respects_limit_and_rejects_out_of_range():
    txn = Transaction(
        transaction_id="T1",
        customer_id="C1",
        product_id="P1",
        amount_usd=Decimal("10.00"),
        amount_usd_source="native_usd",
        transaction_date=datetime(2026, 1, 1),
    )
    session = FakeSession(exec_rows=[txn])
    store = TransactionStore(session)  # type: ignore[arg-type]
    assert await store.list_by_customer("C1", limit=10) == (txn,)
    sql = _sql(session.exec_statements[0])
    assert "bank.transactions" in sql
    assert "customer_id" in sql
    assert "transaction_date" in sql
    assert "DESC" in sql
    assert "LIMIT" in sql
    with pytest.raises(ValueError, match="limit must be between"):
        await store.list_by_customer("C1", limit=0)
    with pytest.raises(ValueError, match="limit must be between"):
        await store.list_by_customer("C1", limit=101)


@pytest.mark.asyncio
async def test_transaction_get_by_pk_only():
    session = FakeSession(get_result=None)
    store = TransactionStore(session)  # type: ignore[arg-type]
    await store.get("T1")
    assert session.get_calls == [(Transaction, "T1")]


@pytest.mark.asyncio
async def test_complaint_stats_and_benchmark_get():
    stats = CustomerComplaintStats(customer_id="C1", is_repeat_complainer=False)
    bench = ResolutionBenchmark(category="Transactions", priority="all")
    stats_session = FakeSession(get_result=stats)
    bench_session = FakeSession(get_result=bench)
    assert await ComplaintStatsStore(stats_session).get("C1") is stats  # type: ignore[arg-type]
    assert await BenchmarkStore(bench_session).get("Transactions", "all") is bench  # type: ignore[arg-type]
    assert bench_session.get_calls == [(ResolutionBenchmark, ("Transactions", "all"))]


@pytest.mark.asyncio
async def test_dispute_scenario_list_for_rule_and_all():
    row = DisputeScenario(
        rule_id="D09-eligible",
        customer_says_not_me=False,
        transaction_id="T1",
        customer_id="C1",
        amount_usd=Decimal("50.00"),
    )
    session = FakeSession(exec_rows=[row])
    store = DisputeScenarioStore(session)  # type: ignore[arg-type]
    assert await store.list_for_rule("D09-eligible") == (row,)
    assert await store.list_all() == (row,)
    assert "rule_id" in _sql(session.exec_statements[0])
    assert len(session.exec_statements) == 2


@pytest.mark.asyncio
async def test_transaction_filters_are_customer_scoped_and_escape_like_wildcards():
    session = FakeSession(exec_rows=[])
    store = TransactionStore(session)  # type: ignore[arg-type]
    await store.list_by_customer(
        "C1", limit=5, merchant="50%_off", date_from=date(2026, 6, 1), date_to=date(2026, 6, 15)
    )
    statement = session.exec_statements[0]
    sql = _sql(statement)
    assert "customer_id" in sql and "ILIKE" in sql.upper()
    params = statement.compile(dialect=postgresql.dialect()).params
    assert "%50\\%\\_off%" in params.values()  # the customer's % and _ are literal, not wildcards
    assert date(2026, 6, 16) in params.values()  # date_to is inclusive: < the next day


@pytest.mark.asyncio
async def test_search_pool_is_one_customers_history_newest_first_and_never_truncated_silently():
    from minsky_api.store.errors import StoreError
    from minsky_api.store.transactions import SEARCH_POOL

    txn = Transaction(
        transaction_id="T1",
        customer_id="C1",
        product_id="P1",
        amount_usd=Decimal("10.00"),
        amount_usd_source="native_usd",
        transaction_date=datetime(2026, 1, 1),
    )
    session = FakeSession(exec_rows=[txn])
    assert await TransactionStore(session).search_pool("C1") == (txn,)  # type: ignore[arg-type]
    sql = _sql(session.exec_statements[0])
    assert "customer_id" in sql and "DESC" in sql and "LIMIT" in sql

    too_many = FakeSession(exec_rows=[txn] * (SEARCH_POOL + 1))
    with pytest.raises(StoreError, match="search pool exceeded"):
        await TransactionStore(too_many).search_pool("C1")  # type: ignore[arg-type]


async def test_random_customer_samples_then_checks_for_a_recent_posted_charge_under_the_limit():
    from decimal import Decimal

    class _Scalar:
        def __init__(self, value: Any) -> None:
            self.value = value

        def scalar(self) -> Any:
            return self.value

    class _Executing(FakeSession):
        def __init__(self, answers: list[Any]) -> None:
            super().__init__()
            self.answers = answers
            self.calls: list[tuple[Any, dict[str, Any]]] = []

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Scalar:
            self.calls.append((statement, params))
            return _Scalar(self.answers.pop(0))

    session = _Executing(["CLI-X"])
    picked = await CustomerStore(session).random_with_recent_charge(  # type: ignore[arg-type]
        today=date(2026, 6, 18), window_days=120, max_amount_usd=Decimal("500")
    )
    assert picked == "CLI-X"
    statement, params = session.calls[0]
    sql = str(statement)
    assert "tablesample bernoulli" in sql and "random()" in sql and "'Approved'" in sql
    assert "amount_usd <= :max_amount" in sql and "is_repeat_complainer is not true" in sql
    assert params == {"since": date(2026, 2, 18), "max_amount": Decimal("500")}

    empty_twice = _Executing([None, None, "CLI-Y"])  # a sample can come up empty: it is tried again
    assert await CustomerStore(empty_twice).random_with_recent_charge(today=date(2026, 6, 18)) == "CLI-Y"  # type: ignore[arg-type]
    assert len(empty_twice.calls) == 3
    never = _Executing([None] * 5)
    assert await CustomerStore(never).random_with_recent_charge(today=date(2026, 6, 18)) is None  # type: ignore[arg-type]

    failing = FakeSession()

    async def boom(statement: Any, params: dict[str, Any]) -> Any:
        raise OperationalError("select", {}, Exception("down"))

    failing.execute = boom  # type: ignore[attr-defined]
    with pytest.raises(StoreError):
        await CustomerStore(failing).random_with_recent_charge(today=date(2026, 6, 18))  # type: ignore[arg-type]
