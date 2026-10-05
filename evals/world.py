"""One trial's bank rows, built from the case the simulator knows and the agent does not.

`user_scenario.known_info` is the only place those facts live. When `label_source` is `policy`,
the trial refuses to start unless `policy.disputes.decide` returns the case's rule and outcome.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from evals.fixtures import FixtureBank
from evals.schema import Case, Outcome
from minsky_api.config import get_settings
from minsky_api.policy.disputes import DisputeFacts, Route, TxnStatus, decide
from minsky_api.store.models import CustomerComplaintStats, Product, Transaction

_BOOL = {"true": True, "false": False}

_ROUTE_OUTCOME = {
    Route.OPEN_DISPUTE: Outcome.RESOLVE,
    Route.INFORM: Outcome.RESOLVE,
    Route.ABSTAIN: Outcome.ABSTAIN,
    Route.REFUSE: Outcome.REFUSE,
    Route.ESCALATE_FRAUD: Outcome.ESCALATE,
    Route.ESCALATE_AGENT: Outcome.ESCALATE,
}


@dataclass(frozen=True)
class WorldFacts:
    """Rows and the policy label the case is allowed to expect."""

    label_source: str
    rule_id: str | None
    transaction_id: str | None
    product_id: str | None
    merchant: str | None
    amount: Decimal | None
    amount_usd: Decimal | None
    currency: str | None
    transaction_status: str | None
    transaction_date: datetime | None
    is_fraud: bool
    is_repeat_complainer: bool
    customer_says_not_me: bool
    other_customer_id: str | None
    other_transaction_id: str | None
    existing_dispute: bool = False  # the trial starts with a dispute already open on the transaction (rule D04)


class MemoryBank(FixtureBank):
    """Per-trial SQL world honoring the real read-store predicates, ordering, and limits."""

    def __init__(
        self,
        transactions: list[Transaction],
        stats: dict[str, CustomerComplaintStats],
        products: dict[str, Product],
        *,
        customer_id: str,
    ) -> None:
        self.customer_id = customer_id
        super().__init__([*transactions, *stats.values(), *products.values()])


def facts_from_case(case: Case) -> WorldFacts:
    info = case.user_scenario.known_info
    label_source = info.get("label_source", "policy")
    if label_source not in {"policy", "tool_denial", "authentication", "data"}:
        raise ValueError(f"{case.id}: label_source must be policy, tool_denial, authentication or data")
    if label_source == "tool_denial":
        _require(info, "other_customer_id", "other_transaction_id")
    elif label_source == "policy":
        _require(
            info,
            "rule_id",
            "transaction_id",
            "product_id",
            "amount",
            "amount_usd",
            "currency",
            "transaction_status",
            "transaction_date",
            "is_fraud",
            "is_repeat_complainer",
            "customer_says_not_me",
        )
    merchant = info.get("merchant") or None
    return WorldFacts(
        label_source=label_source,
        rule_id=info.get("rule_id") or None,
        transaction_id=info.get("transaction_id") or None,
        product_id=info.get("product_id") or None,
        merchant=merchant,
        amount=_decimal(info.get("amount")) if info.get("amount") else None,
        amount_usd=_decimal(info.get("amount_usd")) if info.get("amount_usd") else None,
        currency=info.get("currency") or None,
        transaction_status=info.get("transaction_status") or None,
        transaction_date=_when(info.get("transaction_date")) if info.get("transaction_date") else None,
        is_fraud=_flag(info["is_fraud"]) if "is_fraud" in info else False,
        is_repeat_complainer=_flag(info["is_repeat_complainer"]) if "is_repeat_complainer" in info else False,
        customer_says_not_me=_flag(info["customer_says_not_me"]) if "customer_says_not_me" in info else False,
        other_customer_id=info.get("other_customer_id") or None,
        other_transaction_id=info.get("other_transaction_id") or None,
        existing_dispute=_flag(info["existing_dispute"]) if "existing_dispute" in info else False,
    )


def check_label(case: Case, facts: WorldFacts) -> None:
    """The expected outcome has to be what the policy (or the ownership rule) says, not a guess."""
    expected = case.evaluation_criteria.expected_outcome
    if facts.label_source == "authentication":
        if expected != Outcome.REFUSE:
            raise ValueError("authentication denial must expect refuse")
        return
    if facts.label_source == "data":
        # Ambiguity is a fact of the customer's records: several charges match what they said.
        if expected != Outcome.CLARIFY or int(case.user_scenario.known_info.get("matching_charges", "0")) < 2:
            raise ValueError(f"{case.id}: a data-labeled case must expect clarify over two or more matching charges")
        return
    if facts.label_source == "tool_denial":
        if expected != Outcome.CLARIFY:
            raise ValueError(f"{case.id}: a transaction the customer does not own must expect clarify, not {expected}")
        return
    decision = decide(_dispute_facts(facts), get_settings().today)
    if decision.rule_id != facts.rule_id:
        raise ValueError(f"{case.id}: policy decides {decision.rule_id}, case says {facts.rule_id}")
    if case.llm_faults:
        # Injected provider outage: facts/rule stay policy-true; only the graded outcome is escalate.
        if expected != Outcome.ESCALATE:
            raise ValueError(f"{case.id}: an llm_faults case must expect escalate")
        return
    from_policy = _ROUTE_OUTCOME[decision.route]
    if expected != from_policy:
        raise ValueError(f"{case.id}: policy route {decision.route} expects {from_policy}, case says {expected}")


GOLD = Path(__file__).resolve().parents[1] / "data" / "lake" / "gold"


def _gold_world(customer_id: str) -> MemoryBank:
    """The customer's real records from the gold Parquet (make gold): search runs over their history."""
    if not (GOLD / "transactions.parquet").is_file():
        raise ValueError("world: gold needs data/lake/gold (make silver and make gold)")
    con = duckdb.connect()

    def rows(table: str) -> list[dict[str, Any]]:
        cursor = con.execute(f"select * from '{GOLD}/{table}.parquet' where customer_id = ?", [customer_id])
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]

    transactions = [Transaction(**row) for row in rows("transactions")]
    products = {row["product_id"]: Product(**row) for row in rows("products")}
    stats = {row["customer_id"]: CustomerComplaintStats(**row) for row in rows("customer_complaint_stats")}
    con.close()
    if not transactions:
        raise ValueError(f"{customer_id}: no gold transactions")
    return MemoryBank(transactions, stats, products, customer_id=customer_id)


