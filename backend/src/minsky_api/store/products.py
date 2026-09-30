"""bank.products reads.

Ownership (row.customer_id vs session) is enforced in tools, not here.
"""

from __future__ import annotations

from sqlmodel import col, select

from minsky_api.store.base import BaseStore
from minsky_api.store.models import Product


class ProductStore(BaseStore):
    async def get(self, product_id: str) -> Product | None:
        return await self._get(Product, product_id)

    async def list_by_customer(self, customer_id: str) -> tuple[Product, ...]:
        statement = select(Product).where(col(Product.customer_id) == customer_id)
        return await self._list(statement)
