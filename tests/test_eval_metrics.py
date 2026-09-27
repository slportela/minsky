import math

import pytest

from evals.metrics import (
    Rate,
    cost_per,
    mean_over_tasks,
    pass_at_k,
    pass_hat_k,
    percentile,
    wilson_interval,
    zero_event_upper_bound,
)


def test_pass_hat_k_matches_power_when_estimating_from_a_known_rate():
    # 3 of 4 trials pass: P(2 random trials both pass) = C(3,2)/C(4,2) = 0.5
    assert pass_hat_k(4, 3, 2) == pytest.approx(0.5)
    assert pass_hat_k(4, 4, 4) == 1.0
    assert pass_hat_k(4, 3, 4) == 0.0


def test_pass_at_k_and_pass_hat_k_agree_at_k1():
    assert pass_at_k(5, 2, 1) == pytest.approx(pass_hat_k(5, 2, 1)) == pytest.approx(0.4)
    assert pass_at_k(5, 4, 2) == 1.0  # only one failure, any 2 trials include a success


def test_mean_over_tasks():
    assert mean_over_tasks([(3, 3), (3, 0)], k=3) == pytest.approx(0.5)
    assert mean_over_tasks([], k=1) is None


@pytest.mark.parametrize("n,c,k", [(3, 4, 1), (3, 1, 0), (3, 1, 4)])
def test_invalid_trial_counts(n, c, k):
    with pytest.raises(ValueError):
        pass_hat_k(n, c, k)


def test_wilson_interval_contains_estimate_and_handles_empty():
    interval = wilson_interval(8, 10)
    assert interval is not None
    lo, hi = interval
    assert lo < 0.8 < hi and 0 <= lo and hi <= 1
    assert wilson_interval(0, 0) is None


def test_zero_event_upper_bound_is_close_to_rule_of_three():
    assert zero_event_upper_bound(100) == pytest.approx(3 / 100, rel=0.05)
    assert zero_event_upper_bound(0) is None


def test_rate_reports_not_defined_and_upper_bound():
    assert Rate(0, 0).value is None
    assert str(Rate(0, 0)).startswith("not defined")
    assert "upper bound" in str(Rate(0, 50))


def test_percentile_and_cost():
    assert percentile([5, 1, 3, 2, 4], 50) == 3
    assert percentile([1, 2, 3, 4, 100], 95) == 100
    assert percentile([], 50) is None
    assert cost_per(1.5, 0) is None
    cost = cost_per(1.5, 3)
    assert cost is not None and math.isclose(cost, 0.5)
