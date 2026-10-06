"""Tools for the search agent: query the customer's own transactions, read a general profile of the customer.

Same contract as tools/bank.py: every call takes ToolContext, never a customer id from the model, and
writes an audit record. `load_history` reads the session customer's rows once and builds the sandbox
(agent.sandbox); `query_transactions` is the only door the model has to them.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import dataclass

from minsky_api.agent.sandbox import QueryResult, QuerySandbox, SandboxError
from minsky_api.config import get_settings
from minsky_api.observability import start_span
from minsky_api.store.complaint_stats import ComplaintStatsStore
from minsky_api.store.customers import CustomerStore
from minsky_api.store.products import ProductStore
from minsky_api.store.transactions import TransactionStore
from minsky_api.tools.bank import _audit, _require_customer, _store
from minsky_api.tools.context import ToolContext
from minsky_api.tools.errors import ToolError
from minsky_api.tools.schemas import (
    CustomerProfileView,
    GetCustomerProfileResult,
    QueryTransactionsArgs,
    QueryTransactionsResult,
)

# What one query may put in front of the model: keeps a wide SELECT from flooding the context.
_MAX_RESULT_CHARS = 6000


@dataclass
class TransactionHistory:
    """The sandbox for one customer. Not stored in conversation state (it holds a database connection)."""

    customer_id: str
    sandbox: QuerySandbox

    def close(self) -> None:
        self.sandbox.close()


async def load_history(ctx: ToolContext) -> TransactionHistory:
    audit_args: dict[str, object] = {}
    customer_id = _require_customer(ctx, tool="load_history", args=audit_args)
    rows = await _store(
        ctx,
        tool="load_history",
        args=audit_args,
        customer_id=customer_id,
        call=lambda: TransactionStore(ctx.db).list_history(customer_id),
    )
    # Defense in depth: the store filters by customer, and the sandbox must never hold anyone else's row.
    if any(row.customer_id != customer_id for row in rows):
        _audit(
            ctx, tool="load_history", args=audit_args, outcome="error", reason="foreign_row", customer_id=customer_id
        )
        raise ToolError("load_history: foreign row in the customer's history")
    history = TransactionHistory(customer_id=customer_id, sandbox=QuerySandbox(rows, today=get_settings().today))
    _audit(ctx, tool="load_history", args=audit_args, outcome="ok", reason=f"rows={len(rows)}", customer_id=customer_id)
    return history


def result_text(result: QueryTransactionsResult) -> str:
    """The query result as the model reads it: compact JSON, cut to a size that cannot flood the context."""
    payload = {
        "columns": list(result.columns),
        "rows": [list(row) for row in result.rows],
        "row_count": result.row_count,
        "truncated": result.truncated,
    }
    text = json.dumps(payload, ensure_ascii=False, default=str)
    while len(text) > _MAX_RESULT_CHARS and payload["rows"]:
        payload["rows"] = payload["rows"][: max(1, len(payload["rows"]) // 2)] if len(payload["rows"]) > 1 else []
        payload["truncated"] = True
        text = json.dumps(payload, ensure_ascii=False, default=str)
    return text


def _ids(result: QueryResult) -> tuple[str, ...]:
    if "transaction_id" not in result.columns:
        return ()
    index = result.columns.index("transaction_id")
    return tuple(str(row[index]) for row in result.rows if row[index] is not None)


async def query_transactions(
    ctx: ToolContext, history: TransactionHistory, args: QueryTransactionsArgs
) -> QueryTransactionsResult:
    """Run the model's SELECT in the sandbox. A refused or failed query raises SandboxError (its message is
    for the model, which can fix the query); it is audited as a denial, not as a system error."""
    audit_args = {"sql": args.sql}
    customer_id = _require_customer(ctx, tool="query_transactions", args=audit_args)
    if history.customer_id != customer_id:
        _audit(
            ctx,
            tool="query_transactions",
            args=audit_args,
            outcome="denied",
            reason="not_owner",
            customer_id=customer_id,
        )
        raise ToolError("query_transactions: history belongs to another customer")
    with start_span("tool.query_transactions", tool="query_transactions", sql=args.sql[:300]):
        try:
            # SQLite is synchronous and the deadline is half a second: off the event loop, so a slow query of one
            # customer does not stall every other conversation.
            found = await asyncio.to_thread(history.sandbox.query, args.sql)
        except SandboxError:
            _audit(
                ctx,
                tool="query_transactions",
                args=audit_args,
                outcome="denied",
                reason="query_refused",
                customer_id=customer_id,
            )
            raise
        # row_count is what the query returned (at most the cap); `truncated` says there were more.
        result = QueryTransactionsResult(
            columns=found.columns,
            rows=found.rows,
            row_count=len(found.rows),
            truncated=found.truncated,
            transaction_ids=_ids(found),
        )
        _audit(
            ctx,
            tool="query_transactions",
            args=audit_args,
            outcome="ok",
            reason=f"rows={len(found.rows)}",
            customer_id=customer_id,
        )
        return result


async def get_customer_profile(ctx: ToolContext) -> GetCustomerProfileResult:
    """General facts about the session customer for the person who takes an escalated case."""
    audit_args: dict[str, object] = {}
    customer_id = _require_customer(ctx, tool="get_customer_profile", args=audit_args)
    customer = await _store(
        ctx, tool="get_customer_profile", args=audit_args, customer_id=customer_id,
        call=lambda: CustomerStore(ctx.db).get(customer_id),
    )  # fmt: skip
    stats = await _store(
        ctx, tool="get_customer_profile", args=audit_args, customer_id=customer_id,
        call=lambda: ComplaintStatsStore(ctx.db).get(customer_id),
    )  # fmt: skip
    products = await _store(
        ctx, tool="get_customer_profile", args=audit_args, customer_id=customer_id,
        call=lambda: ProductStore(ctx.db).list_by_customer(customer_id),
    )  # fmt: skip
    kinds = Counter(p.product_type or "unknown" for p in products if p.customer_id == customer_id)
    profile = CustomerProfileView(
        customer_id=customer_id,
        segment=customer.segment if customer else None,
        country=customer.country if customer else None,
        customer_status=customer.customer_status if customer else None,
        products=dict(kinds),
        complaints_total=stats.complaints_total if stats else None,
        complaints_last_90d=stats.complaints_last_90d if stats else None,
        is_repeat_complainer=stats.is_repeat_complainer if stats else None,
    )
    _audit(ctx, tool="get_customer_profile", args=audit_args, outcome="ok", customer_id=customer_id)
    return GetCustomerProfileResult(profile=profile)


__all__ = ["TransactionHistory", "get_customer_profile", "load_history", "query_transactions", "result_text"]
