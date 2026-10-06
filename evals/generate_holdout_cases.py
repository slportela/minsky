"""Write the test split: 50 random real charges, each one a customer trying to dispute it.

Everything before this split counts as training: the dev cases were written and fixed against, and val
was used to select. These charges come from a seeded random draw over gold that nobody has looked
at. 45 come from the 120-day dispute window. 5 are older approved charges without the fraud flag and
without a "not me" claim, so each one is a D05 refusal (decide() checks status and fraud before the window).
The same draw simulates the "it wasn't me" claim on about a quarter of the recent approved charges. Labels come from
`policy.disputes.decide` on the real row (AGENTS rule 5), and the runner re-checks them. Customer
messages use the val templates, in Spanish: the bank has no Portuguese data.

A human runs this once (rule 4: agents never generate into evals/cases/test). It refuses to write
into a non-empty folder, because changing the split is a new versioned release.

    uv run python -m evals.generate_holdout_cases      # needs data/lake/gold (make gold)
"""

from __future__ import annotations

import argparse
import random
from datetime import date, timedelta
from pathlib import Path

import duckdb
import yaml

from evals.generate_val_cases import GOLD, ROOT, SPECS, Spec, case_from_row
from evals.schema import load_cases
from minsky_api.config import get_settings
from minsky_api.policy.disputes import DisputeFacts, TxnStatus, decide

SEED = 20261005
RECENT, OLD = 45, 5
NOT_ME_SHARE = 0.25
OUT = ROOT / "evals" / "cases" / "test"
_SPEC = {(spec.rule, spec.not_me): spec for spec in SPECS}

_JOINS = """
    from '{gold}/transactions.parquet' t
    join '{gold}/customers.parquet' c using (customer_id)
    join '{gold}/products.parquet' p on p.product_id = t.product_id
    left join '{gold}/customer_complaint_stats.parquet' s on s.customer_id = t.customer_id
"""
_ROW = (
    """
    select t.customer_id, t.transaction_id, t.product_id, t.merchant_name, t.amount, t.currency, t.amount_usd,
           t.transaction_status, t.transaction_date, t.is_fraud, coalesce(s.is_repeat_complainer, false),
           c.country, p.is_card
"""
    + _JOINS
)


def _used_customers() -> set[str]:
    cases = [case for split in ("dev", "val") for case in load_cases(ROOT / "evals" / "cases" / split)]
    return {case.session.customer_id for case in cases if case.session.customer_id}


def _pool(con: duckdb.DuckDBPyConnection, gold: Path, cutoff: date, *, recent: bool) -> list[tuple[str, str]]:
    # Sorted ids, so the seeded shuffle alone decides the draw (DuckDB's hash() is not stable across versions).
    # The same joins as _ROW: every drawn charge can be built into a case.
    where = (
        "cast(t.transaction_date as date) >= ?"
        if recent
        else "cast(t.transaction_date as date) < ? and t.transaction_status = 'Approved' and not t.is_fraud"
    )
    return con.execute(
        "select t.transaction_id, t.customer_id"
        + _JOINS.format(gold=gold)
        + f" where t.merchant_name is not null and {where} order by t.transaction_id",
        [cutoff],
    ).fetchall()


def _draw(pool: list[tuple[str, str]], count: int, used: set[str], rng: random.Random) -> list[str]:
    rng.shuffle(pool)
    chosen: list[str] = []
    for txn, customer in pool:
        if customer in used:
            continue
        used.add(customer)  # one charge per customer, and never a dev or val customer
        chosen.append(txn)
        if len(chosen) == count:
            return chosen
    raise SystemExit(f"gold has only {len(chosen)} eligible charges for a draw of {count}")


def _spec(row: tuple, not_me: bool, today: date) -> Spec:
    facts = DisputeFacts(
        status=TxnStatus(row[7]),
        transaction_date=row[8].date(),
        amount_usd=float(row[6]),
        is_fraud=bool(row[9]),
        existing_dispute_ref=None,  # no prior disputes in gold, so D04 cannot occur
        repeat_complainer=bool(row[10]),
        customer_says_not_me=not_me,
    )
    return _SPEC[(decide(facts, today).rule_id, not_me)]


def build_cases(gold: Path = GOLD, seed: int = SEED, used: set[str] | None = None) -> list[dict]:
    today = get_settings().today
    cutoff = today - timedelta(days=120)
    rng = random.Random(seed)
    taken = set(_used_customers() if used is None else used)
    con = duckdb.connect()
    ids = [
        *_draw(_pool(con, gold, cutoff, recent=True), RECENT, taken, rng),
        *_draw(_pool(con, gold, cutoff, recent=False), OLD, taken, rng),
    ]
    query = _ROW.format(gold=gold) + " where list_contains(?, t.transaction_id)"
    rows = {row[1]: row for row in con.execute(query, [ids]).fetchall()}
    con.close()
    cases = []
    for number, txn in enumerate(ids, start=1):
        row = rows[txn]
        not_me = number <= RECENT and row[7] == TxnStatus.APPROVED and rng.random() < NOT_ME_SHARE
        cases.append(
            case_from_row(
                row,
                _spec(row, not_me, today),
                split="test",
                case_id=f"holdout-{number:02d}",
                language="es",
                notes=f"Generated by evals/generate_holdout_cases.py (seed {seed}): a random gold charge, labeled by "
                "the policy's decision on it. Test split: headline numbers only, never tune on it.",
            )
        )
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the test split: 50 random real charges.")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    if args.out.is_dir() and any(args.out.iterdir()):
        raise SystemExit(f"{args.out} is not empty: a new test split is a new versioned release")
    if not (GOLD / "transactions.parquet").is_file():
        raise SystemExit("data/lake/gold is missing: run make silver and make gold first")
    cases = build_cases(seed=args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    for case in cases:
        (args.out / f"{case['id']}.yaml").write_text(
            yaml.safe_dump(case, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
    print(f"wrote {len(cases)} test cases to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
