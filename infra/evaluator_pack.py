"""Build the evaluator local-run slice: bank.* SQL + an env file with credentials and a cheat sheet.

Team only:

    make evaluator-pack        # synthetic fixture rows -> the files that are committed
    make evaluator-pack-gold   # real gold rows (needs `make gold`) -> git-ignored local files

The committed files are always synthetic: gold rows are organizer data and must not reach git
(AGENTS.md rule 7), so the gold variant writes next to them under names `.gitignore` excludes:

  committed:  data/evaluator/bank_slice.sql.gz   .env.evaluator.example
  local only: data/evaluator/bank_slice.gold.sql.gz   .env.evaluator.gold
              (use with: EVALUATOR_SLICE=./data/evaluator/bank_slice.gold.sql.gz docker compose ...)

The tokens are fixed and public on purpose (a judge has no way to generate them), so they are only
valid for a stack on localhost: compose.evaluator.yaml pins DOMAIN=localhost and
MINSKY_ENVIRONMENT=local. Never copy the file to a reachable host.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import math
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

import duckdb

# infra/ has no __init__.py: this is a plain sibling import, which works because `python
# infra/evaluator_pack.py` puts this file's directory first on sys.path. Reused instead of
# duplicated so the two scripts can't silently drift (SCENARIOS, STAFF, the scenario picker, the
# es/pt opening messages).
from demo_sessions import SCENARIOS, STAFF, _opening, _pick

from minsky_api.config import get_settings
from minsky_api.policy.disputes import Decision, DisputeFacts, TxnStatus, decide

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data" / "lake" / "gold"
OUT_DIR = ROOT / "data" / "evaluator"
# synthetic = committed; gold = real organizer rows, git-ignored (see the module docstring)
SLICE_PATH = {"synthetic": OUT_DIR / "bank_slice.sql.gz", "gold": OUT_DIR / "bank_slice.gold.sql.gz"}
ENV_PATH = {"synthetic": ROOT / ".env.evaluator.example", "gold": ROOT / ".env.evaluator.gold"}
# The demo must keep working until 2026-10-16; the sessions expire the day after. Override with --expires.
DEFAULT_EXPIRES = "2026-10-17T00:00:00+00:00"
# Simulated "today": the backend's setting, which the dbt var as_of_date must also equal.
AS_OF = get_settings().today
# ops.load_runs needs a loaded_at; pinned to AS_OF (not wall-clock) so the generated SQL stays
# byte-identical across reruns with no data change.
LOADED_AT = datetime.combine(AS_OF, time())
RUN_ID = "evaluator-pack"


@dataclass(frozen=True)
class DemoRow:
    rule: str
    label: str
    not_me: bool
    customer_id: str
    transaction_id: str
    product_id: str
    merchant: str | None
    amount: Decimal
    currency: str
    when: datetime
    status: str
    amount_usd: Decimal
    is_fraud: bool
    is_repeat: bool
    is_card: bool


def _sql_str(value: object | None) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        # `nan` / `inf` unquoted are column references to Postgres: the init script would abort at container start
        if not math.isfinite(value):
            raise SystemExit(f"cannot write a non-finite float ({value}) into the slice")
        return repr(value)
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            raise SystemExit(f"bank.* timestamps are naive local times, got a timezone-aware value: {value}")
        return f"'{value.isoformat(sep=' ', timespec='seconds')}'"
    if isinstance(value, date):
        return f"'{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


def _token(rule: str) -> str:
    return f"demo-{rule.split('-', 1)[0].lower()}-evaluator"


def _staff_token(agent_id: str) -> str:
    return f"staff-{agent_id.split('.')[0]}-evaluator"


def _parquet(name: str) -> str:
    return str(GOLD / f"{name}.parquet")


def _synthetic_rows() -> list[DemoRow]:
    # rule, label, not_me, merchant, status, amount_usd, txn_date, fraud, repeat
    specs: list[tuple[str, str, bool, str, str, Decimal, date, bool, bool]] = [
        (
            "D09-eligible",
            "case opened automatically",
            False,
            "Farmacias del Ahorro",
            "Approved",
            Decimal("25.37"),
            date(2026, 6, 10),
            False,
            False,
        ),
        (
            "D01-declined-not-charged",
            "nothing to dispute: the payment was declined",
            False,
            "Super Ahorro",
            "Declined",
            Decimal("40.00"),
            date(2026, 6, 8),
            False,
            False,
        ),
        (
            "D02-already-reversed",
            "nothing to dispute: already refunded",
            False,
            "Cafe Central",
            "Reversed",
            Decimal("18.50"),
            date(2026, 6, 5),
            False,
            False,
        ),
        (
            "D06-possible-fraud",
            "customer says it wasn't them: card block offer + fraud team",
            True,
            "Cafe",
            "Approved",
            Decimal("25.00"),
            date(2026, 6, 10),
            False,
            False,
        ),
        (
            "D07-above-auto-limit",
            "amount above USD 500: dispute agent",
            False,
            "Electrónica Sur",
            "Approved",
            Decimal("750.00"),
            date(2026, 6, 1),
            False,
            False,
        ),
        (
            "D08-repeat-complainer",
            "repeat complainer: dispute agent",
            False,
            "Mercado Norte",
            "Approved",
            Decimal("55.00"),
            date(2026, 5, 20),
            False,
            True,
        ),
        (
            "D05-outside-window",
            "older than 120 days: explained, agent offered",
            False,
            "Hotel Sol",
            "Approved",
            Decimal("90.00"),
            date(2025, 12, 1),
            False,
            False,
        ),
    ]
    rows: list[DemoRow] = []
    for i, (rule, label, not_me, merchant, status, amount_usd, txn_day, fraud, repeat) in enumerate(specs, start=1):
        prefix = rule[:3].upper()
        when = datetime(txn_day.year, txn_day.month, txn_day.day, 12, 0, 0)
        rows.append(
            DemoRow(
                rule=rule,
                label=label,
                not_me=not_me,
                customer_id=f"CLI-EVAL-{prefix}-{i:02d}",
                transaction_id=f"TRX-EVAL-{prefix}-{i:02d}",
                product_id=f"PRD-EVAL-{prefix}-{i:02d}",
                merchant=merchant,
                amount=amount_usd,
                currency="USD",
                when=when,
                status=status,
                amount_usd=amount_usd,
                is_fraud=fraud,
                is_repeat=repeat,
                is_card=True,
            )
        )
    return rows


def _rows_from_gold() -> list[DemoRow]:
    if not (GOLD / "dispute_scenarios.parquet").is_file():
        raise SystemExit("data/lake/gold/dispute_scenarios.parquet is missing: run `make gold` or pass --synthetic")
    con = duckdb.connect()
    rows: list[DemoRow] = []
    for rule, not_me, label in SCENARIOS:
        picked = _pick(con, rule, not_me)
        if picked is None:
            raise SystemExit(f"no gold scenario for {rule} (customer_says_not_me={not_me})")
        customer_id, txn_id, merchant, amount, currency, _type, when = picked
        detail = con.execute(
            """
            select t.product_id, t.transaction_status, t.amount_usd, t.is_fraud,
                   coalesce(s.is_repeat_complainer, false), coalesce(p.is_card, false)
            from read_parquet(?) t
            left join read_parquet(?) s using (customer_id)
            left join read_parquet(?) p using (product_id)
            where t.transaction_id = ?
            """,
            [_parquet("transactions"), _parquet("customer_complaint_stats"), _parquet("products"), txn_id],
        ).fetchone()
        if detail is None:
            raise SystemExit(f"transaction {txn_id} missing from gold")
        product_id, status, amount_usd, is_fraud, is_repeat, is_card = detail
        when_dt = when if isinstance(when, datetime) else datetime.fromisoformat(str(when))
        rows.append(
            DemoRow(
                rule=rule,
                label=label,
                not_me=not_me,
                customer_id=customer_id,
                transaction_id=txn_id,
                product_id=product_id,
                merchant=merchant,
                amount=Decimal(str(amount)),
                currency=currency,
                when=when_dt,
                status=status,
                amount_usd=Decimal(str(amount_usd)),
                is_fraud=bool(is_fraud),
                is_repeat=bool(is_repeat),
                is_card=bool(is_card),
            )
        )
    return rows


# Each table's column list is the single source of truth: it drives the SELECT list read from
# gold, the dict keys built in _sql_from_synthetic, and the name list _insert validates every row
# against. A column added, renamed or reordered in one place now fails loudly instead of silently
# shifting values into the wrong column.
CUSTOMERS_COLS = ["customer_id", "first_name", "country", "segment", "customer_status", "detected_accent"]
PRODUCTS_COLS = [
    "product_id",
    "customer_id",
    "product_type",
    "is_card",
    "product_number_last4",
    "currency",
    "product_status",
    "opening_date",
    "expiration_date",
    "has_linked_app",
]
TRANSACTIONS_COLS = [
    "transaction_id",
    "customer_id",
    "product_id",
    "transaction_date",
    "process_date",
    "transaction_type",
    "transaction_category",
    "amount",
    "currency",
    "amount_usd",
    "amount_usd_source",
    "channel",
    "merchant_name",
    "merchant_category",
    "transaction_country",
    "transaction_city",
    "transaction_status",
    "response_code",
    "is_fraud",
    "fraud_score",
]
STATS_COLS = ["customer_id", "complaints_total", "complaints_last_90d", "last_complaint_at", "is_repeat_complainer"]
BENCHMARKS_COLS = [
    "category",
    "priority",
    "cases",
    "resolved_cases",
    "median_resolution_days",
    "p75_resolution_days",
    "sla_breach_rate",
    "rejection_rate",
]
SCENARIOS_COLS = [
    "rule_id",
    "customer_says_not_me",
    "transaction_id",
    "route",
    "offer_card_block",
    "customer_id",
    "transaction_status",
    "transaction_date",
    "days_before_as_of",
    "amount_usd",
    "is_fraud",
    "fraud_score",
    "is_repeat_complainer",
]
# Mirrors the ops.load_runs contract pipeline/load_gold.py writes, so pipeline/check_freshness.py
# and anything else that reads "wherever bank.* exists, ops.load_runs exists too" also works here.
LOAD_RUNS_COLS = ["run_id", "loaded_at", "table_name", "row_count", "git_sha"]


def _insert(table: str, cols: list[str], ddl: str, data: list[dict[str, object]], *, schema: str = "bank") -> str:
    lines = [f"create table {schema}.{table} (\n{ddl}\n);"]
    if not data:
        return "\n".join(lines)
    for row in data:
        missing = [c for c in cols if c not in row]
        extra = [k for k in row if k not in cols]
        if missing or extra:
            raise SystemExit(
                f"{schema}.{table}: row {sorted(row)} does not match columns {cols} (missing={missing}, extra={extra})"
            )
    col_names = ", ".join(cols)
    values = ",\n  ".join("(" + ", ".join(_sql_str(row[c]) for c in cols) + ")" for row in data)
    lines.append(f"insert into {schema}.{table} ({col_names}) values\n  {values};")
    return "\n".join(lines)


def _rows(cols: list[str], tuples: list[tuple[object, ...]]) -> list[dict[str, object]]:
    """Zip fetched/built tuples into named rows. strict=True catches an arity drift between a
    SELECT list (or a synthetic tuple) and `cols` immediately, instead of inside _insert."""
    return [dict(zip(cols, row, strict=True)) for row in tuples]


def _sql_from_gold(rows: list[DemoRow]) -> str:
    con = duckdb.connect()
    customer_ids = [r.customer_id for r in rows]
    placeholders = ", ".join("?" for _ in customer_ids)

    def fetch(parquet: str, cols: list[str], *, filter_by_customer: bool = True) -> list[dict[str, object]]:
        sql = f"select {', '.join(cols)} from read_parquet(?)"
        params: list[object] = [_parquet(parquet)]
        if filter_by_customer:
            sql += f" where customer_id in ({placeholders})"
            params.extend(customer_ids)
        return _rows(cols, con.execute(sql, params).fetchall())

    customers = fetch("customers", CUSTOMERS_COLS)
    products = fetch("products", PRODUCTS_COLS)
    transactions = fetch("transactions", TRANSACTIONS_COLS)
    stats = fetch("customer_complaint_stats", STATS_COLS)
    scenarios = fetch("dispute_scenarios", SCENARIOS_COLS)
    benchmarks = fetch("resolution_benchmarks", BENCHMARKS_COLS, filter_by_customer=False)
    return _assemble_sql("gold", customers, products, transactions, stats, benchmarks, scenarios)


def _decide(r: DemoRow) -> Decision:
    """The route comes from the backend policy, as in the gold model, and must agree with the rule the row is for:
    a stale hand-picked amount or date fails here instead of shipping a slice that contradicts the policy."""
    decision = decide(
        DisputeFacts(
            status=TxnStatus(r.status),
            transaction_date=r.when.date(),
            amount_usd=float(r.amount_usd),
            is_fraud=r.is_fraud,
            existing_dispute_ref=None,
            repeat_complainer=r.is_repeat,
            customer_says_not_me=r.not_me,
        ),
        AS_OF,
    )
    if decision.rule_id != r.rule:
        raise SystemExit(f"synthetic row for {r.rule} now decides {decision.rule_id}: update _synthetic_rows")
    return decision


def _sql_from_synthetic(rows: list[DemoRow]) -> str:
    customers = _rows(
        CUSTOMERS_COLS,
        [(r.customer_id, f"Eval{i}", "Mexico", "Mass", "Active", "mexican") for i, r in enumerate(rows, start=1)],
    )
    products = _rows(
        PRODUCTS_COLS,
        [
            (
                r.product_id,
                r.customer_id,
                "Credit Card",
                True,
                "4242",
                r.currency,
                "Active",
                date(2024, 1, 1),
                date(2028, 1, 1),
                True,
            )
            for r in rows
        ],
    )
    transactions = _rows(
        TRANSACTIONS_COLS,
        [
            (
                r.transaction_id,
                r.customer_id,
                r.product_id,
                r.when,
                r.when.date(),
                "Purchase",
                "Retail",
                r.amount,
                r.currency,
                r.amount_usd,
                "native_usd",
                "POS",
                r.merchant,
                "Retail",
                "Mexico",
                "CDMX",
                r.status,
                "00",
                r.is_fraud,
                Decimal("10.0") if r.is_fraud else Decimal("1.0"),
            )
            for r in rows
        ],
    )
    stats = _rows(
        STATS_COLS,
        [
            (
                r.customer_id,
                3 if r.is_repeat else 0,
                2 if r.is_repeat else 0,
                datetime(2026, 5, 1, 10, 0, 0) if r.is_repeat else None,
                r.is_repeat,
            )
            for r in rows
        ],
    )
    decisions = {r.rule: _decide(r) for r in rows}
    scenarios = _rows(
        SCENARIOS_COLS,
        [
            (
                r.rule,
                r.not_me,
                r.transaction_id,
                decisions[r.rule].route.value,
                decisions[r.rule].offer_card_block,
                r.customer_id,
                r.status,
                r.when,
                (AS_OF - r.when.date()).days,
                r.amount_usd,
                r.is_fraud,
                Decimal("1.0"),
                r.is_repeat,
            )
            for r in rows
        ],
    )
    benchmarks = _rows(
        BENCHMARKS_COLS,
        [
            ("Dispute", "all", 100, 40, 15.5, 22.0, 0.2, 0.01),
            ("all", "all", 1000, 400, 12.0, 18.0, 0.15, 0.02),
        ],
    )
    return _assemble_sql("synthetic", customers, products, transactions, stats, benchmarks, scenarios)


def _assemble_sql(
    source: str,
    customers: list[dict[str, object]],
    products: list[dict[str, object]],
    transactions: list[dict[str, object]],
    stats: list[dict[str, object]],
    benchmarks: list[dict[str, object]],
    scenarios: list[dict[str, object]],
) -> str:
    # loaded_at is pinned to AS_OF (not datetime.now()) and git_sha to None: this slice isn't tied
    # to a real pipeline run, and a wall-clock value here would make the gzip output non-reproducible.
    load_runs = _rows(
        LOAD_RUNS_COLS,
        [
            (RUN_ID, LOADED_AT, "customers", len(customers), None),
            (RUN_ID, LOADED_AT, "products", len(products), None),
            (RUN_ID, LOADED_AT, "transactions", len(transactions), None),
            (RUN_ID, LOADED_AT, "customer_complaint_stats", len(stats), None),
            (RUN_ID, LOADED_AT, "resolution_benchmarks", len(benchmarks), None),
            (RUN_ID, LOADED_AT, "dispute_scenarios", len(scenarios), None),
        ],
    )
    parts = [
        "-- Evaluator bank.* slice (infra/evaluator_pack.py).",
        f"-- source: {source}",
        "-- Loaded by Postgres from /docker-entrypoint-initdb.d/ on an empty volume.",
        "create schema if not exists bank;",
        "create schema if not exists ops;",
        _insert(
            "customers",
            CUSTOMERS_COLS,
            """  customer_id text primary key,
  first_name text,
  country text,
  segment text,
  customer_status text,
  detected_accent text""",
            customers,
        ),
        _insert(
            "products",
            PRODUCTS_COLS,
            """  product_id text primary key,
  customer_id text not null,
  product_type text,
  is_card boolean,
  product_number_last4 text,
  currency text,
  product_status text,
  opening_date date,
  expiration_date date,
  has_linked_app boolean""",
            products,
        ),
        _insert(
            "transactions",
            TRANSACTIONS_COLS,
            """  transaction_id text primary key,
  customer_id text not null,
  product_id text not null,
  transaction_date timestamp,
  process_date date,
  transaction_type text,
  transaction_category text,
  amount numeric,
  currency text,
  amount_usd numeric not null,
  amount_usd_source text not null,
  channel text,
  merchant_name text,
  merchant_category text,
  transaction_country text,
  transaction_city text,
  transaction_status text,
  response_code text,
  is_fraud boolean,
  fraud_score numeric""",
            transactions,
        ),
        _insert(
            "customer_complaint_stats",
            STATS_COLS,
            """  customer_id text primary key,
  complaints_total bigint,
  complaints_last_90d bigint,
  last_complaint_at timestamp,
  is_repeat_complainer boolean""",
            stats,
        ),
        _insert(
            "resolution_benchmarks",
            BENCHMARKS_COLS,
            """  category text,
  priority text,
  cases bigint,
  resolved_cases bigint,
  median_resolution_days double precision,
  p75_resolution_days double precision,
  sla_breach_rate double precision,
  rejection_rate double precision,
  primary key (category, priority)""",
            benchmarks,
        ),
        _insert(
            "dispute_scenarios",
            SCENARIOS_COLS,
            """  rule_id text,
  customer_says_not_me boolean,
  transaction_id text,
  route text,
  offer_card_block boolean,
  customer_id text not null,
  transaction_status text,
  transaction_date timestamp,
  days_before_as_of integer,
  amount_usd numeric,
  is_fraud boolean,
  fraud_score numeric,
  is_repeat_complainer boolean,
  primary key (rule_id, customer_says_not_me, transaction_id)""",
            scenarios,
        ),
        _insert(
            "load_runs",
            LOAD_RUNS_COLS,
            """  run_id text not null,
  loaded_at timestamptz not null,
  table_name text not null,
  row_count bigint not null,
  git_sha text,
  primary key (run_id, table_name)""",
            load_runs,
            schema="ops",
        ),
        "create index transactions_customer_date on bank.transactions (customer_id, transaction_date desc);",
        "create index products_customer on bank.products (customer_id);",
        "create index dispute_scenarios_customer on bank.dispute_scenarios (customer_id);",
    ]
    return "\n".join(parts) + "\n"


def render_slice(sql: str) -> bytes:
    # mtime=0: gzip.open() otherwise stamps the wall-clock time into the header, so two runs over
    # byte-identical SQL would produce different compressed bytes.
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as fh:
        fh.write(sql.encode("utf-8"))
    return buf.getvalue()


def render_env(rows: list[DemoRow], *, source: str, expires: str) -> str:
    sessions = {_token(r.rule): {"customer_id": r.customer_id, "expires_at": expires} for r in rows}
    staff = {_staff_token(name): {"agent_id": name, "expires_at": expires} for name in STAFF}
    sheet: list[str] = []
    for r in rows:
        es, pt = _opening(r.not_me, r.merchant, float(r.amount), r.currency, r.when)
        sheet.extend(
            [
                f"# {r.rule} ({r.label})",
                f"#   credential: {_token(r.rule)}",
                f"#   es: {es}",
                f"#   pt: {pt}",
                "#",
            ]
        )
    staff_sheet = [f"#   {name}: {_staff_token(name)}" for name in STAFF]
    return f"""# Evaluator local run. Copy to .env and set MINSKY_LLM_API_KEY only.
