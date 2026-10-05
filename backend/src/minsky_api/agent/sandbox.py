"""Read-only SQL over one customer's own transactions, for the search agent (docs/agentic_dispute_agent.md).

Isolation is by construction: an in-memory SQLite database is built per customer from the rows the session
customer owns, so no query can reach anyone else's data. The authorizer, the limits and the deadline then keep
the model's SQL to a harmless `SELECT` on one table. The columns that would leak the fraud label, and ids
the model has no use for, are never loaded.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from minsky_api.store.models import Transaction

TABLE = "transactions"

# (name, SQLite type). Not loaded on purpose: customer_id, product_id, is_fraud, fraud_score, response_code,
# process_date. The fraud flag stays with the policy (AGENTS rule 1); the rest is not something a customer says.
COLUMNS: tuple[tuple[str, str], ...] = (
    ("transaction_id", "TEXT"),
    ("transaction_date", "TEXT"),
    ("transaction_type", "TEXT"),
    ("transaction_category", "TEXT"),
    ("amount", "REAL"),
    ("currency", "TEXT"),
    ("amount_usd", "REAL"),
    ("channel", "TEXT"),
    ("merchant_name", "TEXT"),
    ("merchant_category", "TEXT"),
    ("transaction_country", "TEXT"),
    ("transaction_city", "TEXT"),
    ("transaction_status", "TEXT"),
)
_COLUMN_NAMES = frozenset(name for name, _ in COLUMNS)

MAX_ROWS = 20
MAX_SQL_CHARS = 1500
DEADLINE_S = 0.5
_PROGRESS_EVERY = 5_000  # SQLite VM steps between deadline checks

_FUNCTIONS = frozenset(
    {
        "abs", "round", "lower", "upper", "length", "substr", "substring", "instr", "replace", "trim", "ltrim",
        "rtrim", "coalesce", "ifnull", "nullif", "iif", "min", "max", "count", "sum", "avg", "total", "date",
        "datetime", "time", "julianday", "strftime", "printf", "typeof", "like", "glob", "today", "fold",
    }
)  # fmt: skip
# The clock is the data's "today" (settings.today), not the machine's: `today()` is provided, 'now' is refused.
_NOW = re.compile(r"\bnow\b", re.IGNORECASE)


class SandboxError(ValueError):
    """The query is refused or failed. The message goes back to the model so it can fix the query."""


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool  # more than MAX_ROWS rows matched: the model must narrow the query


def _fold(text: str | None) -> str | None:
    """Lower case without accents, so 'Cafe' finds 'Café' and 'jose' finds 'José'."""
    if text is None:
        return None
    import unicodedata

    decomposed = unicodedata.normalize("NFD", str(text).casefold())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _authorize(action: int, arg1: str | None, arg2: str | None, _db: str | None, _source: str | None) -> int:
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        # arg1 is the table, arg2 the column ("" for a count(*)). sqlite_master and anything else is refused.
        ok = arg1 == TABLE and (arg2 == "" or arg2 in _COLUMN_NAMES)
        return sqlite3.SQLITE_OK if ok else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_OK if (arg2 or "").lower() in _FUNCTIONS else sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY  # ATTACH, PRAGMA, writes, recursion, transactions, virtual tables, ...


def _cell(value: Any) -> Any:
    return round(value, 2) if isinstance(value, float) else value


class QuerySandbox:
    """One customer's transactions, queryable and nothing else. Build it per turn: it is not kept in state."""

    def __init__(self, transactions: Iterable[Transaction], *, today: date) -> None:
        self._con = sqlite3.connect(":memory:")
        self._con.create_function("today", 0, lambda: today.isoformat(), deterministic=True)
        self._con.create_function("fold", 1, _fold, deterministic=True)
        names = ", ".join(name for name, _ in COLUMNS)
        self._con.execute(f"CREATE TABLE {TABLE} ({', '.join(f'{n} {t}' for n, t in COLUMNS)})")
        rows = [self._row(txn) for txn in transactions]
        self._con.executemany(f"INSERT INTO {TABLE} ({names}) VALUES ({', '.join('?' * len(COLUMNS))})", rows)
        self._con.commit()
        self.row_count = len(rows)
        # Locked after loading: from here on the connection can only run the authorized SELECT.
        self._con.execute("PRAGMA query_only = ON")
        self._con.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_CHARS)
        self._con.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 20)
        self._con.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 3)
        self._con.setlimit(sqlite3.SQLITE_LIMIT_LIKE_PATTERN_LENGTH, 60)
        self._con.set_authorizer(_authorize)
        self._deadline = 0.0
        self._con.set_progress_handler(lambda: 1 if time.monotonic() > self._deadline else 0, _PROGRESS_EVERY)

    @staticmethod
    def _row(txn: Transaction) -> tuple[Any, ...]:
        def num(value: Any) -> float | None:
            return None if value is None else float(value)

        return (
            txn.transaction_id,
            txn.transaction_date.strftime("%Y-%m-%d %H:%M:%S") if txn.transaction_date else None,
            txn.transaction_type,
            txn.transaction_category,
            num(txn.amount),
            txn.currency,
            num(txn.amount_usd),
            txn.channel,
            txn.merchant_name,
            txn.merchant_category,
            txn.transaction_country,
            txn.transaction_city,
            txn.transaction_status,
        )

    def query(self, sql: str) -> QueryResult:
        if not sql.strip():
            raise SandboxError("empty query")
        if len(sql) > MAX_SQL_CHARS:
            raise SandboxError(f"query longer than {MAX_SQL_CHARS} characters")
        if _NOW.search(sql):
            raise SandboxError("use today() for the current date; 'now' is not the date of the data")
        self._deadline = time.monotonic() + DEADLINE_S
        try:
            cursor = self._con.execute(sql)  # one statement only: sqlite3 refuses several
            fetched = cursor.fetchmany(MAX_ROWS + 1)
        except sqlite3.Error as exc:
            raise SandboxError(_explain(exc)) from exc
        columns = tuple(d[0] for d in (cursor.description or ()))
        rows = tuple(tuple(_cell(v) for v in row) for row in fetched[:MAX_ROWS])
        return QueryResult(columns=columns, rows=rows, truncated=len(fetched) > MAX_ROWS)

    def close(self) -> None:
        self._con.close()


def _explain(exc: sqlite3.Error) -> str:
    message = str(exc)
    if "not authorized" in message or "authoriz" in message:
        return "not allowed: only SELECT on the transactions table, with plain functions"
    if "interrupted" in message:
        return "the query took too long: make it simpler"
    return message
