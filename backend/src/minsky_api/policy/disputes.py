"""Dispute policy: which disputes the system may open on its own, and which go elsewhere.

Pure functions over verified facts: no I/O, no model calls. Every decision carries the id of
the rule that produced it, so replies, handoffs and traces can cite it. The rules are a
SYNTHETIC policy written for the hackathon (docs/dispute_policy.md); they do not reproduce any
real bank's rules. Rules are evaluated in order; the first match wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class TxnStatus(StrEnum):
    APPROVED = "Approved"
    DECLINED = "Declined"
    PENDING = "Pending"
    REVERSED = "Reversed"


class Route(StrEnum):
    OPEN_DISPUTE = "open_dispute"  # automatic intake
    INFORM = "inform"  # nothing to dispute; explain why
    ABSTAIN = "abstain"  # cannot decide yet; explain what is missing
    REFUSE = "refuse"  # not disputable under the policy; offer a human
    ESCALATE_FRAUD = "escalate_fraud"  # fraud team; offer a card block first
    ESCALATE_AGENT = "escalate_agent"  # dispute agent


@dataclass(frozen=True)
class PolicyConfig:
    dispute_window_days: int = 120
    auto_limit_usd: float = 500.0
    fraud_score_threshold: float = 80.0


@dataclass(frozen=True)
class DisputeFacts:
    """Verified facts only: read from the customer's own records, never from the conversation."""

    status: TxnStatus
    transaction_date: date
    amount_usd: float
    fraud_score: float | None
    existing_dispute_ref: str | None
    repeat_complainer: bool
    customer_says_not_me: bool  # the customer's claim, confirmed back to them


@dataclass(frozen=True)
class Decision:
    route: Route
    rule_id: str
    offer_card_block: bool = False


DEFAULT_CONFIG = PolicyConfig()


def decide(facts: DisputeFacts, today: date, config: PolicyConfig = DEFAULT_CONFIG) -> Decision:
    if facts.status == TxnStatus.DECLINED:
        return Decision(Route.INFORM, "D01-declined-not-charged")
    if facts.status == TxnStatus.REVERSED:
        return Decision(Route.INFORM, "D02-already-reversed")
    if facts.status == TxnStatus.PENDING:
        return Decision(Route.ABSTAIN, "D03-pending-not-posted")
    if facts.existing_dispute_ref:
        return Decision(Route.INFORM, "D04-already-disputed")
    if (today - facts.transaction_date).days > config.dispute_window_days:
        return Decision(Route.REFUSE, "D05-outside-window")
    if facts.customer_says_not_me or (facts.fraud_score or 0) >= config.fraud_score_threshold:
        return Decision(Route.ESCALATE_FRAUD, "D06-possible-fraud", offer_card_block=True)
    if facts.amount_usd > config.auto_limit_usd:
        return Decision(Route.ESCALATE_AGENT, "D07-above-auto-limit")
    if facts.repeat_complainer:
        return Decision(Route.ESCALATE_AGENT, "D08-repeat-complainer")
    return Decision(Route.OPEN_DISPUTE, "D09-eligible")
