"""bank.resolution_benchmarks reads."""

from __future__ import annotations

from minsky_api.store.base import BaseStore
from minsky_api.store.models import ResolutionBenchmark


class BenchmarkStore(BaseStore):
    async def get(self, category: str, priority: str) -> ResolutionBenchmark | None:
        return await self._get(ResolutionBenchmark, (category, priority))
