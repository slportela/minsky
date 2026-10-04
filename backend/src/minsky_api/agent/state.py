"""Conversation phase and mutable state for the dispute orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from minsky_api.agent.extract import DisputeDetails


class Phase(StrEnum):
    """Phases that wait for the next customer message (internal tool steps happen inside a turn)."""

    UNDERSTAND = "understand"
    CLARIFY = "clarify"
    CONFIRM_TXN = "confirm_txn"
    CONFIRM_ACT = "confirm_act"
    CARD_OFFER = "card_offer"
    DONE = "done"


@dataclass
class ConversationState:
    conversation_id: UUID
    customer_id: str
    phase: Phase = Phase.UNDERSTAND
    turn_count: int = 0
    clarify_count: int = 0
    candidate_txn_ids: list[str] = field(default_factory=list)
    selected_txn_id: str | None = None
    selected_product_id: str | None = None
    rule_id: str | None = None
    route: str | None = None
    customer_says_not_me: bool = False
    dispute_reason: str = "unrecognized_charge"
    # Set from the first customer message; later turns reuse it.
    language: str | None = None
    # Model decision for the confirm turn in progress (yes, no, unclear). Code acts only on yes.
    confirmation: str | None = None
    # Acts the model chose on each turn, in order. Graders read this instead of fixed sentences.
    acts: list[str] = field(default_factory=list)
    claims_card_blocked: bool = False
    # Text of the confirmation question already sent. None unless phase is a confirm phase.
    pending_question: str | None = None
    search_details: DisputeDetails = field(default_factory=DisputeDetails)
    # Last user/agent texts for the HTTP contract (server-owned history).
    messages: list[tuple[str, str]] = field(default_factory=list)  # ("user"|"agent", text)
