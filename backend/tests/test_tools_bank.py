"""Unit tests for session-scoped bank tools (denials, happy paths, read-back)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from minsky_api.identity import SessionState, ToolSession
from minsky_api.store.cases_memory import InMemoryCasesBackend
from minsky_api.store.models import CustomerComplaintStats, Product, Transaction
from minsky_api.tools import (
    BlockCardArgs,
    CreateHandoffArgs,
    EvaluateDisputeArgs,
    GetDisputeArgs,
    GetTransactionArgs,
    GetTransactionsArgs,
    OpenDisputeArgs,
    ToolContext,
    ToolDenied,
    ToolError,
    block_card,
    create_handoff,
    evaluate_dispute,
    get_dispute,
    get_transaction,
    get_transactions,
    open_dispute,
)


async def test_handoff_idempotency_survives_retry_and_is_customer_scoped():
    cases = InMemoryCasesBackend()
    ctx = ToolContext(session=_valid(), db=FakeSession(), cases=cases)  # type: ignore[arg-type]
    args = CreateHandoffArgs(reason="possible_fraud", idempotency_key="conversation:3")
    first = await create_handoff(ctx, args)
    retry = await create_handoff(ctx, args)
    assert first.handoff.handoff_id == retry.handoff.handoff_id
    other = ToolContext(session=_valid("C2"), db=FakeSession(), cases=cases)  # type: ignore[arg-type]
    separate = await create_handoff(other, args)
    assert separate.handoff.customer_id == "C2"
    assert separate.handoff.handoff_id != first.handoff.handoff_id


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    def __init__(self, *, get_result: Any = None, exec_rows: list[Any] | None = None) -> None:
        # get_result: one row (returned only when asked for its own model) or {model: row}
        self.get_result = get_result
        self.exec_rows = exec_rows or []
        self.exec_statements: list[Any] = []
        self.get_calls: list[tuple[type, Any]] = []
        self.fail_get = False
        self.fail_exec = False

    async def get(self, model: type, identity: Any) -> Any:
        self.get_calls.append((model, identity))
        if self.fail_get:
            raise OperationalError("select", {}, Exception("down"))
        if isinstance(self.get_result, dict):
            return self.get_result.get(model)
        return self.get_result if isinstance(self.get_result, model) else None

    async def exec(self, statement: Any) -> _FakeResult:
        self.exec_statements.append(statement)
        if self.fail_exec:
            raise OperationalError("select", {}, Exception("down"))
        return _FakeResult(self.exec_rows)


def _valid(customer_id: str = "C1") -> ToolSession:
    return ToolSession(session_id="s1", state=SessionState.VALID, customer_id=customer_id)


def _txn(
    *,
    customer_id: str = "C1",
    transaction_id: str = "T1",
    status: str = "Approved",
    when: datetime = datetime(2026, 6, 10, 9, 30),  # within the dispute window of today = 2026-06-18
    amount_usd: str = "25.00",
    is_fraud: bool = False,
) -> Transaction:
    return Transaction(
        transaction_id=transaction_id,
        customer_id=customer_id,
        product_id="P1",
        amount=Decimal(amount_usd),
        currency="USD",
        amount_usd=Decimal(amount_usd),
        amount_usd_source="native_usd",
        transaction_date=when,
        merchant_name="Cafe",
        transaction_status=status,
        is_fraud=is_fraud,
        fraud_score=Decimal("95.00") if is_fraud else Decimal("10.00"),
    )


def _stats(customer_id: str = "C1", *, repeat: bool = False) -> CustomerComplaintStats:
    return CustomerComplaintStats(customer_id=customer_id, is_repeat_complainer=repeat)


def _bank(txn: Transaction, stats: CustomerComplaintStats | None = None) -> dict[type, Any]:
    return {Transaction: txn, CustomerComplaintStats: stats if stats is not None else _stats(txn.customer_id)}


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
    ctx = _ctx(_valid(), get_result=_bank(_txn()), cases=cases)
    with pytest.raises(ToolDenied, match="not_confirmed"):
        await open_dispute(ctx, OpenDisputeArgs(transaction_id="T1", reason="unrecognized", confirmed=False))
    first = await open_dispute(ctx, OpenDisputeArgs(transaction_id="T1", reason="unrecognized", confirmed=True))
    second = await open_dispute(ctx, OpenDisputeArgs(transaction_id="T1", reason="other", confirmed=True))
    assert first.dispute.dispute_id == second.dispute.dispute_id
    assert (first.created, second.created) == (True, False)
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "txn,stats,says_not_me,rule",
    [
        (_txn(status="Declined"), _stats(), False, "D01-declined-not-charged"),
        (_txn(status="Reversed"), _stats(), False, "D02-already-reversed"),
        (_txn(status="Pending"), _stats(), False, "D03-pending-not-posted"),
        (_txn(), _stats(), True, "D06-possible-fraud"),
        (_txn(is_fraud=True), _stats(), False, "D06-possible-fraud"),
        (_txn(when=datetime(2025, 12, 1)), _stats(), False, "D05-outside-window"),
        (_txn(amount_usd="900.00"), _stats(), False, "D07-above-auto-limit"),
        (_txn(), _stats(repeat=True), False, "D08-repeat-complainer"),
    ],
)
async def test_open_dispute_enforces_the_policy(txn, stats, says_not_me, rule):
    cases = InMemoryCasesBackend()
    ctx = _ctx(_valid(), get_result=_bank(txn, stats), cases=cases)
    args = OpenDisputeArgs(transaction_id="T1", reason="x", confirmed=True, customer_says_not_me=says_not_me)
    with pytest.raises(ToolDenied, match=f"policy:{rule}"):
        await open_dispute(ctx, args)
    assert cases.get_dispute_by_transaction(customer_id="C1", transaction_id="T1") is None
    assert cases.list_audit()[-1].reason == f"policy:{rule}"


@pytest.mark.asyncio
async def test_evaluate_dispute_returns_the_decision_without_writing():
    cases = InMemoryCasesBackend()
    ctx = _ctx(_valid(), get_result=_bank(_txn(is_fraud=True)), cases=cases)
    result = await evaluate_dispute(ctx, EvaluateDisputeArgs(transaction_id="T1"))
    assert (result.rule_id, result.route, result.offer_card_block) == ("D06-possible-fraud", "escalate_fraud", True)
    assert "is_fraud" not in result.model_dump()
    assert cases.get_dispute_by_transaction(customer_id="C1", transaction_id="T1") is None
    eligible = await evaluate_dispute(
        _ctx(_valid(), get_result=_bank(_txn())), EvaluateDisputeArgs(transaction_id="T1")
    )
    assert eligible.rule_id == "D09-eligible"


@pytest.mark.asyncio
async def test_evaluate_dispute_reports_an_existing_dispute():
    cases = InMemoryCasesBackend()
    ctx = _ctx(_valid(), get_result=_bank(_txn()), cases=cases)
    opened = await open_dispute(ctx, OpenDisputeArgs(transaction_id="T1", reason="x", confirmed=True))
    result = await evaluate_dispute(ctx, EvaluateDisputeArgs(transaction_id="T1"))
    assert (result.rule_id, result.existing_dispute_id) == ("D04-already-disputed", opened.dispute.dispute_id)


@pytest.mark.asyncio
async def test_missing_complaint_stats_fail_loudly():
    ctx = _ctx(_valid(), get_result={Transaction: _txn(), CustomerComplaintStats: None})
    with pytest.raises(ToolError):
        await open_dispute(ctx, OpenDisputeArgs(transaction_id="T1", reason="x", confirmed=True))


@pytest.mark.asyncio
async def test_transaction_view_hides_fraud_signals_and_owner():
    ctx = _ctx(_valid(), get_result=_txn(is_fraud=True))
    view = (await get_transaction(ctx, GetTransactionArgs(transaction_id="T1"))).transaction.model_dump()
    assert not {"is_fraud", "fraud_score", "customer_id"} & set(view)


@pytest.mark.asyncio
async def test_get_transactions_passes_the_filters_to_the_store():
    ctx = _ctx(_valid(), exec_rows=[_txn()])
    args = GetTransactionsArgs(
        merchant="caf",
        min_amount=Decimal("10"),
        max_amount=Decimal("50"),
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 15),
    )
    await get_transactions(ctx, args)
    from sqlalchemy.dialects import postgresql

    sql = str(ctx.db.exec_statements[0].compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]
    assert "ILIKE" in sql.upper() and "amount >=" in sql and "amount <=" in sql and "transaction_date >=" in sql
    assert "customer_id" in sql


def test_get_transactions_args_reject_inverted_ranges():
    with pytest.raises(ValueError):
        GetTransactionsArgs(min_amount=Decimal("50"), max_amount=Decimal("10"))
    with pytest.raises(ValueError):
        GetTransactionsArgs(date_from=date(2026, 6, 15), date_to=date(2026, 6, 1))
