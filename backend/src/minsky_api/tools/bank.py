"""Mock core-banking tools: typed, session-scoped, audited. No LangGraph.

Contracts and limits (B4):
- Every call takes ToolContext (session + db + cases); never a customer_id from the model.
- Reads use bank.* stores; writes go through the CasesBackend protocol (Postgres cases.* in compose,
  in memory in tests and offline evals).
- Every dispute opened and every handoff also queues a back-office case (tools.casework) with its
  triage, the verified transaction facts and the open questions, for the agent console.
- open_dispute and block_card require confirmed=True (orchestrator sets it after explicit YES).
- open_dispute also enforces the dispute policy itself (policy.disputes.decide on verified facts):
  only rule D09 (eligible) opens a dispute; any other rule is a denial whose reason is the rule id.
  This is the last line of defense behind the orchestrator (AGENTS rule 1).
- evaluate_dispute returns that decision without writing, so the orchestrator can route the case
  without ever seeing the fraud flag.
- Writes return only after read-back of the stored row; open_dispute says whether it created the
  dispute or it already existed.
- get_transactions limit is 1..100 (default 20), with optional customer-scoped filters.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, NoReturn, Protocol

from minsky_api.config import get_settings
from minsky_api.identity.errors import PermissionDenied
from minsky_api.identity.session import require_customer
from minsky_api.policy.disputes import Decision, DisputeFacts, Route, TxnStatus, decide
from minsky_api.policy.triage import CaseKind
from minsky_api.store.complaint_stats import ComplaintStatsStore
from minsky_api.store.errors import StoreError
from minsky_api.store.models import Transaction
from minsky_api.store.products import ProductStore
from minsky_api.store.transactions import TransactionStore
from minsky_api.tools.casework import enqueue_case
from minsky_api.tools.context import ToolContext
from minsky_api.tools.errors import ToolDenied, ToolError
from minsky_api.tools.schemas import (
    BlockCardArgs,
    BlockCardResult,
    CardBlockView,
    CreateHandoffArgs,
    CreateHandoffResult,
    DisputeView,
    EvaluateDisputeArgs,
    EvaluateDisputeResult,
    GetDisputeArgs,
    GetDisputeResult,
    GetTransactionArgs,
    GetTransactionResult,
    GetTransactionsArgs,
    GetTransactionsResult,
    HandoffView,
    OpenDisputeArgs,
    OpenDisputeResult,
    TransactionView,
)


def _args_digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _audit(
    ctx: ToolContext,
    *,
    tool: str,
    args: dict[str, Any],
    outcome: str,
    reason: str | None = None,
    customer_id: str | None = None,
) -> None:
    ctx.cases.append_audit(
        tool=tool,
        session_id=ctx.session.session_id,
        customer_id=customer_id if customer_id is not None else ctx.session.customer_id,
        args_digest=_args_digest(args),
        outcome=outcome,
        reason=reason,
    )


def _deny(
    ctx: ToolContext,
    *,
    tool: str,
    args: dict[str, Any],
    reason: str,
    customer_id: str | None = None,
) -> NoReturn:
    _audit(ctx, tool=tool, args=args, outcome="denied", reason=reason, customer_id=customer_id)
    raise ToolDenied(reason)


def _require_customer(ctx: ToolContext, *, tool: str, args: dict[str, Any]) -> str:
    try:
        return require_customer(ctx.session)
    except PermissionDenied as denied:
        _audit(ctx, tool=tool, args=args, outcome="denied", reason=denied.reason)
        raise ToolDenied(denied.reason) from denied


async def _store[T](
    ctx: ToolContext,
    *,
    tool: str,
    args: dict[str, Any],
    customer_id: str,
    call: Callable[[], Awaitable[T]],
) -> T:
    try:
        return await call()
    except StoreError as error:
        _audit(ctx, tool=tool, args=args, outcome="error", reason="store_error", customer_id=customer_id)
        raise ToolError(f"{tool} store failure") from error


class _HasCustomerId(Protocol):
    @property
    def customer_id(self) -> str: ...


def _owned[OwnedT: _HasCustomerId](
    row: OwnedT | None,
    *,
    customer_id: str,
    ctx: ToolContext,
    tool: str,
    args: dict[str, Any],
) -> OwnedT:
    if row is None:
        _deny(ctx, tool=tool, args=args, reason="not_found", customer_id=customer_id)
    if row.customer_id != customer_id:
        _deny(ctx, tool=tool, args=args, reason="not_owner", customer_id=customer_id)
    return row


async def get_transactions(ctx: ToolContext, args: GetTransactionsArgs | None = None) -> GetTransactionsResult:
    params = args or GetTransactionsArgs()
    audit_args = params.model_dump(exclude_none=True)
    customer_id = _require_customer(ctx, tool="get_transactions", args=audit_args)
    rows = await _store(
        ctx,
        tool="get_transactions",
        args=audit_args,
        customer_id=customer_id,
        call=lambda: TransactionStore(ctx.db).list_by_customer(
            customer_id,
            limit=params.limit,
            merchant=params.merchant,
            min_amount=params.min_amount,
            max_amount=params.max_amount,
            date_from=params.date_from,
            date_to=params.date_to,
        ),
    )
    result = GetTransactionsResult(transactions=tuple(TransactionView.model_validate(row) for row in rows))
    _audit(ctx, tool="get_transactions", args=audit_args, outcome="ok", customer_id=customer_id)
    return result


async def get_transaction(ctx: ToolContext, args: GetTransactionArgs) -> GetTransactionResult:
    audit_args = {"transaction_id": args.transaction_id}
    customer_id = _require_customer(ctx, tool="get_transaction", args=audit_args)
    row = await _store(
        ctx,
        tool="get_transaction",
        args=audit_args,
        customer_id=customer_id,
        call=lambda: TransactionStore(ctx.db).get(args.transaction_id),
    )
    owned = _owned(row, customer_id=customer_id, ctx=ctx, tool="get_transaction", args=audit_args)
    result = GetTransactionResult(transaction=TransactionView.model_validate(owned))
    _audit(ctx, tool="get_transaction", args=audit_args, outcome="ok", customer_id=customer_id)
    return result


async def _dispute_decision(
    ctx: ToolContext,
    *,
    tool: str,
    args: dict[str, Any],
    customer_id: str,
    txn: Transaction,
    says_not_me: bool,
) -> Decision:
    """The policy's decision on verified facts only: the bank's rows and our own cases, never the chat."""
    stats = await _store(
        ctx,
        tool=tool,
        args=args,
        customer_id=customer_id,
        call=lambda: ComplaintStatsStore(ctx.db).get(customer_id),
    )
    if stats is None or txn.transaction_status is None or txn.transaction_date is None:
        # bank.* guarantees these (one stats row per customer; status and date on every transaction)
        _audit(ctx, tool=tool, args=args, outcome="error", reason="missing_facts", customer_id=customer_id)
        raise ToolError(f"{tool}: facts missing for {txn.transaction_id}")
    existing = ctx.cases.get_dispute_by_transaction(customer_id=customer_id, transaction_id=txn.transaction_id)
    facts = DisputeFacts(
        status=TxnStatus(txn.transaction_status),
        transaction_date=txn.transaction_date.date(),
        amount_usd=float(txn.amount_usd),
        is_fraud=bool(txn.is_fraud),
        existing_dispute_ref=existing.dispute_id if existing else None,
        repeat_complainer=bool(stats.is_repeat_complainer),
        customer_says_not_me=says_not_me,
    )
    return decide(facts, get_settings().today)


async def evaluate_dispute(ctx: ToolContext, args: EvaluateDisputeArgs) -> EvaluateDisputeResult:
    """Read-only: what the policy decides for one of the customer's transactions."""
    audit_args = {"transaction_id": args.transaction_id, "customer_says_not_me": args.customer_says_not_me}
    customer_id = _require_customer(ctx, tool="evaluate_dispute", args=audit_args)
    row = await _store(
        ctx,
        tool="evaluate_dispute",
        args=audit_args,
        customer_id=customer_id,
        call=lambda: TransactionStore(ctx.db).get(args.transaction_id),
    )
    txn = _owned(row, customer_id=customer_id, ctx=ctx, tool="evaluate_dispute", args=audit_args)
    decision = await _dispute_decision(
        ctx,
        tool="evaluate_dispute",
        args=audit_args,
        customer_id=customer_id,
        txn=txn,
        says_not_me=args.customer_says_not_me,
    )
    existing = ctx.cases.get_dispute_by_transaction(customer_id=customer_id, transaction_id=txn.transaction_id)
    offer_block = decision.offer_card_block
    if offer_block:
        # Never offer what cannot be done: a transfer or a payment from an account has no card to block.
        product = await _store(
            ctx,
            tool="evaluate_dispute",
            args=audit_args,
            customer_id=customer_id,
            call=lambda: ProductStore(ctx.db).get(txn.product_id),
        )
        offer_block = product is not None and product.customer_id == customer_id and product.is_card is True
    result = EvaluateDisputeResult(
        transaction_id=txn.transaction_id,
        rule_id=decision.rule_id,
        route=decision.route.value,
        offer_card_block=offer_block,
        existing_dispute_id=existing.dispute_id if existing else None,
    )
    _audit(
        ctx, tool="evaluate_dispute", args=audit_args, outcome="ok", reason=decision.rule_id, customer_id=customer_id
    )
    return result


