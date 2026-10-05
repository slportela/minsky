"""Late rows appear and corrected rows replace by primary key (P4.3 fixture)."""

from __future__ import annotations

from pathlib import Path

from pipeline.partition_update import apply_late_and_corrected

FIXTURES = Path(__file__).resolve().parents[1] / "pipeline" / "fixtures" / "late_corrected"


def test_late_and_corrected_partition_merge() -> None:
    rows = apply_late_and_corrected(
        FIXTURES / "base_transactions.csv",
        FIXTURES / "late_transactions.csv",
        FIXTURES / "corrected_transactions.csv",
    )
    by_id = {row["transaction_id"]: row for row in rows}
    assert set(by_id) == {"T1", "T2", "T3"}
    assert float(by_id["T1"]["amount_usd"]) == 25.5
    assert float(by_id["T2"]["amount_usd"]) == 10.0
    assert float(by_id["T3"]["amount_usd"]) == 40.0
