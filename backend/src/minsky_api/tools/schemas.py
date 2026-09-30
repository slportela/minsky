"""Pydantic I/O for mock bank tools. Limits and shapes are the B4 contract."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PositiveInt


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class TransactionView(_Strict):
    transaction_id: str
    customer_id: str
    product_id: str
    transaction_date: datetime | None = None
    amount: Decimal | None = None
    currency: str | None = None
    amount_usd: Decimal
    merchant_name: str | None = None
    transaction_status: str | None = None
    is_fraud: bool | None = None
    fraud_score: Decimal | None = None


class ProductView(_Strict):
    product_id: str
    customer_id: str
    product_type: str | None = None
    is_card: bool | None = None
    product_number_last4: str | None = None
    product_status: str | None = None


class GetTransactionsArgs(_Strict):
    limit: PositiveInt = Field(default=20, le=100)


class GetTransactionsResult(_Strict):
    transactions: tuple[TransactionView, ...]


class GetTransactionArgs(_Strict):
    transaction_id: str = Field(min_length=1)


class GetTransactionResult(_Strict):
    transaction: TransactionView


class OpenDisputeArgs(_Strict):
    transaction_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=200)
    confirmed: bool = False


class DisputeView(_Strict):
    dispute_id: str
    customer_id: str
    transaction_id: str
    reason: str
    status: str
    created_at: datetime


class OpenDisputeResult(_Strict):
    dispute: DisputeView


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
