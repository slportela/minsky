"""/api/console: the back-office case queue for dispute and fraud agents.

Agents see every case the assistant opened or handed off, ordered by triage (fraud first, then amount,
then due time), with the verified facts, what the customer said, the actions taken, the open questions
and the tool audit trail. They claim a case and resolve it with a note; the outcome is a human decision
recorded here, never a system one. Auth is a staff credential, separate from customer sessions.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from minsky_api.api.contracts import ErrorCode, ErrorResponse
from minsky_api.identity.errors import PermissionDenied
from minsky_api.identity.staff import StaffSession, resolve_staff
from minsky_api.store.cases import CasesBackend
from minsky_api.store.cases_memory import CaseRecord, CaseTransitionError

router = APIRouter(prefix="/api/console", tags=["console"])
CONVERSATION_WINDOW = timedelta(minutes=30)  # longer than any conversation within the turn budget


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CaseSummary(_Out):
    case_id: str
    kind: str
    priority: str
    queue: str
    status: str
    summary: str
    rule_id: str | None
    due_at: datetime
    overdue: bool
    assigned_to: str | None
    created_at: datetime
    amount_usd: str | None
    merchant: str | None


class QueueStats(_Out):
    open_cases: int
    overdue: int
    by_priority: dict[str, int]
    by_queue: dict[str, int]


class CaseList(_Out):
    agent_id: str
    stats: QueueStats
    cases: tuple[CaseSummary, ...]


class AuditEntry(_Out):
    tool: str
    outcome: str
    reason: str | None
    at: datetime


class CaseDetail(_Out):
    case: CaseSummary
    customer_id: str
    reason: str
    triage_reason: str
    facts: dict[str, Any]
    actions: tuple[str, ...]
    open_questions: tuple[str, ...]
    expected_resolution_days: float | None
    resolution_note: str | None
    audit: tuple[AuditEntry, ...]


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=3, max_length=2000)


def _error(code: ErrorCode, message: str, status_code: int) -> JSONResponse:
    body = ErrorResponse(code=code, message=message, request_id=str(uuid4()))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _staff(authorization: str | None) -> StaffSession | JSONResponse:
    try:
        return resolve_staff(authorization)
    except PermissionDenied as exc:
        return _error(ErrorCode(exc.reason), "staff credential is missing, invalid, or expired", 401)
    except RuntimeError:
        return _error(ErrorCode.SERVICE_UNAVAILABLE, "staff credentials are unavailable", 503)


def _summary(record: CaseRecord, now: datetime) -> CaseSummary:
    verified = record.facts.get("verified") or {}
    return CaseSummary(
        case_id=record.case_id,
        kind=record.kind,
        priority=record.priority,
        queue=record.queue,
        status=record.status,
        summary=record.summary,
        rule_id=record.rule_id,
        due_at=record.due_at,
        overdue=record.status != "resolved" and record.due_at < now,
        assigned_to=record.assigned_to,
        created_at=record.created_at,
        amount_usd=verified.get("amount_usd"),
        merchant=verified.get("merchant"),
    )


def _detail(record: CaseRecord, cases: CasesBackend, now: datetime) -> CaseDetail:
    # The case's own trail: the customer's tool calls in the conversation window that produced it, including
    # the write that created the case (audited just after it).
    window_start, window_end = record.created_at - CONVERSATION_WINDOW, record.created_at + timedelta(minutes=1)
    audit = tuple(
        AuditEntry(tool=a.tool, outcome=a.outcome, reason=a.reason, at=a.at)
        for a in cases.list_audit_for_customer(record.customer_id)
        if window_start <= a.at <= window_end
    )[-25:]
    return CaseDetail(
        case=_summary(record, now),
        customer_id=record.customer_id,
        reason=record.reason,
        triage_reason=record.triage_reason,
        facts=record.facts,
        actions=record.actions,
        open_questions=record.open_questions,
        expected_resolution_days=record.expected_resolution_days,
        resolution_note=record.resolution_note,
        audit=audit,
    )


@router.get("/cases", response_model=None)
async def list_cases(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    status: Annotated[str | None, Query(pattern="^(new|in_progress|resolved)$")] = None,
    queue: Annotated[str | None, Query(pattern="^(fraud|disputes|general)$")] = None,
) -> CaseList | JSONResponse:
    staff = _staff(authorization)
    if isinstance(staff, JSONResponse):
        return staff
    cases: CasesBackend = request.app.state.cases
    now = datetime.now(UTC)
    # The case store is synchronous: keep its calls off the event loop.
    records = await asyncio.to_thread(cases.list_cases, status=status, queue=queue)
    open_records = [r for r in await asyncio.to_thread(cases.list_cases) if r.status != "resolved"]
    stats = QueueStats(
        open_cases=len(open_records),
        overdue=sum(1 for r in open_records if r.due_at < now),
        by_priority={p: sum(1 for r in open_records if r.priority == p) for p in ("Critical", "High", "Medium", "Low")},
        by_queue={q: sum(1 for r in open_records if r.queue == q) for q in ("fraud", "disputes", "general")},
    )
    return CaseList(agent_id=staff.agent_id, stats=stats, cases=tuple(_summary(r, now) for r in records))


@router.get("/cases/{case_id}", response_model=None)
async def get_case(
    case_id: str, request: Request, authorization: Annotated[str | None, Header()] = None
) -> CaseDetail | JSONResponse:
    staff = _staff(authorization)
    if isinstance(staff, JSONResponse):
        return staff
    cases: CasesBackend = request.app.state.cases
    record = await asyncio.to_thread(cases.get_case, case_id)
    if record is None:
        return _error(ErrorCode.CASE_NOT_FOUND, "unknown case_id", 404)
    return await asyncio.to_thread(_detail, record, cases, datetime.now(UTC))


@router.post("/cases/{case_id}/claim", response_model=None)
async def claim_case(
    case_id: str, request: Request, authorization: Annotated[str | None, Header()] = None
) -> CaseDetail | JSONResponse:
    staff = _staff(authorization)
    if isinstance(staff, JSONResponse):
        return staff
    cases: CasesBackend = request.app.state.cases
    try:
        record = await asyncio.to_thread(cases.claim_case, case_id, staff.agent_id)
    except CaseTransitionError as exc:
        return _error(ErrorCode.CASE_CONFLICT, str(exc), 409)
    if record is None:
        return _error(ErrorCode.CASE_NOT_FOUND, "unknown case_id", 404)
    return await asyncio.to_thread(_detail, record, cases, datetime.now(UTC))


@router.post("/cases/{case_id}/resolve", response_model=None)
async def resolve_case(
    case_id: str,
    body: ResolveRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> CaseDetail | JSONResponse:
    staff = _staff(authorization)
    if isinstance(staff, JSONResponse):
        return staff
    cases: CasesBackend = request.app.state.cases
    try:
        record = await asyncio.to_thread(cases.resolve_case, case_id, staff.agent_id, body.note.strip())
    except CaseTransitionError as exc:
        return _error(ErrorCode.CASE_CONFLICT, str(exc), 409)
    if record is None:
        return _error(ErrorCode.CASE_NOT_FOUND, "unknown case_id", 404)
    return await asyncio.to_thread(_detail, record, cases, datetime.now(UTC))