async def open_dispute(ctx: ToolContext, args: OpenDisputeArgs) -> OpenDisputeResult:
    audit_args = {
        "transaction_id": args.transaction_id,
        "reason": args.reason,
        "confirmed": args.confirmed,
        "customer_says_not_me": args.customer_says_not_me,
    }
    customer_id = _require_customer(ctx, tool="open_dispute", args=audit_args)
    if not args.confirmed:
        _deny(ctx, tool="open_dispute", args=audit_args, reason="not_confirmed", customer_id=customer_id)
    row = await _store(
        ctx,
        tool="open_dispute",
        args=audit_args,
        customer_id=customer_id,
        call=lambda: TransactionStore(ctx.db).get(args.transaction_id),
    )
    txn = _owned(row, customer_id=customer_id, ctx=ctx, tool="open_dispute", args=audit_args)
    existing = ctx.cases.get_dispute_by_transaction(customer_id=customer_id, transaction_id=txn.transaction_id)
    if existing is None:
        decision = await _dispute_decision(
            ctx,
            tool="open_dispute",
            args=audit_args,
            customer_id=customer_id,
            txn=txn,
            says_not_me=args.customer_says_not_me,
        )
        if decision.route != Route.OPEN_DISPUTE:
            _deny(
                ctx, tool="open_dispute", args=audit_args, reason=f"policy:{decision.rule_id}", customer_id=customer_id
            )
    record = existing or ctx.cases.create_dispute(
        customer_id=customer_id,
        transaction_id=args.transaction_id,
        reason=args.reason,
    )
    verified = ctx.cases.get_dispute(record.dispute_id)
    if verified is None:
        raise ToolError("open_dispute read-back failed")
    if existing is None:
        await enqueue_case(
            ctx,
            kind=CaseKind.DISPUTE,
            case_id=verified.dispute_id,
            customer_id=customer_id,
            reason=args.reason,
            rule_id="D09-eligible",
            txn=txn,
            customer_facts={
                "customer_says_not_me": args.customer_says_not_me or None,
                "dispute_type": args.reason,
            },
            actions=("dispute_opened",),
            created_at=verified.created_at,
        )
    result = OpenDisputeResult(dispute=DisputeView.model_validate(verified), created=existing is None)
    _audit(ctx, tool="open_dispute", args=audit_args, outcome="ok", customer_id=customer_id)
    return result


