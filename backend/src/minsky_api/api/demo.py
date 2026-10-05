"""Demo operator endpoints (ADR 0015): choose the customer a demo conversation runs as.

Off unless MINSKY_DEMO_OPERATOR_ENABLED is set, and refused outside the local and demo environments at startup.
An operator credential proves who is asking; the customer id in the body only says which customer to chat as, and
is checked against bank.customers. Every choice is written to the audit trail with the operator and the customer,
and the session it returns carries the operator in its id, so the actions taken in it name who made them.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from minsky_api.agent.wording import policy_reason
from minsky_api.api.contracts import ErrorCode, ErrorResponse
from minsky_api.config import get_settings
from minsky_api.identity.demo_sessions import DemoSessionLimit, DemoSessionStore
from minsky_api.identity.errors import PermissionDenied
from minsky_api.identity.operator import resolve_operator
from minsky_api.identity.session import SessionState, ToolSession
from minsky_api.store.cases import CasesBackend
from minsky_api.store.customers import CustomerStore
from minsky_api.store.db import session
from minsky_api.store.errors import StoreError
from minsky_api.tools.bank import evaluate_dispute, get_transactions
from minsky_api.tools.context import ToolContext
from minsky_api.tools.errors import ToolDenied, ToolError
from minsky_api.tools.schemas import EvaluateDisputeArgs, GetTransactionsArgs, TransactionView

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/demo", tags=["demo"])


def _error(code: ErrorCode, message: str, status_code: int) -> JSONResponse:
    body = ErrorResponse(code=code, message=message, request_id=str(uuid4()))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


class SessionRequest(BaseModel):
    """Either a customer id, or `random`: a customer with a recent charge a dispute can be opened on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    customer_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    random: bool = False

    @model_validator(mode="after")
    def _one_of(self) -> SessionRequest:
        if (self.customer_id is None) == (not self.random):
            raise ValueError("send either customer_id or random=true")
        return self


class OperatorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operator_id: str


class ChargeView(BaseModel):
    """A recent charge of the chosen customer, with what the policy would do with it. Read through the tools."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transaction_id: str
    merchant: str | None
    amount: str | None
    currency: str | None
    amount_usd: str
    date: str | None
    transaction_type: str | None
    status: str | None
    route: str  # the policy's route: open_dispute, escalate_agent, escalate_fraud, refuse, inform, abstain
    rule_id: str
    hint: str | None  # the policy's reason in plain words
    existing_dispute_id: str | None
    suggested_message: str  # what to type in the chat to dispute this charge


class SessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    credential: str
    customer_id: str
    first_name: str | None
    country: str | None
    operator_id: str
    expires_at: str
    # None: the charges could not be read (the session is still good). Otherwise the most recent charges inside the
    # dispute window, newest first, so whoever chose the customer knows what there is to dispute.
    recent_charges: list[ChargeView] | None


def _enabled() -> bool:
    return get_settings().demo_operator_enabled


_WINDOW_DAYS = 120
_AUTOMATIC_LIMIT_USD = Decimal("500")
_RECENT = 4  # the newest charges, whatever they are
_LIKELY = 4  # the newest posted charges under the automatic limit: the ones a dispute can usually be opened on


def _suggested_message(txn: TransactionView) -> str:
    where = f" en {txn.merchant_name}" if txn.merchant_name else ""
    when = f" del {txn.transaction_date.date().isoformat()}" if txn.transaction_date else ""
    amount = (
        f"{txn.amount:.2f} {txn.currency}" if txn.amount is not None and txn.currency else f"{txn.amount_usd:.2f} USD"
    )
    return f"Quiero reclamar un cargo de {amount}{where}{when}, el monto no es correcto."


async def _recent_charges(
    db: object, cases: CasesBackend, issued_session_id: str, customer_id: str, today: date
) -> list[ChargeView] | None:
    """Through the tool layer, as the chosen customer: the same reads and the same policy the chat uses."""
    ctx = ToolContext(
        session=ToolSession(session_id=issued_session_id, state=SessionState.VALID, customer_id=customer_id),
        db=db,  # type: ignore[arg-type]
        cases=cases,
    )
    try:
        found = await get_transactions(
            ctx, GetTransactionsArgs(limit=40, date_from=today - timedelta(days=_WINDOW_DAYS), date_to=today)
        )
        newest = list(found.transactions)  # newest first
        likely = [t for t in newest if t.transaction_status == "Approved" and t.amount_usd <= _AUTOMATIC_LIMIT_USD]
        chosen = {t.transaction_id: t for t in [*newest[:_RECENT], *likely[:_LIKELY]]}
        charges: list[ChargeView] = []
        for txn in sorted(chosen.values(), key=lambda t: t.transaction_date or datetime.min, reverse=True):
            decision = await evaluate_dispute(ctx, EvaluateDisputeArgs(transaction_id=txn.transaction_id))
            charges.append(
                ChargeView(
                    transaction_id=txn.transaction_id,
                    merchant=txn.merchant_name,
                    amount=None if txn.amount is None else f"{txn.amount:.2f}",
                    currency=txn.currency,
                    amount_usd=f"{txn.amount_usd:.2f}",
                    date=txn.transaction_date.date().isoformat() if txn.transaction_date else None,
                    transaction_type=txn.transaction_type,
                    status=txn.transaction_status,
                    route=decision.route,
                    rule_id=decision.rule_id,
                    hint=policy_reason(decision.rule_id, "es"),
                    existing_dispute_id=decision.existing_dispute_id,
                    suggested_message=_suggested_message(txn),
                )
            )
        return charges
    except (StoreError, ToolError, ToolDenied):
        logger.warning("demo session: the recent charges could not be read", exc_info=True)
        return None


def _operator(authorization: str | None):  # noqa: ANN202 - returns OperatorSession or a JSONResponse
    try:
        return resolve_operator(authorization)
    except PermissionDenied as exc:
        return _error(ErrorCode(exc.reason), "demo operator credential is missing, invalid, or expired", 401)
    except RuntimeError:
        return _error(ErrorCode.SERVICE_UNAVAILABLE, "demo operator credentials are unavailable", 503)


@router.get("/whoami", response_model=None)
async def whoami(authorization: Annotated[str | None, Header()] = None) -> OperatorResponse | JSONResponse:
    """Is this credential a demo operator's? The chat page asks before it shows the customer picker."""
    if not _enabled():
        return _error(ErrorCode.NOT_FOUND, "not found", 404)
    operator = _operator(authorization)
    if isinstance(operator, JSONResponse):
        return operator
    return OperatorResponse(operator_id=operator.operator_id)


