from dataclasses import replace
from datetime import date, timedelta

import pytest

from minsky_api.policy.disputes import DisputeFacts, PolicyConfig, Route, TxnStatus, decide

TODAY = date(2026, 6, 17)  # the dataset's last day: the system's configured "today"
ELIGIBLE = DisputeFacts(status=TxnStatus.APPROVED, transaction_date=TODAY - timedelta(days=5), amount_usd=40.0,
                        fraud_score=10.0, existing_dispute_ref=None, repeat_complainer=False,
                        customer_says_not_me=False)


@pytest.mark.parametrize("change,route,rule", [
    ({}, Route.OPEN_DISPUTE, "D09-eligible"),
    ({"status": TxnStatus.DECLINED}, Route.INFORM, "D01-declined-not-charged"),
    ({"status": TxnStatus.REVERSED}, Route.INFORM, "D02-already-reversed"),
    ({"status": TxnStatus.PENDING}, Route.ABSTAIN, "D03-pending-not-posted"),
    ({"existing_dispute_ref": "DSP-1"}, Route.INFORM, "D04-already-disputed"),
    ({"transaction_date": TODAY - timedelta(days=121)}, Route.REFUSE, "D05-outside-window"),
    ({"customer_says_not_me": True}, Route.ESCALATE_FRAUD, "D06-possible-fraud"),
    ({"fraud_score": 80.0}, Route.ESCALATE_FRAUD, "D06-possible-fraud"),
    ({"amount_usd": 500.01}, Route.ESCALATE_AGENT, "D07-above-auto-limit"),
    ({"repeat_complainer": True}, Route.ESCALATE_AGENT, "D08-repeat-complainer"),
])
def test_each_rule(change, route, rule):
    decision = decide(replace(ELIGIBLE, **change), TODAY)
    assert (decision.route, decision.rule_id) == (route, rule)


def test_boundaries_are_inclusive_for_the_customer():
    assert decide(replace(ELIGIBLE, transaction_date=TODAY - timedelta(days=120)), TODAY).route == Route.OPEN_DISPUTE
    assert decide(replace(ELIGIBLE, amount_usd=500.0), TODAY).route == Route.OPEN_DISPUTE
    assert decide(replace(ELIGIBLE, fraud_score=None), TODAY).route == Route.OPEN_DISPUTE


def test_only_fraud_offers_a_card_block():
    assert decide(replace(ELIGIBLE, customer_says_not_me=True), TODAY).offer_card_block
    assert not decide(replace(ELIGIBLE, amount_usd=9000.0), TODAY).offer_card_block


def test_rule_order_status_before_fraud():
    # A declined charge was never taken: nothing to dispute, even if the customer says "not me".
    facts = replace(ELIGIBLE, status=TxnStatus.DECLINED, customer_says_not_me=True)
    assert decide(facts, TODAY).rule_id == "D01-declined-not-charged"


def test_thresholds_come_from_config():
    strict = PolicyConfig(auto_limit_usd=10.0)
    assert decide(ELIGIBLE, TODAY, strict).route == Route.ESCALATE_AGENT
