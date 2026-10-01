"""bank.customers reads."""

from __future__ import annotations

from minsky_api.store.base import BaseStore
from minsky_api.store.models import Customer


class CustomerStore(BaseStore):
    async def get(self, customer_id: str) -> Customer | None:
        return await self._get(Customer, customer_id)
