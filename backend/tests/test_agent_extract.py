"""Extraction details: relative days are turned into calendar days by code, never by the model."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from minsky_api.agent.extract import DisputeDetails, resolve_relative_dates

TODAY = date(2026, 6, 18)


@pytest.mark.parametrize(("days_ago", "day"), [(0, date(2026, 6, 18)), (1, date(2026, 6, 17)), (2, date(2026, 6, 16))])
def test_a_relative_day_becomes_that_calendar_day(days_ago: int, day: date):
    resolved = resolve_relative_dates(DisputeDetails(days_ago=days_ago), TODAY)
    assert (resolved.date_from, resolved.date_to) == (day, day)
    assert resolved.days_ago is None


def test_a_written_date_wins_over_a_relative_one():
    written = DisputeDetails(days_ago=1, date_from=date(2026, 6, 1), date_to=date(2026, 6, 5))
    resolved = resolve_relative_dates(written, TODAY)
    assert (resolved.date_from, resolved.date_to) == (date(2026, 6, 1), date(2026, 6, 5))
    assert resolved.days_ago is None


@pytest.mark.parametrize("days_ago", [-1, 367, 100000])
def test_an_implausible_relative_day_is_dropped_not_guessed(days_ago: int):
    resolved = resolve_relative_dates(DisputeDetails(days_ago=days_ago), TODAY)
    assert resolved.date_from is None and resolved.date_to is None and resolved.days_ago is None


def test_details_without_a_relative_day_are_returned_unchanged():
    details = DisputeDetails(merchant="Cafe", amount=Decimal("25"))
    assert resolve_relative_dates(details, TODAY) is details


def test_the_new_fields_only_take_the_values_the_data_has():
    DisputeDetails(currency="COP", transaction_type="Withdrawal", category="Food", approximate=True)
    for bad in ({"currency": "EUR"}, {"transaction_type": "Refund"}, {"category": "Gambling"}):
        with pytest.raises(ValidationError):
            DisputeDetails.model_validate(bad)


def test_approximate_is_none_unless_said_so_a_later_message_cannot_undo_it():
    assert DisputeDetails().approximate is None