async def get_dispute(ctx: ToolContext, args: GetDisputeArgs) -> GetDisputeResult:
    audit_args = {"dispute_id": args.dispute_id}
    customer_id = _require_customer(ctx, tool="get_dispute", args=audit_args)
    owned = _owned(
        ctx.cases.get_dispute(args.dispute_id),
        customer_id=customer_id,
        ctx=ctx,
        tool="get_dispute",
        args=audit_args,
    )
    result = GetDisputeResult(dispute=DisputeView.model_validate(owned))
    _audit(ctx, tool="get_dispute", args=audit_args, outcome="ok", customer_id=customer_id)
    return result


async def block_card(ctx: ToolContext, args: BlockCardArgs) -> BlockCardResult:
    audit_args = {"product_id": args.product_id, "confirmed": args.confirmed}
    customer_id = _require_customer(ctx, tool="block_card", args=audit_args)
    if not args.confirmed:
        _deny(ctx, tool="block_card", args=audit_args, reason="not_confirmed", customer_id=customer_id)
    product = await _store(
        ctx,
        tool="block_card",
        args=audit_args,
        customer_id=customer_id,
        call=lambda: ProductStore(ctx.db).get(args.product_id),
    )
    owned = _owned(product, customer_id=customer_id, ctx=ctx, tool="block_card", args=audit_args)
    if owned.is_card is not True:
        _deny(ctx, tool="block_card", args=audit_args, reason="not_a_card", customer_id=customer_id)
    ctx.cases.block_card(customer_id=customer_id, product_id=args.product_id)
    verified = ctx.cases.get_card_block(args.product_id)
    if verified is None or verified.customer_id != customer_id:
        raise ToolError("block_card read-back failed")
    result = BlockCardResult(block=CardBlockView.model_validate(verified))
    _audit(ctx, tool="block_card", args=audit_args, outcome="ok", customer_id=customer_id)
    return result


