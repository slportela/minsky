"""Mock core-banking tools: typed, session-scoped, audited. No LangGraph.

Contracts and limits (B4):
- Every call takes ToolContext (session + db + cases); never a customer_id from the model.
- Reads use bank.* stores; writes use InMemoryCasesBackend until Postgres cases.* exists.
- open_dispute and block_card require confirmed=True (orchestrator sets it after explicit YES).
- Writes return only after read-back of the stored row.
- get_transactions limit is 1..100 (default 20).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, NoReturn, Protocol

from minsky_api.identity.errors import PermissionDenied
from minsky_api.identity.session import require_customer
from minsky_api.store.errors import StoreError
from minsky_api.store.products import ProductStore
from minsky_api.store.transactions import TransactionStore
from minsky_api.tools.context import ToolContext
from minsky_api.tools.errors import ToolDenied, ToolError
from minsky_api.tools.schemas import (
    BlockCardArgs,
    BlockCardResult,
    CardBlockView,
    CreateHandoffArgs,
    CreateHandoffResult,
    DisputeView,
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
    audit_args = {"limit": params.limit}
    customer_id = _require_customer(ctx, tool="get_transactions", args=audit_args)
    rows = await _store(
        ctx,
        tool="get_transactions",
        args=audit_args,
        customer_id=customer_id,
        call=lambda: TransactionStore(ctx.db).list_by_customer(customer_id, limit=params.limit),
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


async def open_dispute(ctx: ToolContext, args: OpenDisputeArgs) -> OpenDisputeResult:
    audit_args = {
        "transaction_id": args.transaction_id,
        "reason": args.reason,
        "confirmed": args.confirmed,
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
    _owned(row, customer_id=customer_id, ctx=ctx, tool="open_dispute", args=audit_args)
    created = ctx.cases.create_dispute(
        customer_id=customer_id,
        transaction_id=args.transaction_id,
        reason=args.reason,
    )
    verified = ctx.cases.get_dispute(created.dispute_id)
    if verified is None:
        raise ToolError("open_dispute read-back failed")
    result = OpenDisputeResult(dispute=DisputeView.model_validate(verified))
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
    created = ctx.cases.create_handoff(
        customer_id=customer_id,
        reason=args.reason,
        rule_id=args.rule_id,
        facts=args.facts,
        actions=args.actions,
    )
    verified = ctx.cases.get_handoff(created.handoff_id)
    if verified is None:
        raise ToolError("create_handoff read-back failed")
    result = CreateHandoffResult(handoff=HandoffView.model_validate(verified))
    _audit(ctx, tool="create_handoff", args=audit_args, outcome="ok", customer_id=customer_id)
    return result


__all__ = [
    "block_card",
    "create_handoff",
    "get_dispute",
    "get_transaction",
    "get_transactions",
    "open_dispute",
]
