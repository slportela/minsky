"""The evaluator slice: the committed files are fresh, synthetic and consistent with the backend models.

infra/ is not a package, so the generator is loaded by path (its `demo_sessions` import is a sibling).
"""

import gzip
import importlib.util
import json
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from minsky_api.policy.disputes import Route
from minsky_api.store import models

INFRA = Path(__file__).resolve().parents[1] / "infra"
sys.path.insert(0, str(INFRA))
_spec = importlib.util.spec_from_file_location("evaluator_pack", INFRA / "evaluator_pack.py")
assert _spec and _spec.loader
pack = importlib.util.module_from_spec(_spec)
sys.modules["evaluator_pack"] = pack
_spec.loader.exec_module(pack)

ROOT = pack.ROOT


def _committed_sql() -> str:
    return gzip.decompress((ROOT / "data/evaluator/bank_slice.sql.gz").read_bytes()).decode("utf-8")


def test_committed_slice_is_what_the_generator_writes() -> None:
    rows = pack._synthetic_rows()
    sql = pack._sql_from_synthetic(rows)
    assert _committed_sql() == sql, "stale slice: run `make evaluator-pack`"
    assert (ROOT / ".env.evaluator.example").read_text() == pack.render_env(
        rows, source="synthetic", expires=pack.DEFAULT_EXPIRES
    ), "stale env file: run `make evaluator-pack`"


def test_committed_slice_is_synthetic() -> None:
    # real organizer rows must never reach git (AGENTS.md rule 7): the gold variant has its own ignored files
    assert "-- source: synthetic" in _committed_sql().splitlines()[:3]
    assert pack.SLICE_PATH["gold"] != pack.SLICE_PATH["synthetic"]


def test_generation_is_reproducible() -> None:
    rows = pack._synthetic_rows()
    first = pack.render_slice(pack._sql_from_synthetic(rows))
    assert first == pack.render_slice(pack._sql_from_synthetic(rows))


@pytest.mark.parametrize(
    ("cols", "model"),
    [
        (pack.CUSTOMERS_COLS, models.Customer),
        (pack.PRODUCTS_COLS, models.Product),
        (pack.TRANSACTIONS_COLS, models.Transaction),
        (pack.STATS_COLS, models.CustomerComplaintStats),
        (pack.BENCHMARKS_COLS, models.ResolutionBenchmark),
        (pack.SCENARIOS_COLS, models.DisputeScenario),
    ],
)
def test_slice_columns_match_the_backend_models(cols: list[str], model: type) -> None:
    assert sorted(cols) == sorted(model.model_fields)


def test_every_scenario_row_decides_the_rule_it_is_for() -> None:
    for row in pack._synthetic_rows():
        assert pack._decide(row).rule_id == row.rule


def test_scenario_routes_come_from_the_policy() -> None:
    routes = {r.rule: pack._decide(r).route for r in pack._synthetic_rows()}
    assert routes["D09-eligible"] == Route.OPEN_DISPUTE
    assert routes["D06-possible-fraud"] == Route.ESCALATE_FRAUD
    assert pack._decide(next(r for r in pack._synthetic_rows() if r.rule.startswith("D06"))).offer_card_block


def test_simulated_today_is_the_backends() -> None:
    from minsky_api.config import get_settings

    assert pack.AS_OF == get_settings().today


def test_credentials_are_unique_and_parse() -> None:
    rows = pack._synthetic_rows()
    env = pack.render_env(rows, source="synthetic", expires=pack.DEFAULT_EXPIRES)
    sessions = json.loads(re.search(r"^MINSKY_TEST_SESSIONS=(.*)$", env, re.M).group(1))  # type: ignore[union-attr]
    staff = json.loads(re.search(r"^MINSKY_STAFF_SESSIONS=(.*)$", env, re.M).group(1))  # type: ignore[union-attr]
    assert len(sessions) == len(rows) == len({r.customer_id for r in rows})
    assert len(staff) == len(pack.STAFF)
    assert {v["expires_at"] for v in [*sessions.values(), *staff.values()]} == {pack.DEFAULT_EXPIRES}


def test_default_expiry_covers_the_demo_window() -> None:
    assert datetime.fromisoformat(pack.DEFAULT_EXPIRES) > datetime.fromisoformat("2026-10-16T23:59:59+00:00")


def test_expires_must_be_aware_and_in_the_future() -> None:
    with pytest.raises(SystemExit):
        pack._parse_expires("2099-01-01T00:00:00")
    with pytest.raises(SystemExit):
        pack._parse_expires("2020-01-01T00:00:00+00:00")
    with pytest.raises(SystemExit):
        pack._parse_expires("not a date")
    assert pack._parse_expires("2099-01-01T00:00:00+00:00") == "2099-01-01T00:00:00+00:00"


def test_sql_literals() -> None:
    assert pack._sql_str(None) == "NULL"
    assert pack._sql_str(True) == "TRUE"
    assert pack._sql_str("O'Brien") == "'O''Brien'"
    assert pack._sql_str(Decimal("1.50")) == "1.50"
    assert pack._sql_str(0.1) == "0.1"
    assert pack._sql_str(date(2026, 6, 18)) == "'2026-06-18'"
    assert pack._sql_str(datetime(2026, 6, 18, 12)) == "'2026-06-18 12:00:00'"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), datetime.fromisoformat("2026-06-18T12:00:00+00:00")])
def test_sql_literal_refuses_values_postgres_would_misread(bad: object) -> None:
    with pytest.raises(SystemExit):
        pack._sql_str(bad)


def test_insert_rejects_rows_that_do_not_match_the_columns() -> None:
    with pytest.raises(SystemExit):
        pack._insert("t", ["a", "b"], "a int, b int", [{"a": 1}])
    with pytest.raises(ValueError):  # zip(strict=True): a SELECT list and its column list drifted apart
        pack._rows(["a", "b"], [(1,)])
