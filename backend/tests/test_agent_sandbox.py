"""The query sandbox: the model's SQL can read one customer's transactions and nothing else."""

from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal

import pytest

from minsky_api.agent.sandbox import MAX_ROWS, QuerySandbox, SandboxError
from minsky_api.store.models import Transaction

TODAY = date(2026, 6, 18)


def _txn(txn_id: str, amount: str, merchant: str | None, when: datetime, **kwargs: object) -> Transaction:
    return Transaction(
        transaction_id=txn_id,
        customer_id="C1",
        product_id="P1",
        amount=Decimal(amount),
        currency="USD",
        amount_usd=Decimal(amount),
        amount_usd_source="native_usd",
        transaction_date=when,
        merchant_name=merchant,
        transaction_status="Approved",
        is_fraud=True,
        fraud_score=Decimal("99.00"),
        response_code="00",
        **kwargs,  # type: ignore[arg-type]
    )


def _sandbox() -> QuerySandbox:
    return QuerySandbox(
        [
            _txn("T1", "123.10", "Café Sur", datetime(2026, 6, 17, 9, 0), transaction_type="Purchase"),
            _txn("T2", "83.00", "Super Ahorro", datetime(2026, 6, 18, 1, 0), transaction_type="Purchase"),
            _txn("T3", "40.00", None, datetime(2026, 5, 1, 12, 0), transaction_type="Withdrawal"),
        ],
        today=TODAY,
    )


def test_a_near_amount_is_found_with_a_range():
    result = _sandbox().query("SELECT transaction_id, amount FROM transactions WHERE abs(amount - 123) < 1")
    assert result.rows == (("T1", 123.1),)


def test_today_is_the_date_of_the_data_not_the_clock():
    result = _sandbox().query(
        "SELECT transaction_id FROM transactions WHERE date(transaction_date) = date(today(), '-1 day')"
    )
    assert result.rows == (("T1",),)


def test_merchant_search_ignores_case_and_accents():
    result = _sandbox().query("SELECT transaction_id FROM transactions WHERE fold(merchant_name) LIKE '%cafe%'")
    assert result.rows == (("T1",),)


def test_aggregates_and_ordering_work():
    sandbox = _sandbox()
    assert sandbox.query("SELECT count(*) FROM transactions").rows == ((3,),)
    assert sandbox.query("SELECT transaction_id FROM transactions ORDER BY transaction_date DESC LIMIT 1").rows == (
        ("T2",),
    )


def test_rows_are_capped_and_the_cap_is_reported():
    many = [_txn(f"T{i}", "1.00", "X", datetime(2026, 6, 1, 0, 0)) for i in range(MAX_ROWS + 5)]
    result = QuerySandbox(many, today=TODAY).query("SELECT transaction_id FROM transactions")
    assert len(result.rows) == MAX_ROWS
    assert result.truncated is True


@pytest.mark.parametrize(
    "hidden", ["is_fraud", "fraud_score", "response_code", "customer_id", "product_id", "process_date"]
)
def test_columns_the_model_must_not_see_do_not_exist(hidden):
    with pytest.raises(SandboxError):
        _sandbox().query(f"SELECT {hidden} FROM transactions")


def test_select_star_has_no_hidden_column():
    columns = _sandbox().query("SELECT * FROM transactions").columns
    assert not {"is_fraud", "fraud_score", "response_code", "customer_id", "product_id"} & set(columns)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM sqlite_master",
        "SELECT name FROM sqlite_schema",
        "PRAGMA table_info(transactions)",
        "ATTACH DATABASE ':memory:' AS other",
        "SELECT 1; SELECT 2",
        "SELECT 1; DELETE FROM transactions",
        "DELETE FROM transactions",
        "UPDATE transactions SET amount = 0",
        "INSERT INTO transactions (transaction_id) VALUES ('x')",
        "DROP TABLE transactions",
        "CREATE TABLE t (x)",
        "CREATE VIEW v AS SELECT 1",
        "SELECT load_extension('x')",
        "SELECT readfile('/etc/passwd')",
        "SELECT randomblob(1000000000)",
        "SELECT zeroblob(1000000000)",
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT count(*) FROM r",
        "SELECT * FROM pragma_table_info('transactions')",
        "SELECT date('now')",
        "SELECT datetime('NOW')",
        "",
        "   ",
    ],
)
def test_anything_but_a_plain_select_on_the_table_is_refused(sql):
    with pytest.raises(SandboxError):
        _sandbox().query(sql)


def test_the_data_survives_a_refused_write():
    sandbox = _sandbox()
    with pytest.raises(SandboxError):
        sandbox.query("DELETE FROM transactions")
    assert sandbox.query("SELECT count(*) FROM transactions").rows == ((3,),)


def test_a_very_long_query_is_refused():
    with pytest.raises(SandboxError):
        _sandbox().query("SELECT 1 WHERE 1 = 1 " + "AND 1 = 1 " * 300)


def test_a_runaway_query_is_interrupted_by_the_deadline():
    many = [_txn(f"T{i}", "1.00", "X", datetime(2026, 6, 1, 0, 0)) for i in range(150)]
    sandbox = QuerySandbox(many, today=TODAY)
    started = time.monotonic()
    with pytest.raises(SandboxError):
        sandbox.query(
            "SELECT count(*) FROM transactions a, transactions b, transactions c, transactions d, transactions e"
        )
    assert time.monotonic() - started < 3


def test_the_sandbox_holds_only_the_rows_it_was_given():
    assert _sandbox().row_count == 3
    other = QuerySandbox([], today=TODAY)
    assert other.query("SELECT count(*) FROM transactions").rows == ((0,),)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT length(replace(printf('%10000d', 1), ' ', 'abcdefghij'))",
        "SELECT length(replace(printf('%5000d', 1), ' ', 'abcdefghijklmnopqrstuvwxyz'))",
    ],
)
def test_a_query_cannot_build_a_huge_value(sql):
    with pytest.raises(SandboxError, match="too large"):
        _sandbox().query(sql)


def test_a_huge_printf_is_cut_before_it_fills_memory():
    result = _sandbox().query("SELECT length(printf('%10000000d', 1))")
    assert result.rows == ((None,),)  # SQLite refuses the value: it is never built
