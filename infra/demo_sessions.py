"""Print demo credentials for `make up`: one customer per dispute-policy rule, plus agent-console staff.

Reads the gold Parquet that `make gold` writes (data/lake/gold), so every demo customer is a real
customer of the dataset whose transaction triggers that rule. Prints two lines to paste into .env
(MINSKY_TEST_SESSIONS, MINSKY_STAFF_SESSIONS) and a cheat sheet with what to type in the chat.

    uv run python infra/demo_sessions.py --days 14 > /tmp/demo.txt

Tokens are random and expire; they are synthetic test identities, never real ones (ADR 0009). The
output is a secret for the demo: share it only with the people running the demo.
"""

from __future__ import annotations

import argparse
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

GOLD = Path(__file__).resolve().parents[1] / "data" / "lake" / "gold"

# (rule, customer says "it wasn't me"): the demo path each one shows
SCENARIOS = (
    ("D09-eligible", False, "case opened automatically"),
    ("D01-declined-not-charged", False, "nothing to dispute: the payment was declined"),
    ("D02-already-reversed", False, "nothing to dispute: already refunded"),
    ("D06-possible-fraud", True, "customer says it wasn't them: card block offer + fraud team"),
    ("D07-above-auto-limit", False, "amount above USD 500: dispute agent"),
    ("D08-repeat-complainer", False, "repeat complainer: dispute agent"),
    ("D05-outside-window", False, "older than 120 days: explained, agent offered"),
)
STAFF = ("ana.fraude", "luis.disputas")


def _pick(con: duckdb.DuckDBPyConnection, rule: str, not_me: bool) -> tuple | None:
    # Prefer charges a customer can describe: on a card (so the fraud path shows the block offer), with a
    # merchant name, recent.
    return con.execute(
        f"""
        select s.customer_id, s.transaction_id, t.merchant_name, t.amount, t.currency, t.transaction_type,
               t.transaction_date
        from '{GOLD}/dispute_scenarios.parquet' s
        join '{GOLD}/transactions.parquet' t using (transaction_id)
        join '{GOLD}/products.parquet' p on p.product_id = t.product_id
        where s.rule_id = ? and s.customer_says_not_me = ?
        order by p.is_card is not true, t.merchant_name is null,
                 t.transaction_type not in ('Purchase', 'Withdrawal'), t.transaction_date desc, s.transaction_id
        limit 1
        """,
        [rule, not_me],
    ).fetchone()


def _opening(not_me: bool, merchant: str | None, amount: float, currency: str, when: datetime) -> tuple[str, str]:
    where_es = f" en {merchant}" if merchant else ""
    where_pt = f" em {merchant}" if merchant else ""
    day = when.date().isoformat()
    if not_me:
        return (
            f"Tengo un cargo de {amount:.2f} {currency}{where_es} del {day} que yo no hice.",
            f"Tenho uma cobrança de {amount:.2f} {currency}{where_pt} do dia {day} que eu não fiz.",
        )
    return (
        f"Quiero reclamar un cargo de {amount:.2f} {currency}{where_es} del {day}, el monto no es correcto.",
        f"Quero contestar uma cobrança de {amount:.2f} {currency}{where_pt} do dia {day}, o valor está errado.",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Print demo credentials for make up.")
    parser.add_argument("--days", type=int, default=14, help="credential lifetime in days (default 14)")
    args = parser.parse_args()
    if not (GOLD / "dispute_scenarios.parquet").is_file():
        raise SystemExit("data/lake/gold/dispute_scenarios.parquet is missing: run `make silver` and `make gold` first")

    expires = (datetime.now(UTC) + timedelta(days=args.days)).replace(microsecond=0).isoformat()
    con = duckdb.connect()
    sessions: dict[str, dict[str, str]] = {}
    sheet: list[str] = []
    for rule, not_me, label in SCENARIOS:
        row = _pick(con, rule, not_me)
        if row is None:
            sheet.append(f"- {rule}: no scenario in gold (skipped)")
            continue
        customer_id, _txn_id, merchant, amount, currency, _type, when = row
        token = f"demo-{rule[:3].lower()}-{secrets.token_urlsafe(12)}"
        sessions[token] = {"customer_id": customer_id, "expires_at": expires}
        es, pt = _opening(not_me, merchant, float(amount), currency, when)
        sheet.append(f"- {rule} ({label})\n    credential: {token}\n    es: {es}\n    pt: {pt}")
    staff = {
        f"staff-{name.split('.')[0]}-{secrets.token_urlsafe(12)}": {"agent_id": name, "expires_at": expires}
        for name in STAFF
    }

    print("# Paste these two lines into .env, then `make up` (they expire " + expires + ")")
    print("MINSKY_TEST_SESSIONS=" + json.dumps(sessions, separators=(",", ":")))
    print("MINSKY_STAFF_SESSIONS=" + json.dumps(staff, separators=(",", ":")))
    print("\n# Demo cheat sheet: open https://localhost/chat, paste a credential, type the message")
    print("\n".join(sheet))
    print("\n# Agent console: https://localhost/console")
    for token, record in staff.items():
        print(f"- {record['agent_id']}: {token}")


if __name__ == "__main__":
    main()