async def create_handoff(ctx: ToolContext, args: CreateHandoffArgs) -> CreateHandoffResult:
    audit_args = {
        "reason": args.reason,
        "rule_id": args.rule_id,
        "facts_keys": sorted(args.facts),
        "actions": list(args.actions),
    }
    customer_id = _require_customer(ctx, tool="create_handoff", args=audit_args)
    # The case's transaction facts come from bank.*, and only for the customer's own transaction.
    txn: Transaction | None = None
    txn_id = args.facts.get("transaction_id")
    if isinstance(txn_id, str) and txn_id:
        row = await _store(
            ctx,
            tool="create_handoff",
            args=audit_args,
            customer_id=customer_id,
            call=lambda: TransactionStore(ctx.db).get(txn_id),
        )
        txn = row if row is not None and row.customer_id == customer_id else None
    created = ctx.cases.create_handoff(
        customer_id=customer_id,
        reason=args.reason,
        rule_id=args.rule_id,
        facts=args.facts,
        actions=args.actions,
        idempotency_key=args.idempotency_key,
    )
    verified = ctx.cases.get_handoff(created.handoff_id)
    if verified is None:
        raise ToolError("create_handoff read-back failed")
    await enqueue_case(
        ctx,
        kind=CaseKind.HANDOFF,
        case_id=verified.handoff_id,
        customer_id=customer_id,
        reason=verified.reason,
        rule_id=verified.rule_id,
        txn=txn,
        customer_facts=dict(verified.facts),
        actions=verified.actions,
        created_at=verified.created_at,
    )
    result = CreateHandoffResult(handoff=HandoffView.model_validate(verified))
    _audit(ctx, tool="create_handoff", args=audit_args, outcome="ok", customer_id=customer_id)
    return result


__all__ = [
    "block_card",
    "create_handoff",
    "evaluate_dispute",
    "get_dispute",
    "get_transaction",
    "get_transactions",
    "open_dispute",
]
