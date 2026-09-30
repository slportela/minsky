"""Load the gold read models into Postgres as bank.*, atomically.

The tables are built in a staging schema (bank_next), indexed, and swapped in with a schema rename
inside one transaction, so the API never sees a half-loaded bank. Every load is recorded in
ops.load_runs (append-only): run id, table, row count, as-of date and git commit, for freshness
checks and lineage.

Usage:
  uv run python pipeline/load_gold.py                      # local compose Postgres (make up)
  GOLD_DATABASE_URL=postgresql://... uv run python pipeline/load_gold.py
"""

import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE = ROOT / "data" / "warehouse.duckdb"
# compose.local.yaml publishes Postgres on 127.0.0.1:${POSTGRES_HOST_PORT:-5433}
DEFAULT_URL = "postgresql://minsky:minsky@127.0.0.1:5433/minsky"

# table -> (primary key, extra indexes) in Postgres
TABLES: dict[str, tuple[str, list[str]]] = {
    "customers": ("customer_id", []),
    "products": ("product_id", ["customer_id"]),
    "transactions": ("transaction_id", ["customer_id, transaction_date desc", "product_id"]),
    "customer_complaint_stats": ("customer_id", []),
    "resolution_benchmarks": ("category, priority", []),
    "dispute_scenarios": ("rule_id, customer_says_not_me, transaction_id", ["customer_id"]),
}


def git_sha() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
        return out.stdout.strip() or None
    except OSError:
        return None


def main() -> int:
    url = os.environ.get("GOLD_DATABASE_URL", DEFAULT_URL)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    # in-memory session: the warehouse attached read-only, Postgres read-write (a read-only main
    # database would make every attached database read-only too)
    con = duckdb.connect()
    con.execute("install postgres; load postgres")
    con.execute(f"attach '{WAREHOUSE}' as wh (read_only)")
    con.execute(f"attach '{url}' as pg (type postgres)")

    def pg(sql: str) -> None:
        con.execute("call postgres_execute('pg', ?)", [sql])

    pg("drop schema if exists bank_next cascade; create schema bank_next; create schema if not exists ops;")
    pg("""create table if not exists ops.load_runs (
            run_id text not null, loaded_at timestamptz not null, table_name text not null,
            row_count bigint not null, git_sha text, primary key (run_id, table_name))""")

    counts = {}
    for table, (pk, indexes) in TABLES.items():
        t0 = time.time()
        con.execute(f"create table pg.bank_next.{table} as select * from wh.bank.{table}")
        pg(f"alter table bank_next.{table} add primary key ({pk})")
        for i, cols in enumerate(indexes):
            pg(f"create index {table}_idx{i} on bank_next.{table} ({cols})")
        counts[table] = con.execute(f"select count(*) from wh.bank.{table}").fetchone()[0]
        print(f"  bank.{table:26s} {counts[table]:>10,} rows  {time.time() - t0:5.1f}s", flush=True)

    loaded_at = datetime.now(UTC).isoformat()
    values = ", ".join(f"('{run_id}', '{loaded_at}', '{t}', {n}, {repr(git_sha()) if git_sha() else 'null'})"
                       for t, n in counts.items())
    # the swap and the run record commit together: readers see the old bank or the new one, never a mix
    pg(f"""begin;
           drop schema if exists bank cascade;
           alter schema bank_next rename to bank;
           insert into ops.load_runs values {values};
           commit;""")
    pg("analyze bank.transactions; analyze bank.products; analyze bank.customers;")
    print(f"run {run_id}: loaded {len(counts)} tables into bank.* ({url.split('@')[-1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
