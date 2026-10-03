"""Pydantic I/O for mock bank tools. Limits and shapes are the B4 contract.

What reaches the model is minimal: TransactionView carries what a customer would recognise a charge
by, not the fraud flag, the fraud score or ids of other people. The policy reads those facts itself,
in code (see evaluate_dispute / open_dispute).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class TransactionView(_Strict):
    transaction_id: str
    product_id: str
    transaction_date: datetime | None = None  # local time of the transaction, no zone
    transaction_type: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    amount_usd: Decimal
    merchant_name: str | None = None
    transaction_status: str | None = None


class ProductView(_Strict):
    product_id: str
    customer_id: str
    product_type: str | None = None
    is_card: bool | None = None
    product_number_last4: str | None = None
    product_status: str | None = None


class GetTransactionsArgs(_Strict):
    """Latest transactions of the session customer, optionally narrowed the way a customer describes a charge."""

    limit: PositiveInt = Field(default=20, le=100)
    merchant: str | None = Field(default=None, min_length=1, max_length=100)  # case-insensitive substring
    min_amount: Decimal | None = Field(default=None, ge=0)  # in the transaction's own currency
    max_amount: Decimal | None = Field(default=None, ge=0)
    date_from: date | None = None
    date_to: date | None = None  # inclusive

    @model_validator(mode="after")
    def _ranges(self) -> GetTransactionsArgs:
        if self.min_amount is not None and self.max_amount is not None and self.min_amount > self.max_amount:
            raise ValueError("min_amount must not exceed max_amount")
        if self.date_from is not None and self.date_to is not None and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class GetTransactionsResult(_Strict):
    transactions: tuple[TransactionView, ...]


class GetTransactionArgs(_Strict):
    transaction_id: str = Field(min_length=1)


class GetTransactionResult(_Strict):
    transaction: TransactionView


class EvaluateDisputeArgs(_Strict):
    transaction_id: str = Field(min_length=1)
    customer_says_not_me: bool = False  # the customer's claim, confirmed back to them


class EvaluateDisputeResult(_Strict):
    """The policy's decision for one of the customer's transactions (docs/dispute_policy.md)."""

    transaction_id: str
    rule_id: str
    route: str
    offer_card_block: bool
    existing_dispute_id: str | None = None


class OpenDisputeArgs(_Strict):
    transaction_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=200)
    confirmed: bool = False
    customer_says_not_me: bool = False


class DisputeView(_Strict):
    dispute_id: str
    customer_id: str
    transaction_id: str
    reason: str
    status: str
    created_at: datetime


class OpenDisputeResult(_Strict):
    dispute: DisputeView
    created: bool  # False: the dispute already existed (idempotent call); tell the customer its reference


class GetDisputeArgs(_Strict):
    dispute_id: str = Field(min_length=1)


class GetDisputeResult(_Strict):
    dispute: DisputeView


class BlockCardArgs(_Strict):
    product_id: str = Field(min_length=1)
    confirmed: bool = False


class CardBlockView(_Strict):
    product_id: str
    customer_id: str
    status: str
    blocked_at: datetime


class BlockCardResult(_Strict):
    block: CardBlockView


class CreateHandoffArgs(_Strict):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=200)
    rule_id: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    actions: tuple[str, ...] = ()


class HandoffView(_Strict):
    handoff_id: str
    customer_id: str
    reason: str
    rule_id: str | None
    facts: dict[str, Any]
    actions: tuple[str, ...]
    created_at: datetime


class CreateHandoffResult(_Strict):
    handoff: HandoffView


class ClassifyReplyArgs(_Strict):
    """The question the system already sent, and the customer's reply. Neither is a customer id."""

    question: str = Field(min_length=1)
    text: str = Field(min_length=1)


class ClassifyReplyResult(_Strict):
    decision: Literal["yes", "no", "unclear"]
