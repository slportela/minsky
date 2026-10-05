"""Late rows appear and corrected rows replace by primary key (P4.3 fixture)."""

from __future__ import annotations

import csv
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "pipeline" / "fixtures" / "late_corrected"


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_late_and_corrected_partition_merge() -> None:
    merged: dict[str, dict[str, str]] = {}
    for path in (
        FIXTURES / "base_transactions.csv",
        FIXTURES / "late_transactions.csv",
        FIXTURES / "corrected_transactions.csv",
    ):
        for row in _rows(path):
            merged[row["transaction_id"]] = row

    by_id = merged
    assert set(by_id) == {"T1", "T2", "T3"}
    assert float(by_id["T1"]["amount_usd"]) == 25.5
    assert float(by_id["T2"]["amount_usd"]) == 10.0
    assert float(by_id["T3"]["amount_usd"]) == 40.0
