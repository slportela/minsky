"""Fail if ops.load_runs is missing or older than MINSKY_FRESHNESS_MAX_AGE_HOURS.

Usage:
  uv run python pipeline/check_freshness.py
  GOLD_DATABASE_URL=postgresql://... uv run python pipeline/check_freshness.py
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

import psycopg

DEFAULT_URL = "postgresql://minsky:minsky@127.0.0.1:5433/minsky"
REQUIRED_TABLES = ("transactions", "customers", "products")


def max_age_hours() -> float:
    raw = os.environ.get("MINSKY_FRESHNESS_MAX_AGE_HOURS", "168")
    return float(raw)


def stale_tables(
    rows: list[tuple[str, datetime]],
    *,
    now: datetime,
    max_age: timedelta,
    required: tuple[str, ...] = REQUIRED_TABLES,
) -> list[str]:
    """Return required table names whose newest loaded_at is missing or too old."""
    newest: dict[str, datetime] = {}
    for table_name, loaded_at in rows:
        previous = newest.get(table_name)
        if previous is None or loaded_at > previous:
            newest[table_name] = loaded_at
    stale: list[str] = []
    for table in required:
        loaded_at = newest.get(table)
        if loaded_at is None or now - loaded_at > max_age:
            stale.append(table)
    return stale


def main() -> int:
    url = os.environ.get("GOLD_DATABASE_URL", DEFAULT_URL)
    age = timedelta(hours=max_age_hours())
    now = datetime.now(UTC)
    try:
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select table_name, loaded_at
                    from ops.load_runs
                    where table_name = any(%s)
                    """,
                    [list(REQUIRED_TABLES)],
                )
                rows = []
                for name, loaded_at in cur.fetchall():
                    if loaded_at.tzinfo is None:
                        loaded_at = loaded_at.replace(tzinfo=UTC)
                    rows.append((str(name), loaded_at))
    except Exception as exc:  # noqa: BLE001 — fail loud with the connection error
        print(f"freshness-check: cannot read ops.load_runs: {exc}", file=sys.stderr)
        return 2

    bad = stale_tables(rows, now=now, max_age=age)
    if bad:
        print(
            f"freshness-check: stale or missing loads for {', '.join(bad)} (max age {age.total_seconds() / 3600:.0f}h)",
            file=sys.stderr,
        )
        return 1
    print(f"freshness-check: ok ({len(REQUIRED_TABLES)} tables within {age.total_seconds() / 3600:.0f}h)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
