"""Demo operator endpoints (ADR 0015): choose the customer a demo conversation runs as.

Off unless MINSKY_DEMO_OPERATOR_ENABLED is set, and refused outside the local and demo environments at startup.
An operator credential proves who is asking; the customer id in the body only says which customer to chat as, and
is checked against bank.customers. Every choice is written to the audit trail with the operator and the customer,
and the session it returns carries the operator in its id, so the actions taken in it name who made them.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from minsky_api.api.contracts import ErrorCode, ErrorResponse
from minsky_api.config import get_settings
from minsky_api.identity.demo_sessions import DemoSessionLimit, DemoSessionStore
from minsky_api.identity.errors import PermissionDenied
from minsky_api.identity.operator import resolve_operator
from minsky_api.store.cases import CasesBackend
from minsky_api.store.customers import CustomerStore
from minsky_api.store.db import session
from minsky_api.store.errors import StoreError

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


class SessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    credential: str
    customer_id: str
    first_name: str | None
    country: str | None
    operator_id: str
    expires_at: str


def _enabled() -> bool:
    return get_settings().demo_operator_enabled


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
    except StoreError:
        logger.warning("demo session: customer lookup failed", exc_info=True)
        return _error(ErrorCode.SERVICE_UNAVAILABLE, "the customer lookup is unavailable", 503)
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
    return SessionResponse(
        credential=token,
        customer_id=customer.customer_id,
        first_name=customer.first_name,
        country=customer.country,
        operator_id=operator.operator_id,
        expires_at=issued.expires_at.isoformat(),
    )
