"""Real (customer, transaction) pairs that trigger each dispute-policy rule, for eval cases and demo users.

The rules are not re-implemented here: every candidate goes through the backend's own policy
(minsky_api.policy.disputes.decide), so this table follows the policy when it changes. Candidates come
from a deterministic sample of customers; at most PER_RULE rows are kept per rule, picked by a stable
hash so reruns give the same rows.

Not covered, because the bank data cannot produce them: D04 (needs an existing dispute, which lives in
cases.*). "It wasn't me" (D06 by claim) is simulated on eligible transactions with
customer_says_not_me = true.
"""

from datetime import date

PER_RULE = 20
SAMPLE_MODULUS = 50  # ~2 % of customers (~3,000); plenty of candidates for every rule


def model(dbt, session):
    from minsky_api.config import get_settings
    from minsky_api.policy.disputes import DisputeFacts, TxnStatus, decide

    dbt.config(alias="dispute_scenarios")
    as_of = date.fromisoformat(dbt.config.get("as_of"))
    today = get_settings().today
    if today != as_of:
        raise ValueError(f"dbt var as_of_date ({as_of}) differs from the backend's MINSKY_TODAY ({today})")

    transactions = dbt.ref("bank_transactions")
    stats = dbt.ref("bank_customer_complaint_stats")
    rows = (
        transactions.filter(f"hash(customer_id) % {SAMPLE_MODULUS} = 0")
        .join(stats, "customer_id")
        .select(
            "transaction_id, customer_id, transaction_status, transaction_date, amount_usd, "
            "is_fraud, fraud_score, is_repeat_complainer, hash(transaction_id) as pick"
        )
        .order("pick")
        .fetchall()
    )

    kept: dict[str, list[dict]] = {}
    for tx_id, customer_id, status, tx_date, amount_usd, is_fraud, fraud_score, repeat, _ in rows:
        for says_not_me in (False, True):
            facts = DisputeFacts(
                status=TxnStatus(status),
                transaction_date=tx_date.date(),
                amount_usd=float(amount_usd),
                fraud_score=float(fraud_score) if fraud_score is not None else None,
                existing_dispute_ref=None,
                repeat_complainer=bool(repeat),
                customer_says_not_me=says_not_me,
            )
            decision = decide(facts, today)
            # the "not me" variant is only interesting where the claim itself changes the route
            if says_not_me and decision.rule_id != "D06-possible-fraud":
                continue
            bucket = kept.setdefault(f"{decision.rule_id}|{says_not_me}", [])
            if len(bucket) < PER_RULE:
                bucket.append(
                    {
                        "rule_id": decision.rule_id,
                        "route": decision.route.value,
                        "offer_card_block": decision.offer_card_block,
                        "customer_says_not_me": says_not_me,
                        "customer_id": customer_id,
                        "transaction_id": tx_id,
                        "transaction_status": status,
                        "transaction_date": tx_date,
                        "days_before_as_of": (today - tx_date.date()).days,
                        "amount_usd": float(amount_usd),
                        "is_fraud": bool(is_fraud),
                        "fraud_score": float(fraud_score) if fraud_score is not None else None,
                        "is_repeat_complainer": bool(repeat),
                    }
                )

    import pandas as pd

    return pd.DataFrame([row for bucket in kept.values() for row in bucket]).sort_values(
        ["rule_id", "customer_says_not_me", "transaction_id"], ignore_index=True
    )
