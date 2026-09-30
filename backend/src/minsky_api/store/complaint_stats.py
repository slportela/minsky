"""bank.customer_complaint_stats reads."""

from __future__ import annotations

from minsky_api.store.base import BaseStore
from minsky_api.store.models import CustomerComplaintStats


class ComplaintStatsStore(BaseStore):
    async def get(self, customer_id: str) -> CustomerComplaintStats | None:
        return await self._get(CustomerComplaintStats, customer_id)
