"""Compare matched dev workloads from durable trial evidence; never mix unmatched suites."""

import argparse
import json
from pathlib import Path
from statistics import median

from evals.metrics import Rate


def load_run(path: Path) -> tuple[dict, list[dict]]:
    metadata = json.loads((path / "metadata.json").read_text())
    records = [json.loads(p.read_text()) for p in sorted(path.glob("*-*.json"))]
    return metadata, records


def summarize(records: list[dict]) -> dict:
    errors = sum(row["status"] == "error" for row in records)
    passed = sum(row["status"] == "passed" for row in records)
    grouped: dict[str, list[dict]] = {}
    for row in records:
        grouped.setdefault(row["case_id"], []).append(row)
    complete = [rows for rows in grouped.values() if all(row["status"] != "error" for row in rows)]
    all_passed = sum(all(row["status"] == "passed" for row in rows) for rows in complete)
    latency = sorted(row["latency_ms"] for row in records if row["status"] != "error")
    return {
        "pass_rate": str(Rate(passed, len(records) - errors)),
        "infrastructure_errors": f"{errors}/{len(records)}",
        "all_trials_passed": str(Rate(all_passed, len(complete))),
        "case_groups_excluded_for_errors": len(grouped) - len(complete),
        "trial_latency_ms_p50": median(latency) if latency else None,
        "trial_latency_ms_p95_nearest_rank": latency[max(0, (95 * len(latency) + 99) // 100 - 1)] if latency else None,
        "cases": {
            key: {"passed": sum(row["status"] == "passed" for row in rows), "attempted": len(rows)}
            for key, rows in grouped.items()
        },
        "limitation": "draft dev scripts and partial safety; no held-out generalization claim",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    before_meta, before = load_run(args.before)
    after_meta, after = load_run(args.after)
    for field in ("cases", "model", "mode", "database", "today", "trials_per_case"):
        if before_meta[field] != after_meta[field]:
            parser.error(f"unmatched {field}; comparison refused")
    expected = len(before_meta["cases"]) * before_meta["trials_per_case"]
    for records in (before, after):
        counts = {case: sum(row["case_id"] == case for row in records) for case in before_meta["cases"]}
        if len(records) != expected or any(count != before_meta["trials_per_case"] for count in counts.values()):
            parser.error("trial evidence is incomplete or has duplicate/mismatched case records")
    result = {
        "before_metadata": before_meta,
        "after_metadata": after_meta,
        "before": summarize(before),
        "after": summarize(after),
    }
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
