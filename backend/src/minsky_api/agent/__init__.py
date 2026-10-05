"""Orchestrator: the dispute state machine (docs/solution.md, steps 1-8).

Owns every decision; calls the LLM extract step, policy and tools.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from minsky_api.agent.memory import ConversationStore
    from minsky_api.agent.orchestrator import run_turn
    from minsky_api.agent.state import ConversationState, Phase

__all__ = [
    "ConversationState",
    "ConversationStore",
    "Phase",
    "run_turn",
]


def __getattr__(name: str) -> Any:
    # Lazy exports avoid confirm → agent → orchestrator → confirm at import time.
    if name in {"ConversationState", "Phase"}:
        from minsky_api.agent import state as _state

        return getattr(_state, name)
    if name == "ConversationStore":
        from minsky_api.agent.memory import ConversationStore

        return ConversationStore
    if name == "run_turn":
        from minsky_api.agent.orchestrator import run_turn

        return run_turn
    raise AttributeError(name)
