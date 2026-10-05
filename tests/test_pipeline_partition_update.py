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


def test_bom_prefixed_csv_headers_are_readable(tmp_path: Path) -> None:
    base = tmp_path / "base.csv"
    late = tmp_path / "late.csv"
    corrected = tmp_path / "corrected.csv"
    base.write_bytes(b"\xef\xbb\xbftransaction_id,amount_usd\nT1,1.0\n")
    late.write_text("transaction_id,amount_usd\nT2,2.0\n", encoding="utf-8")
    corrected.write_text("transaction_id,amount_usd\nT1,9.0\n", encoding="utf-8")
    rows = apply_late_and_corrected(base, late, corrected)
    by_id = {row["transaction_id"]: row for row in rows}
    assert float(by_id["T1"]["amount_usd"]) == 9.0
    assert float(by_id["T2"]["amount_usd"]) == 2.0
