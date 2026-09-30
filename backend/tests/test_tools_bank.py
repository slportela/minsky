"""Unit tests for session-scoped bank tools (denials, happy paths, read-back)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from minsky_api.identity import SessionState, ToolSession
from minsky_api.store.cases_memory import InMemoryCasesBackend
from minsky_api.store.models import Product, Transaction
from minsky_api.tools import (
    BlockCardArgs,
    CreateHandoffArgs,
    GetDisputeArgs,
    GetTransactionArgs,
    GetTransactionsArgs,
    OpenDisputeArgs,
    ToolContext,
    ToolDenied,
    ToolError,
    block_card,
    create_handoff,
    get_dispute,
    get_transaction,
    get_transactions,
    open_dispute,
)


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    def __init__(self, *, get_result: Any = None, exec_rows: list[Any] | None = None) -> None:
        self.get_result = get_result
        self.exec_rows = exec_rows or []
        self.get_calls: list[tuple[type, Any]] = []
        self.fail_get = False
        self.fail_exec = False

    async def get(self, model: type, identity: Any) -> Any:
        self.get_calls.append((model, identity))
        if self.fail_get:
            raise OperationalError("select", {}, Exception("down"))
        return self.get_result

    async def exec(self, statement: Any) -> _FakeResult:
        if self.fail_exec:
            raise OperationalError("select", {}, Exception("down"))
        return _FakeResult(self.exec_rows)


def _valid(customer_id: str = "C1") -> ToolSession:
    return ToolSession(session_id="s1", state=SessionState.VALID, customer_id=customer_id)


def _txn(*, customer_id: str = "C1", transaction_id: str = "T1") -> Transaction:
    return Transaction(
        transaction_id=transaction_id,
        customer_id=customer_id,
        product_id="P1",
        amount_usd=Decimal("25.00"),
        amount_usd_source="native_usd",
        transaction_date=datetime(2026, 1, 1),
        merchant_name="Cafe",
        transaction_status="Approved",
    )


def _card(*, customer_id: str = "C1", product_id: str = "P1", is_card: bool | None = True) -> Product:
    return Product(
        product_id=product_id,
        customer_id=customer_id,
        is_card=is_card,
        product_number_last4="1234",
        product_status="Active",
    )


def _ctx(
    session: ToolSession,
    *,
    get_result: Any = None,
    exec_rows: list[Any] | None = None,
    cases: InMemoryCasesBackend | None = None,
    fail_get: bool = False,
    fail_exec: bool = False,
) -> ToolContext:
    db = FakeSession(get_result=get_result, exec_rows=exec_rows)
    db.fail_get = fail_get
    db.fail_exec = fail_exec
    return ToolContext(
        session=session,
        db=db,  # type: ignore[arg-type]
        cases=cases or InMemoryCasesBackend(),
    )


@pytest.mark.asyncio
async def test_get_transactions_happy_path():
    txn = _txn()
    ctx = _ctx(_valid(), exec_rows=[txn])
    result = await get_transactions(ctx, GetTransactionsArgs(limit=5))
    assert len(result.transactions) == 1
    assert result.transactions[0].transaction_id == "T1"
    assert ctx.cases.list_audit()[-1].outcome == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session",
    [
        ToolSession(session_id="s1", state=SessionState.ANONYMOUS),
        ToolSession(session_id="s1", state=SessionState.EXPIRED, customer_id="C1"),
    ],
)
async def test_all_tools_deny_bad_session(session: ToolSession):
    ctx = _ctx(session, get_result=_txn(), exec_rows=[_txn()])
    with pytest.raises(ToolDenied):
        await get_transactions(ctx)
    with pytest.raises(ToolDenied):
        await get_transaction(ctx, GetTransactionArgs(transaction_id="T1"))
    with pytest.raises(ToolDenied):
        await open_dispute(ctx, OpenDisputeArgs(transaction_id="T1", reason="x", confirmed=True))
    with pytest.raises(ToolDenied):
        await get_dispute(ctx, GetDisputeArgs(dispute_id="DSP-1"))
    with pytest.raises(ToolDenied):
        await block_card(ctx, BlockCardArgs(product_id="P1", confirmed=True))
    with pytest.raises(ToolDenied):
        await create_handoff(ctx, CreateHandoffArgs(reason="escalate"))
    assert all(entry.outcome == "denied" for entry in ctx.cases.list_audit())


@pytest.mark.asyncio
async def test_get_transaction_denies_other_customer():
    ctx = _ctx(_valid("C1"), get_result=_txn(customer_id="C2"))
    with pytest.raises(ToolDenied, match="not_owner"):
        await get_transaction(ctx, GetTransactionArgs(transaction_id="T1"))
    assert ctx.cases.list_audit()[-1].reason == "not_owner"


@pytest.mark.asyncio
async def test_get_transaction_not_found():
    ctx = _ctx(_valid(), get_result=None)
    with pytest.raises(ToolDenied, match="not_found"):
        await get_transaction(ctx, GetTransactionArgs(transaction_id="missing"))


@pytest.mark.asyncio
async def test_open_dispute_requires_confirmation_then_idempotent_read_back():
    cases = InMemoryCasesBackend()
    ctx = _ctx(_valid(), get_result=_txn(), cases=cases)
    with pytest.raises(ToolDenied, match="not_confirmed"):
        await open_dispute(ctx, OpenDisputeArgs(transaction_id="T1", reason="unrecognized", confirmed=False))
    first = await open_dispute(ctx, OpenDisputeArgs(transaction_id="T1", reason="unrecognized", confirmed=True))
    second = await open_dispute(ctx, OpenDisputeArgs(transaction_id="T1", reason="other", confirmed=True))
    assert first.dispute.dispute_id == second.dispute.dispute_id
    stored = cases.get_dispute(first.dispute.dispute_id)
    assert stored is not None
    assert first.dispute.dispute_id == stored.dispute_id
    fetched = await get_dispute(ctx, GetDisputeArgs(dispute_id=first.dispute.dispute_id))
    assert fetched.dispute == first.dispute


@pytest.mark.asyncio
async def test_get_dispute_denies_cross_customer():
    cases = InMemoryCasesBackend()
    dispute = cases.create_dispute(customer_id="C2", transaction_id="T9", reason="x")
    ctx = _ctx(_valid("C1"), cases=cases)
    with pytest.raises(ToolDenied, match="not_owner"):
        await get_dispute(ctx, GetDisputeArgs(dispute_id=dispute.dispute_id))


@pytest.mark.asyncio
async def test_block_card_requires_confirmation_and_ownership():
    cases = InMemoryCasesBackend()
    ctx = _ctx(_valid(), get_result=_card(), cases=cases)
    with pytest.raises(ToolDenied, match="not_confirmed"):
        await block_card(ctx, BlockCardArgs(product_id="P1", confirmed=False))
    result = await block_card(ctx, BlockCardArgs(product_id="P1", confirmed=True))
    assert result.block.status == "Blocked"
    assert cases.get_card_block("P1") is not None
    assert result.block.product_id == cases.get_card_block("P1").product_id  # type: ignore[union-attr]

    other = _ctx(_valid("C1"), get_result=_card(customer_id="C2"), cases=InMemoryCasesBackend())
    with pytest.raises(ToolDenied, match="not_owner"):
        await block_card(other, BlockCardArgs(product_id="P1", confirmed=True))


@pytest.mark.asyncio
async def test_block_card_rejects_non_card_product():
    product = Product(product_id="P2", customer_id="C1", is_card=False, product_status="Active")
    ctx = _ctx(_valid(), get_result=product)
    with pytest.raises(ToolDenied, match="not_a_card"):
        await block_card(ctx, BlockCardArgs(product_id="P2", confirmed=True))


@pytest.mark.asyncio
async def test_block_card_rejects_unknown_is_card():
    ctx = _ctx(_valid(), get_result=_card(is_card=None))
    with pytest.raises(ToolDenied, match="not_a_card"):
        await block_card(ctx, BlockCardArgs(product_id="P1", confirmed=True))
    assert ctx.cases.list_audit()[-1].reason == "not_a_card"


@pytest.mark.asyncio
async def test_get_transaction_store_failure_is_tool_error_and_audited():
    ctx = _ctx(_valid(), fail_get=True)
    with pytest.raises(ToolError, match="get_transaction store failure"):
        await get_transaction(ctx, GetTransactionArgs(transaction_id="T1"))
    entry = ctx.cases.list_audit()[-1]
    assert entry.outcome == "error"
    assert entry.reason == "store_error"


@pytest.mark.asyncio
async def test_get_transactions_store_failure_is_tool_error_and_audited():
    ctx = _ctx(_valid(), fail_exec=True)
    with pytest.raises(ToolError, match="get_transactions store failure"):
        await get_transactions(ctx, GetTransactionsArgs(limit=5))
    assert ctx.cases.list_audit()[-1].outcome == "error"


@pytest.mark.asyncio
async def test_open_dispute_and_block_card_store_failures():
    dispute_ctx = _ctx(_valid(), fail_get=True)
    with pytest.raises(ToolError, match="open_dispute store failure"):
        await open_dispute(
            dispute_ctx,
            OpenDisputeArgs(transaction_id="T1", reason="unrecognized", confirmed=True),
        )
    block_ctx = _ctx(_valid(), fail_get=True)
    with pytest.raises(ToolError, match="block_card store failure"):
        await block_card(block_ctx, BlockCardArgs(product_id="P1", confirmed=True))
    assert dispute_ctx.cases.list_audit()[-1].reason == "store_error"
    assert block_ctx.cases.list_audit()[-1].reason == "store_error"


@pytest.mark.asyncio
async def test_create_handoff_read_back():
    cases = InMemoryCasesBackend()
    ctx = _ctx(_valid(), cases=cases)
    result = await create_handoff(
        ctx,
        CreateHandoffArgs(
            reason="fraud review",
            rule_id="D06-possible-fraud",
            facts={"transaction_id": "T1"},
            actions=("block_offered",),
        ),
    )
    stored = cases.get_handoff(result.handoff.handoff_id)
    assert stored is not None
    assert result.handoff.handoff_id == stored.handoff_id
    assert result.handoff.rule_id == "D06-possible-fraud"


@pytest.mark.asyncio
async def test_open_dispute_denies_other_customer_transaction():
    ctx = _ctx(_valid("C1"), get_result=_txn(customer_id="C2"))
    with pytest.raises(ToolDenied, match="not_owner"):
        await open_dispute(ctx, OpenDisputeArgs(transaction_id="T1", reason="x", confirmed=True))
