"""bank.transactions reads.

Ownership (row.customer_id vs session) is enforced in tools, not here.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlmodel import col, select

from minsky_api.store.base import BaseStore
from minsky_api.store.models import Transaction

_MIN_LIMIT = 1
_MAX_LIMIT = 100


def _like_pattern(text: str) -> str:
    """Substring pattern for ILIKE, with the LIKE wildcards in the input escaped."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class TransactionStore(BaseStore):
    async def get(self, transaction_id: str) -> Transaction | None:
        return await self._get(Transaction, transaction_id)

    async def list_by_customer(
        self,
        customer_id: str,
        *,
        limit: int,
        merchant: str | None = None,
        min_amount: Decimal | None = None,
        max_amount: Decimal | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[Transaction, ...]:
        """A customer's latest transactions, optionally narrowed the way a customer describes a charge.

        `merchant` is a case-insensitive substring; amounts are in the transaction's own currency
        (what the customer sees); `date_to` is inclusive.
        """
        if not _MIN_LIMIT <= limit <= _MAX_LIMIT:
            raise ValueError(f"limit must be between {_MIN_LIMIT} and {_MAX_LIMIT}")
        statement = select(Transaction).where(col(Transaction.customer_id) == customer_id)
        if merchant:
            statement = statement.where(col(Transaction.merchant_name).ilike(_like_pattern(merchant), escape="\\"))
        if min_amount is not None:
            statement = statement.where(col(Transaction.amount) >= min_amount)
        if max_amount is not None:
            statement = statement.where(col(Transaction.amount) <= max_amount)
        if date_from is not None:
            statement = statement.where(col(Transaction.transaction_date) >= date_from)
        if date_to is not None:
            statement = statement.where(col(Transaction.transaction_date) < date_to + timedelta(days=1))
        statement = statement.order_by(col(Transaction.transaction_date).desc()).limit(limit)
        return await self._list(statement)