@router.post("/session", response_model=None)
async def choose_customer(
    body: SessionRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> SessionResponse | JSONResponse:
    if not _enabled():
        return _error(ErrorCode.NOT_FOUND, "not found", 404)
    operator = _operator(authorization)
    if isinstance(operator, JSONResponse):
        return operator
    settings = get_settings()
    store: DemoSessionStore | None = getattr(request.app.state, "demo_sessions", None)
    cases: CasesBackend = request.app.state.cases
    if store is None:
        return _error(ErrorCode.SERVICE_UNAVAILABLE, "demo sessions are unavailable", 503)
    try:
        async with session() as db:
            customers = CustomerStore(db)
            customer_id = body.customer_id
            if customer_id is None:
                customer_id = await customers.random_with_recent_charge(today=settings.today)
                if customer_id is None:
                    return _error(ErrorCode.CUSTOMER_NOT_FOUND, "no customer with a recent charge was found", 404)
            customer = await customers.get(customer_id)
            if customer is None:
                return _error(ErrorCode.CUSTOMER_NOT_FOUND, "no such customer", 404)
            try:
                token, issued = store.issue(operator_id=operator.operator_id, customer_id=customer.customer_id)
            except DemoSessionLimit:
                return _error(ErrorCode.RATE_LIMITED, "too many demo sessions; wait a minute", 429)
            cases.append_audit(
                tool="demo_choose_customer",
                session_id=issued.session_id,
                customer_id=customer.customer_id,
                args_digest=hashlib.sha256(f"{operator.operator_id}:{customer.customer_id}".encode()).hexdigest(),
                outcome="ok",
                reason=f"operator={operator.operator_id}",
            )
            charges = await _recent_charges(db, cases, issued.session_id, customer.customer_id, settings.today)
    except StoreError:
        logger.warning("demo session: customer lookup failed", exc_info=True)
        return _error(ErrorCode.SERVICE_UNAVAILABLE, "the customer lookup is unavailable", 503)
    return SessionResponse(
        credential=token,
        customer_id=customer.customer_id,
        first_name=customer.first_name,
        country=customer.country,
        operator_id=operator.operator_id,
        expires_at=issued.expires_at.isoformat(),
        recent_charges=charges,
    )
