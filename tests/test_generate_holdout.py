"""The hold-out draw: seeded, policy-labeled, one charge per customer, never a dev or val customer."""

from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest
import yaml

from evals import generate_holdout_cases
from evals.generate_holdout_cases import CASES, OLD, RECENT, _used_customers, build_cases, main
from evals.schema import Case
from evals.world import check_label, facts_from_case
from minsky_api.config import get_settings

_STATUSES = ("Approved", "Approved", "Approved", "Declined", "Reversed", "Pending")


def _gold(root: Path, customers: int = 120) -> Path:
    """Two charges per customer, one inside the 120-day window and one outside, with mixed facts."""
    today = datetime.combine(get_settings().today, datetime.min.time())
    txns, people, products, stats = [], [], [], []
    for i in range(customers):
        customer, product = f"CLI-{i:03d}", f"PRD-{i:03d}"
        people.append((customer, ("Mexico", "Colombia", "Argentina")[i % 3]))
        products.append((product, customer, i % 4 != 0))
        stats.append((customer, i % 7 == 0))
        for kind, days in (("R", 1 + i % 110), ("O", 130 + i)):
            amount = 40 + (i * 37) % 900
            txns.append(
                (f"TRX-{kind}{i:03d}", customer, product, f"Comercio {i}", amount, "USD", amount,
                 _STATUSES[i % len(_STATUSES)], today - timedelta(days=days, hours=3), i % 11 == 0)
            )  # fmt: skip
    con = duckdb.connect()
    tables = {
        "transactions": (
            "transaction_id varchar, customer_id varchar, product_id varchar, merchant_name varchar, "
            "amount decimal(12,2), currency varchar, amount_usd decimal(12,2), transaction_status varchar, "
            "transaction_date timestamp, is_fraud boolean",
            txns,
        ),
        "customers": ("customer_id varchar, country varchar", people),
        "products": ("product_id varchar, customer_id varchar, is_card boolean", products),
        "customer_complaint_stats": ("customer_id varchar, is_repeat_complainer boolean", stats),
    }
    for name, (columns, rows) in tables.items():
        con.execute(f"create table {name} ({columns})")
        con.executemany(f"insert into {name} values ({', '.join('?' * len(rows[0]))})", rows)
        con.execute(f"copy {name} to '{root}/{name}.parquet' (format parquet)")
    con.close()
    return root


def test_the_same_seed_draws_the_same_cases(tmp_path: Path) -> None:
    gold = _gold(tmp_path)
    first = build_cases(gold, seed=7, used=set())
    assert first == build_cases(gold, seed=7, used=set())
    assert first != build_cases(gold, seed=8, used=set())


def test_the_draw_is_45_recent_and_5_old_charges_one_per_customer(tmp_path: Path) -> None:
    cases = build_cases(_gold(tmp_path), seed=7, used=set())
    txns = [case["user_scenario"]["known_info"]["transaction_id"] for case in cases]
    assert sum(txn.startswith("TRX-R") for txn in txns) == RECENT
    assert sum(txn.startswith("TRX-O") for txn in txns) == OLD
    customers = [case["session"]["customer_id"] for case in cases]
    assert len(set(customers)) == len(customers) == RECENT + OLD


def test_every_old_charge_is_a_d05_refusal(tmp_path: Path) -> None:
    """decide() checks status and fraud before the window, so only approved, unflagged old charges are D05."""
    cases = build_cases(_gold(tmp_path), seed=7, used=set())
    old = [case for case in cases if case["user_scenario"]["known_info"]["transaction_id"].startswith("TRX-O")]
    assert [case["user_scenario"]["known_info"]["rule_id"] for case in old] == ["D05-outside-window"] * OLD


def test_a_charge_without_its_product_is_never_drawn(tmp_path: Path) -> None:
    gold = _gold(tmp_path)
    con = duckdb.connect()
    con.execute(f"copy (select * from '{gold}/products.parquet' where product_id >= 'PRD-040') "
                f"to '{gold}/products.parquet' (format parquet)")  # fmt: skip
    con.close()
    cases = build_cases(gold, seed=7, used=set())
    assert all(case["session"]["customer_id"] >= "CLI-040" for case in cases)


def test_dev_and_val_customers_are_never_drawn(tmp_path: Path) -> None:
    used = {f"CLI-{i:03d}" for i in range(30)}
    cases = build_cases(_gold(tmp_path), seed=7, used=set(used))
    assert not {case["session"]["customer_id"] for case in cases} & used


def test_every_label_is_the_policy_decision(tmp_path: Path) -> None:
    for data in build_cases(_gold(tmp_path), seed=7, used=set()):
        case = Case.model_validate(data)
        assert case.split == "test"
        check_label(case, facts_from_case(case))


def test_a_small_gold_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="eligible charges"):
        build_cases(_gold(tmp_path, customers=30), seed=7, used=set())


def test_it_refuses_to_overwrite_a_released_split(tmp_path: Path) -> None:
    (tmp_path / "holdout-01.yaml").write_text("id: holdout-01\n")
    with pytest.raises(SystemExit, match="not empty"):
        main(["--out", str(tmp_path)])


def test_the_customers_of_dev_and_val_cases_are_read_from_the_cases_folder(tmp_path: Path) -> None:
    """load_cases wants the folder of split folders: a regression here returned no customers at all."""
    sample = yaml.safe_load(next((CASES / "val").glob("*.yaml")).read_text(encoding="utf-8"))
    for split, customer in (("dev", "CLI-001"), ("val", "CLI-002"), ("test", "CLI-003")):
        case = {
            **sample,
            "id": f"{split}-sample",
            "split": split,
            "session": {"state": "valid", "customer_id": customer},
        }
        (tmp_path / split).mkdir()
        (tmp_path / split / "sample.yaml").write_text(yaml.safe_dump(case), encoding="utf-8")
    assert _used_customers(tmp_path) == {"CLI-001", "CLI-002"}
    assert _used_customers() == _used_customers(CASES) != set()


def test_a_folder_with_only_a_gitkeep_counts_as_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "test"
    out.mkdir()
    (out / ".gitkeep").touch()
    monkeypatch.setattr(generate_holdout_cases, "GOLD", _gold(tmp_path))
    monkeypatch.setattr(generate_holdout_cases, "_used_customers", set)
    assert main(["--out", str(out)]) == 0
    assert len(list(out.glob("holdout-*.yaml"))) == RECENT + OLD