def build_bank(case: Case, facts: WorldFacts) -> MemoryBank:
    customer_id = case.session.customer_id
    if not customer_id:
        raise ValueError(f"{case.id}: customer_id is required")
    if case.user_scenario.known_info.get("world") == "gold":
        return _gold_world(customer_id)
    transactions: list[Transaction] = []
    products: dict[str, Product] = {}
    stats: dict[str, CustomerComplaintStats] = {}
    if facts.transaction_id and facts.product_id and facts.amount is not None and facts.amount_usd is not None:
        transactions.append(_transaction(customer_id, facts))
        products[facts.product_id] = _card(customer_id, facts.product_id)
        stats[customer_id] = CustomerComplaintStats(
            customer_id=customer_id, is_repeat_complainer=facts.is_repeat_complainer
        )
    if facts.other_transaction_id and facts.other_customer_id:
        transactions.append(
            Transaction(
                transaction_id=facts.other_transaction_id,
                customer_id=facts.other_customer_id,
                product_id="PRD-OTHER",
                amount=Decimal("10.00"),
                currency="USD",
                amount_usd=Decimal("10.00"),
                amount_usd_source="native_usd",
                transaction_date=datetime(2026, 6, 1, 12, 0),
                merchant_name="Otro",
                transaction_status="Approved",
                is_fraud=False,
                fraud_score=Decimal("1.00"),
            )
        )
    for extra in json.loads(case.user_scenario.known_info.get("extra_transactions", "[]")):
        row = Transaction.model_validate(
            {
                **_transaction(customer_id, facts).model_dump(),
                "transaction_id": extra["transaction_id"],
                "merchant_name": extra["merchant"],
                "amount": Decimal(extra["amount"]),
                "amount_usd": Decimal(extra["amount"]),
            }
        )
        transactions.append(row)
    if not transactions and facts.label_source != "authentication":
        raise ValueError(f"{case.id}: known_info has no transaction to put in the world")
    return MemoryBank(transactions, stats, products, customer_id=customer_id)


def _dispute_facts(facts: WorldFacts) -> DisputeFacts:
    if facts.transaction_status is None or facts.transaction_date is None or facts.amount_usd is None:
        raise ValueError("policy label requires transaction_status, transaction_date and amount_usd")
    return DisputeFacts(
        status=TxnStatus(facts.transaction_status),
        transaction_date=facts.transaction_date.date(),
        amount_usd=float(facts.amount_usd),
        is_fraud=facts.is_fraud,
        existing_dispute_ref="DSP-existing" if facts.existing_dispute else None,
        repeat_complainer=facts.is_repeat_complainer,
        customer_says_not_me=facts.customer_says_not_me,
    )


def _transaction(customer_id: str, facts: WorldFacts) -> Transaction:
    assert facts.transaction_id and facts.product_id and facts.amount is not None and facts.amount_usd is not None
    return Transaction(
        transaction_id=facts.transaction_id,
        customer_id=customer_id,
        product_id=facts.product_id,
        amount=facts.amount,
        currency=facts.currency or "USD",
        amount_usd=facts.amount_usd,
        amount_usd_source="native_usd",
        transaction_date=facts.transaction_date,
        merchant_name=facts.merchant,
        transaction_status=facts.transaction_status,
        is_fraud=facts.is_fraud,
        fraud_score=Decimal("95.00") if facts.is_fraud else Decimal("1.00"),
    )


def _card(customer_id: str, product_id: str) -> Product:
    return Product(
        product_id=product_id,
        customer_id=customer_id,
        is_card=True,
        product_number_last4="0000",
        product_status="Active",
    )


def _require(info: dict[str, str], *keys: str) -> None:
    missing = [key for key in keys if not info.get(key)]
    if missing:
        raise ValueError(f"known_info missing {', '.join(missing)}")


def _flag(raw: str) -> bool:
    try:
        return _BOOL[raw.strip().lower()]
    except KeyError as error:
        raise ValueError(f"expected true or false, got {raw!r}") from error


def _decimal(raw: str | None) -> Decimal:
    if raw is None:
        raise ValueError("missing amount")
    return Decimal(raw)


def _when(raw: str | None) -> datetime:
    if raw is None:
        raise ValueError("missing transaction_date")
    return datetime.fromisoformat(raw)
