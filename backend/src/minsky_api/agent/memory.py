"""In-memory conversation store for the POC (Postgres cases.* / sessions come later)."""

from __future__ import annotations

from threading import Lock
from uuid import UUID

from minsky_api.agent.state import ConversationState


class ConversationStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._by_id: dict[UUID, ConversationState] = {}

    def get(self, conversation_id: UUID) -> ConversationState | None:
        with self._lock:
            return self._by_id.get(conversation_id)

    def put(self, state: ConversationState) -> None:
        with self._lock:
            self._by_id[state.conversation_id] = state

    def clear(self) -> None:
        with self._lock:
            self._by_id.clear()
