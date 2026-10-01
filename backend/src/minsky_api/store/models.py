"""SQLModel rows for bank.* read models. Aligned to pipeline/transform/models/gold/_gold.yml.

The pipeline owns DDL and schema swaps; the API never create_all on bank.

Timestamps in bank.* are `timestamp without time zone`: local times of the source, with no zone.
They are mapped naive (`_local_timestamp`); SQLModel's default would label them as UTC.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel


def _local_timestamp() -> Any:
    return Field(default=None, sa_type=DateTime(timezone=False))


class Customer(SQLModel, table=True):
    __tablename__: ClassVar[Any] = "customers"
    __table_args__: ClassVar[Any] = {"schema": "bank"}

    customer_id: str = Field(primary_key=True)
    first_name: str | None = None
    country: str | None = None
    segment: str | None = None
    customer_status: str | None = None
    detected_accent: str | None = None


class Product(SQLModel, table=True):
    __tablename__: ClassVar[Any] = "products"
    __table_args__: ClassVar[Any] = {"schema": "bank"}

    product_id: str = Field(primary_key=True)
    customer_id: str
    product_type: str | None = None
    is_card: bool | None = None
    product_number_last4: str | None = None
    currency: str | None = None
    product_status: str | None = None
    opening_date: date | None = None
    expiration_date: date | None = None
    has_linked_app: bool | None = None


class Transaction(SQLModel, table=True):
    __tablename__: ClassVar[Any] = "transactions"
    __table_args__: ClassVar[Any] = {"schema": "bank"}

    transaction_id: str = Field(primary_key=True)
    customer_id: str
    product_id: str
    transaction_date: datetime | None = _local_timestamp()
    process_date: date | None = None
    transaction_type: str | None = None
    transaction_category: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    amount_usd: Decimal
    amount_usd_source: str
    channel: str | None = None
    merchant_name: str | None = None
    merchant_category: str | None = None
    transaction_country: str | None = None
    transaction_city: str | None = None
    transaction_status: str | None = None
    response_code: str | None = None
    is_fraud: bool | None = None
    fraud_score: Decimal | None = None


class CustomerComplaintStats(SQLModel, table=True):
    __tablename__: ClassVar[Any] = "customer_complaint_stats"
    __table_args__: ClassVar[Any] = {"schema": "bank"}

    customer_id: str = Field(primary_key=True)
    complaints_total: int | None = None
    complaints_last_90d: int | None = None
    last_complaint_at: datetime | None = _local_timestamp()
    is_repeat_complainer: bool | None = None


class ResolutionBenchmark(SQLModel, table=True):
    __tablename__: ClassVar[Any] = "resolution_benchmarks"
    __table_args__: ClassVar[Any] = {"schema": "bank"}

    category: str = Field(primary_key=True)
    priority: str = Field(primary_key=True)
    cases: int | None = None
    resolved_cases: int | None = None
    median_resolution_days: float | None = None
    p75_resolution_days: float | None = None
    sla_breach_rate: float | None = None
    rejection_rate: float | None = None


class DisputeScenario(SQLModel, table=True):
    __tablename__: ClassVar[Any] = "dispute_scenarios"
    __table_args__: ClassVar[Any] = {"schema": "bank"}

    rule_id: str = Field(primary_key=True)
    customer_says_not_me: bool = Field(primary_key=True)
    transaction_id: str = Field(primary_key=True)
    route: str | None = None
    offer_card_block: bool | None = None
    customer_id: str
    transaction_status: str | None = None
    transaction_date: datetime | None = _local_timestamp()
    days_before_as_of: int | None = None
    amount_usd: Decimal | None = None
    is_fraud: bool | None = None
    fraud_score: Decimal | None = None
    is_repeat_complainer: bool | None = None
