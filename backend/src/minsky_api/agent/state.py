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
    OFFER_HANDOFF = "offer_handoff"
    DONE = "done"


@dataclass(frozen=True)
class Terminal:
    """How a conversation ended, read from what the tools returned, never from what the model wrote.

    Once the phase is DONE every further message is answered from this record by code (`wording.terminal_reply`):
    the status, the reference, and that a new charge needs a new conversation.
    """

    outcome: str  # dispute_opened | handoff | informed | cancelled | no_case
    reference: str | None = None  # the handoff id, or the dispute id when outcome is dispute_opened
    card_blocked: bool = False
    dispute_id: str | None = None  # a dispute already open next to a handoff (informed: the existing one)


@dataclass
class ConversationState:
    conversation_id: UUID
    customer_id: str
    phase: Phase = Phase.UNDERSTAND
    terminal: Terminal | None = None  # set exactly when phase is DONE
    # The customer said yes to "is this the charge you mean?". No handoff is created before that, except one the
    # customer asked for (`handoff_accepted`) or the one for a model outage (agent/degraded.py).
    txn_confirmed: bool = False
    handoff_accepted: bool = False
    handoff_offers: int = 0  # times a human agent was offered because no charge could be identified
    offer_reason: str | None = None  # why: out_of_scope, clarify_exhausted, unclear_confirmation
    turn_count: int = 0
    clarify_count: int = 0
    # Replies in a row that were not a plain yes or no to the pending question. Reset when a new question is asked.
    unclear_count: int = 0
    candidate_txn_ids: list[str] = field(default_factory=list)
    selected_txn_id: str | None = None
    selected_product_id: str | None = None
    selected_type: str | None = None  # Purchase, Transfer, ...: how the customer names it
    rule_id: str | None = None
    route: str | None = None
    customer_says_not_me: bool = False
    dispute_reason: str = "unspecified"  # set from the router only when it is confident
    # The learned router's reading of the first message (a suggestion for the back office, never a decision).
    router_label: str | None = None
    router_confidence: float | None = None
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
