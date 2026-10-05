"""Conversation phase and mutable state for the dispute orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
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
    # Agentic mode (agent.agentic): the agent searches, then code takes over.
    SEARCH = "search"
    CONFIRM_DISPUTE = "confirm_dispute"
    RECOGNIZE = "recognize"
    OFFER_ESCALATION = "offer_escalation"


@dataclass
class ConversationState:
    conversation_id: UUID
    customer_id: str
    # "workflow": extract-then-search orchestrator. "agentic": a tool-using agent finds the transaction.
    # Fixed when the conversation starts, so a setting change never lands a conversation in the wrong phases.
    mode: str = "workflow"
    phase: Phase = Phase.UNDERSTAND
    turn_count: int = 0
    clarify_count: int = 0
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
    # Agentic mode only. The agent's own conversation (role messages, tool calls and results) in Responses
    # API item form; it is kept apart from `messages`, which is what the customer sees.
    agent_items: list[dict[str, Any]] = field(default_factory=list)
    # Ids a query result has shown: the only ones the agent may propose.
    seen_txn_ids: list[str] = field(default_factory=list)
    # Transactions the customer said were not the one: never proposed again.
    rejected_txn_ids: list[str] = field(default_factory=list)
    search_failures: int = 0
    queries_run: int = 0
    # Why the customer is being offered a person (the rule id for a denial, else a reason code).
    escalation_reason: str | None = None
    existing_dispute_id: str | None = None
    denial_text: str | None = None  # what the customer read when the dispute could not go ahead
    # The customer's own words that said yes to the card (agent.consent). The write happens one question later
    # (the recognition answer), so graders read the consent here and not from the turn that ran the tool.
    consent_text: str | None = None
