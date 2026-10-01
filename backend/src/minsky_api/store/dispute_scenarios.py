"""bank.dispute_scenarios reads (evals / demo)."""

from __future__ import annotations

from sqlmodel import col, select

from minsky_api.store.base import BaseStore
from minsky_api.store.models import DisputeScenario


class DisputeScenarioStore(BaseStore):
    async def list_for_rule(self, rule_id: str) -> tuple[DisputeScenario, ...]:
        statement = select(DisputeScenario).where(col(DisputeScenario.rule_id) == rule_id)
        return await self._list(statement)

    async def list_all(self) -> tuple[DisputeScenario, ...]:
        return await self._list(select(DisputeScenario))
