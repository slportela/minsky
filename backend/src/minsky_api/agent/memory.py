"""In-memory conversation store for the POC (Postgres cases.* / sessions come later)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from threading import Lock
from uuid import UUID

from minsky_api.agent.state import ConversationState


class ConversationStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._by_id: dict[UUID, ConversationState] = {}
        self._turn_locks: dict[UUID, asyncio.Lock] = {}

    @asynccontextmanager
    async def turn(self, conversation_id: UUID) -> AsyncIterator[None]:
        """Keep history validation and turn commit in one per-conversation critical section."""
        with self._lock:
            lock = self._turn_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            yield

    def get(self, conversation_id: UUID) -> ConversationState | None:
        with self._lock:
            return deepcopy(self._by_id.get(conversation_id))

    def put(self, state: ConversationState) -> None:
        with self._lock:
            self._by_id[state.conversation_id] = deepcopy(state)

    def clear(self) -> None:
        with self._lock:
            self._by_id.clear()
