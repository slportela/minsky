"""bank.transactions reads.

Ownership (row.customer_id vs session) is enforced in tools, not here.
"""

from __future__ import annotations

from sqlmodel import col, select

from minsky_api.store.base import BaseStore
from minsky_api.store.models import Transaction

_MIN_LIMIT = 1
_MAX_LIMIT = 100


class TransactionStore(BaseStore):
    async def get(self, transaction_id: str) -> Transaction | None:
        return await self._get(Transaction, transaction_id)

    async def list_by_customer(self, customer_id: str, *, limit: int) -> tuple[Transaction, ...]:
        if not _MIN_LIMIT <= limit <= _MAX_LIMIT:
            raise ValueError(f"limit must be between {_MIN_LIMIT} and {_MAX_LIMIT}")
        statement = (
            select(Transaction)
            .where(col(Transaction.customer_id) == customer_id)
            .order_by(col(Transaction.transaction_date).desc())
            .limit(limit)
        )
        return await self._list(statement)
