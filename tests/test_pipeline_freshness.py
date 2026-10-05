"""Freshness check labels tables whose newest ops.load_runs row is too old."""

from datetime import UTC, datetime, timedelta

import pytest

from pipeline.check_freshness import max_age_hours, stale_tables


def test_stale_tables_when_load_is_too_old() -> None:
    now = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    rows = [
        ("transactions", now - timedelta(hours=10)),
        ("customers", now - timedelta(hours=10)),
        ("products", now - timedelta(days=10)),
    ]
    assert stale_tables(rows, now=now, max_age=timedelta(hours=168)) == ["products"]


def test_fresh_loads_pass() -> None:
    now = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    rows = [
        ("transactions", now - timedelta(hours=1)),
        ("customers", now - timedelta(hours=1)),
        ("products", now - timedelta(hours=1)),
        ("products", now - timedelta(days=30)),  # older run ignored
    ]
    assert stale_tables(rows, now=now, max_age=timedelta(hours=168)) == []


def test_missing_required_table_is_stale() -> None:
    now = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    rows = [("transactions", now)]
    assert stale_tables(rows, now=now, max_age=timedelta(hours=168)) == ["customers", "products"]


def test_max_age_hours_rejects_non_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINSKY_FRESHNESS_MAX_AGE_HOURS", "7d")
    with pytest.raises(ValueError, match="invalid MINSKY_FRESHNESS_MAX_AGE_HOURS"):
        max_age_hours()


def test_max_age_hours_rejects_non_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINSKY_FRESHNESS_MAX_AGE_HOURS", "0")
    with pytest.raises(ValueError, match="must be > 0"):
        max_age_hours()
