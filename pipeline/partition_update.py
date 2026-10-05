"""Apply a late partition and a corrected partition onto a keyed table (P4.3 fixture helper).

Offline only: proves that late rows appear and corrected rows replace by primary key.
Not the production scheduler.
"""

from __future__ import annotations

import csv
from pathlib import Path


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def apply_late_and_corrected(
    base_csv: Path,
    late_csv: Path,
    corrected_csv: Path,
    *,
    key: str = "transaction_id",
) -> list[dict[str, str]]:
    """Merge three CSV snapshots. Later files win on the same primary key."""
    merged: dict[str, dict[str, str]] = {}
    for path in (base_csv, late_csv, corrected_csv):
        for row in _read_csv(path):
            merged[row[key]] = row
    return [merged[item] for item in sorted(merged)]
