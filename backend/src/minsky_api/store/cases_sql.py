"""Postgres cases.*: disputes, handoffs, card blocks, the audit log and the back-office case queue.

Same methods as InMemoryCasesBackend, so tools and the console do not change. Writes are idempotent
through unique keys (one dispute per customer and transaction, one handoff per idempotency key), which
is what makes a retried tool call safe. The API creates these tables at startup (the pipeline owns
bank.*, the API owns cases.*); production replaces create_all with migrations (docs/poc_to_prod.md).

Synchronous on purpose: each call is one or two indexed statements, and the CasesBackend protocol is
synchronous. SQLite works too (tests), with the cases schema mapped away.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    Connection,
    DateTime,
    Engine,
    Float,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.pool import StaticPool

from minsky_api.store.cases_memory import (
    AuditRecord,
    CardBlockRecord,
    CaseRecord,
    DisputeRecord,
    HandoffRecord,
    claimed,
    resolved,
    sort_cases,
)

SCHEMA = "cases"
metadata = MetaData(schema=SCHEMA)

disputes = Table(
    "disputes",
    metadata,
    Column("dispute_id", String(32), primary_key=True),
    Column("customer_id", String(64), nullable=False, index=True),
    Column("transaction_id", String(64), nullable=False),
    Column("reason", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("customer_id", "transaction_id", name="uq_disputes_customer_transaction"),
)

handoffs = Table(
    "handoffs",
    metadata,
    Column("handoff_id", String(32), primary_key=True),
    Column("customer_id", String(64), nullable=False, index=True),
    Column("reason", String(200), nullable=False),
    Column("rule_id", String(64)),
    Column("facts", JSON, nullable=False),
    Column("actions", JSON, nullable=False),
    Column("idempotency_key", String(128)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("customer_id", "idempotency_key", name="uq_handoffs_customer_key"),
)

card_blocks = Table(
    "card_blocks",
    metadata,
    Column("product_id", String(64), primary_key=True),
    Column("customer_id", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("blocked_at", DateTime(timezone=True), nullable=False),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("audit_id", BigInteger().with_variant(sqlite.INTEGER(), "sqlite"), primary_key=True, autoincrement=True),
    Column("tool", String(64), nullable=False),
    Column("session_id", String(128), nullable=False),
    Column("customer_id", String(64), index=True),
    Column("args_digest", String(64), nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("reason", String(200)),
    Column("at", DateTime(timezone=True), nullable=False),
)

case_queue = Table(
    "case_queue",
    metadata,
    Column("case_id", String(32), primary_key=True),
    Column("kind", String(16), nullable=False),
    Column("customer_id", String(64), nullable=False, index=True),
    Column("rule_id", String(64)),
    Column("reason", String(200), nullable=False),
    Column("priority", String(16), nullable=False),
    Column("queue", String(16), nullable=False, index=True),
    Column("triage_reason", String(200), nullable=False),
    Column("due_at", DateTime(timezone=True), nullable=False),
    Column("status", String(16), nullable=False, index=True),
    Column("summary", Text, nullable=False),
    Column("facts", JSON, nullable=False),
    Column("actions", JSON, nullable=False),
    Column("open_questions", JSON, nullable=False),
    Column("expected_resolution_days", Float),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("assigned_to", String(64)),
    Column("resolution_note", Text),
)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    # SQLite returns naive datetimes; every timestamp we write is UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def to_sync_url(url: str) -> str:
    """The async app URL also works synchronously with psycopg 3; only aiosqlite needs swapping."""
    return url.replace("sqlite+aiosqlite", "sqlite+pysqlite")


class SqlCasesBackend:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._sqlite = engine.dialect.name == "sqlite"

    @classmethod
    def from_url(
        cls, url: str, *, pool_size: int = 5, max_overflow: int = 5, pool_timeout: int = 30
    ) -> SqlCasesBackend:
        sync_url = to_sync_url(url)
        if sync_url.startswith("sqlite"):
            engine = create_engine(sync_url, poolclass=StaticPool, connect_args={"check_same_thread": False})
        else:
            engine = create_engine(
                sync_url,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=pool_timeout,
                pool_pre_ping=True,
            )
        return cls(engine)

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            if not self._sqlite:
                conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
            metadata.create_all(conn)

    def dispose(self) -> None:
        self._engine.dispose()

    @contextmanager
    def _connect(self) -> Iterator[Connection]:
        options = {"schema_translate_map": {SCHEMA: None}} if self._sqlite else {}
        with self._engine.begin() as conn:
            yield conn.execution_options(**options)

    def _insert_ignore(self, conn: Connection, table: Table, values: dict[str, Any]) -> None:
        dialect_insert = sqlite.insert if self._sqlite else postgresql.insert
        conn.execute(dialect_insert(table).values(**values).on_conflict_do_nothing())

    # ---- disputes ----

    @staticmethod
    def _dispute(row: Any) -> DisputeRecord:
        return DisputeRecord(
            dispute_id=row.dispute_id,
            customer_id=row.customer_id,
            transaction_id=row.transaction_id,
            reason=row.reason,
            status=row.status,
            created_at=_aware(row.created_at),
        )

    def create_dispute(self, *, customer_id: str, transaction_id: str, reason: str) -> DisputeRecord:
        with self._connect() as conn:
            self._insert_ignore(
                conn,
                disputes,
                {
                    "dispute_id": f"DSP-{uuid4().hex[:12]}",
                    "customer_id": customer_id,
                    "transaction_id": transaction_id,
                    "reason": reason,
                    "status": "open",
                    "created_at": _now(),
                },
            )
            row = conn.execute(
                select(disputes).where(
                    disputes.c.customer_id == customer_id, disputes.c.transaction_id == transaction_id
                )
            ).one()
        return self._dispute(row)

    def get_dispute(self, dispute_id: str) -> DisputeRecord | None:
        with self._connect() as conn:
            row = conn.execute(select(disputes).where(disputes.c.dispute_id == dispute_id)).one_or_none()
        return self._dispute(row) if row else None

    def get_dispute_by_transaction(self, *, customer_id: str, transaction_id: str) -> DisputeRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                select(disputes).where(
                    disputes.c.customer_id == customer_id, disputes.c.transaction_id == transaction_id
                )
            ).one_or_none()
        return self._dispute(row) if row else None

    # ---- handoffs ----

    @staticmethod
    def _handoff(row: Any) -> HandoffRecord:
        return HandoffRecord(
            handoff_id=row.handoff_id,
            customer_id=row.customer_id,
            reason=row.reason,
            rule_id=row.rule_id,
            facts=dict(row.facts),
            actions=tuple(row.actions),
            created_at=_aware(row.created_at),
        )

    def create_handoff(
        self,
        *,
        customer_id: str,
        reason: str,
        rule_id: str | None,
        facts: dict[str, Any],
        actions: tuple[str, ...],
        idempotency_key: str | None = None,
    ) -> HandoffRecord:
        handoff_id = f"HO-{uuid4().hex[:12]}"
        values = {
            "handoff_id": handoff_id,
            "customer_id": customer_id,
            "reason": reason,
            "rule_id": rule_id,
            "facts": dict(facts),
            "actions": list(actions),
            "idempotency_key": idempotency_key,
            "created_at": _now(),
        }
        with self._connect() as conn:
            if idempotency_key is None:
                conn.execute(insert(handoffs).values(**values))
                where = handoffs.c.handoff_id == handoff_id
            else:
                self._insert_ignore(conn, handoffs, values)
                where = (handoffs.c.customer_id == customer_id) & (handoffs.c.idempotency_key == idempotency_key)
            row = conn.execute(select(handoffs).where(where)).one()
        return self._handoff(row)

    def get_handoff(self, handoff_id: str) -> HandoffRecord | None:
        with self._connect() as conn:
            row = conn.execute(select(handoffs).where(handoffs.c.handoff_id == handoff_id)).one_or_none()
        return self._handoff(row) if row else None

    # ---- card blocks ----

    @staticmethod
    def _block(row: Any) -> CardBlockRecord:
        return CardBlockRecord(
            product_id=row.product_id,
            customer_id=row.customer_id,
            status=row.status,
            blocked_at=_aware(row.blocked_at),
        )

    def block_card(self, *, customer_id: str, product_id: str) -> CardBlockRecord:
        with self._connect() as conn:
            self._insert_ignore(
                conn,
                card_blocks,
                {"product_id": product_id, "customer_id": customer_id, "status": "Blocked", "blocked_at": _now()},
            )
            row = conn.execute(select(card_blocks).where(card_blocks.c.product_id == product_id)).one()
        return self._block(row)

    def get_card_block(self, product_id: str) -> CardBlockRecord | None:
        with self._connect() as conn:
            row = conn.execute(select(card_blocks).where(card_blocks.c.product_id == product_id)).one_or_none()
        return self._block(row) if row else None

    # ---- audit ----

    @staticmethod
    def _audit(row: Any) -> AuditRecord:
        return AuditRecord(
            tool=row.tool,
            session_id=row.session_id,
            customer_id=row.customer_id,
            args_digest=row.args_digest,
            outcome=row.outcome,
            reason=row.reason,
            at=_aware(row.at),
        )

    def append_audit(
        self,
        *,
        tool: str,
        session_id: str,
        customer_id: str | None,
        args_digest: str,
        outcome: str,
        reason: str | None = None,
    ) -> AuditRecord:
        record = AuditRecord(
            tool=tool,
            session_id=session_id,
            customer_id=customer_id,
            args_digest=args_digest,
            outcome=outcome,
            reason=reason,
            at=_now(),
        )
        with self._connect() as conn:
            conn.execute(
                insert(audit_log).values(
                    tool=tool,
                    session_id=session_id,
                    customer_id=customer_id,
                    args_digest=args_digest,
                    outcome=outcome,
                    reason=reason,
                    at=record.at,
                )
            )
        return record

    def list_audit(self) -> tuple[AuditRecord, ...]:
        with self._connect() as conn:
            rows = conn.execute(select(audit_log).order_by(audit_log.c.audit_id)).all()
        return tuple(self._audit(row) for row in rows)

    def list_audit_for_customer(self, customer_id: str) -> tuple[AuditRecord, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                select(audit_log).where(audit_log.c.customer_id == customer_id).order_by(audit_log.c.audit_id)
            ).all()
        return tuple(self._audit(row) for row in rows)

    # ---- case queue ----

    @staticmethod
    def _case(row: Any) -> CaseRecord:
        return CaseRecord(
            case_id=row.case_id,
            kind=row.kind,
            customer_id=row.customer_id,
            rule_id=row.rule_id,
            reason=row.reason,
            priority=row.priority,
            queue=row.queue,
            triage_reason=row.triage_reason,
            due_at=_aware(row.due_at),
            status=row.status,
            summary=row.summary,
            facts=dict(row.facts),
            actions=tuple(row.actions),
            open_questions=tuple(row.open_questions),
            expected_resolution_days=row.expected_resolution_days,
            created_at=_aware(row.created_at),
            updated_at=_aware(row.updated_at),
            assigned_to=row.assigned_to,
            resolution_note=row.resolution_note,
        )

    @staticmethod
    def _case_values(record: CaseRecord) -> dict[str, Any]:
        return {
            "case_id": record.case_id,
            "kind": record.kind,
            "customer_id": record.customer_id,
            "rule_id": record.rule_id,
            "reason": record.reason,
            "priority": record.priority,
            "queue": record.queue,
            "triage_reason": record.triage_reason,
            "due_at": record.due_at,
            "status": record.status,
            "summary": record.summary,
            "facts": dict(record.facts),
            "actions": list(record.actions),
            "open_questions": list(record.open_questions),
            "expected_resolution_days": record.expected_resolution_days,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "assigned_to": record.assigned_to,
            "resolution_note": record.resolution_note,
        }

    def enqueue_case(self, record: CaseRecord) -> CaseRecord:
        with self._connect() as conn:
            self._insert_ignore(conn, case_queue, self._case_values(record))
            row = conn.execute(select(case_queue).where(case_queue.c.case_id == record.case_id)).one()
        return self._case(row)

    def get_case(self, case_id: str) -> CaseRecord | None:
        with self._connect() as conn:
            row = conn.execute(select(case_queue).where(case_queue.c.case_id == case_id)).one_or_none()
        return self._case(row) if row else None

    def list_cases(self, *, status: str | None = None, queue: str | None = None) -> tuple[CaseRecord, ...]:
        query = select(case_queue)
        if status is not None:
            query = query.where(case_queue.c.status == status)
        if queue is not None:
            query = query.where(case_queue.c.queue == queue)
        with self._connect() as conn:
            rows = conn.execute(query).all()
        return sort_cases([self._case(row) for row in rows])

    def _transition(self, case_id: str, change: Any) -> CaseRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                select(case_queue).where(case_queue.c.case_id == case_id).with_for_update()
            ).one_or_none()
            if row is None:
                return None
            after: CaseRecord = change(self._case(row))
            conn.execute(
                update(case_queue)
                .where(case_queue.c.case_id == case_id)
                .values(
                    status=after.status,
                    assigned_to=after.assigned_to,
                    resolution_note=after.resolution_note,
                    updated_at=after.updated_at,
                )
            )
        return after

    def claim_case(self, case_id: str, agent_id: str) -> CaseRecord | None:
        return self._transition(case_id, lambda record: claimed(record, agent_id))

    def resolve_case(self, case_id: str, agent_id: str, note: str) -> CaseRecord | None:
        return self._transition(case_id, lambda record: resolved(record, agent_id, note))