# Then: docker compose -f compose.yaml -f compose.evaluator.yaml up --build
# Open https://localhost/chat (accept the local Caddy certificate warning).
# Reset: docker compose -f compose.yaml -f compose.evaluator.yaml down -v
# Generated by infra/evaluator_pack.py ({source}); sessions expire {expires}.
#
# The credentials below are PUBLIC and fixed on purpose: they only work on a stack on localhost
# (compose.evaluator.yaml pins DOMAIN and MINSKY_ENVIRONMENT). Never use this file on a reachable host:
# there, generate random ones with `make demo-sessions`.

DOMAIN=localhost
MINSKY_ENVIRONMENT=local
POSTGRES_USER=minsky
POSTGRES_PASSWORD=minsky
POSTGRES_DB=minsky

MINSKY_LLM_BASE_URL=https://api.openai.com/v1
MINSKY_LLM_MODEL=gpt-6-luna
MINSKY_LLM_API_KEY=

MINSKY_TEST_SESSIONS={json.dumps(sessions, separators=(",", ":"))}
MINSKY_STAFF_SESSIONS={json.dumps(staff, separators=(",", ":"))}

# ---------------------------------------------------------------------------
# Cheat sheet: paste a credential in /chat, then type the es or pt message.
# ---------------------------------------------------------------------------
{chr(10).join(sheet)}
# Agent console: https://localhost/console
{chr(10).join(staff_sheet)}
"""


def _parse_expires(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise SystemExit(f"--expires must be an ISO-8601 timestamp, got {value!r}") from None
    if parsed.tzinfo is None:
        raise SystemExit(f"--expires must carry a timezone (e.g. +00:00), got {value!r}")
    if parsed <= datetime.now(UTC):
        raise SystemExit(f"--expires {value} is already in the past: every credential would fail closed")
    return parsed.isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--gold",
        action="store_true",
        help="read real rows from data/lake/gold and write the git-ignored local files instead of the committed ones",
    )
    parser.add_argument("--expires", default=DEFAULT_EXPIRES, help=f"credential expiry (default {DEFAULT_EXPIRES})")
    args = parser.parse_args(argv)
    expires = _parse_expires(args.expires)
    source = "gold" if args.gold else "synthetic"
    if args.gold:
        rows = _rows_from_gold()
        sql = _sql_from_gold(rows)
    else:
        rows = _synthetic_rows()
        sql = _sql_from_synthetic(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SLICE_PATH[source].write_bytes(render_slice(sql))
    ENV_PATH[source].write_text(render_env(rows, source=source, expires=expires), encoding="utf-8")
    print(f"wrote {SLICE_PATH[source].relative_to(ROOT)} ({SLICE_PATH[source].stat().st_size} bytes, {source})")
    print(f"wrote {ENV_PATH[source].relative_to(ROOT)}")
    print("customers: " + ", ".join(r.customer_id for r in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
