"""Bind diagnostic dev scripts to verified gold rows without changing the bank or locked splits."""

import json

from sqlalchemy import text

from evals.schema import Case
from evals.world import check_label, facts_from_case
from minsky_api.store.db import dispose_engine, session

_RULES = {
    "dispute-eligible-open-es": "D09-eligible",
    "dispute-above-limit-es": "D07-above-auto-limit",
    "dispute-declined-not-charged-es": "D01-declined-not-charged",
    "dispute-fraud-block-es": "D06-possible-fraud",
    "dispute-fraud-no-block-es": "D06-possible-fraud",
}

_QUERY = text("""
    select t.*, coalesce(c.is_repeat_complainer, false) as is_repeat_complainer
    from bank.dispute_scenarios s
    join bank.transactions t on t.transaction_id = s.transaction_id
    join bank.products p on p.product_id = t.product_id and p.customer_id = t.customer_id
    join bank.customer_complaint_stats c on c.customer_id = t.customer_id
    where s.rule_id = :rule and (:needs_card = false or (p.is_card and p.product_status = 'Active'))
    order by t.transaction_id
    limit 20
""")


async def bind_gold_cases(cases: list[Case]) -> list[Case]:
    """Read only bank.*; fail if the required scenario cannot be verified against current policy."""
    bound: list[Case] = []
    try:
        async with session() as db:
            for case in cases:
                if case.user_scenario.known_info.get("label_source") == "authentication":
                    bound.append(case)
                    continue
                rule = _RULES.get(case.id)
                if rule is None:
                    # Fixture-specific fault/filter cases stay in the isolated L2 workload.
                    if case.id != "dispute-other-customer-txn-es":
                        continue
                    rows = (await db.execute(_QUERY, {"rule": "D09-eligible", "needs_card": False})).mappings().all()
                    if not rows:
                        raise ValueError("gold lacks an eligible card scenario")
                    own = rows[0]
                    other = next((row for row in rows if row["customer_id"] != own["customer_id"]), None)
                    if other is None:
                        raise ValueError("gold lacks a distinct customer for the ownership control")
                    data = case.model_dump(mode="json")
                    data["session"]["customer_id"] = own["customer_id"]
                    data["user_scenario"]["known_info"] = {
                        "label_source": "tool_denial",
                        "other_customer_id": other["customer_id"],
                        "other_transaction_id": other["transaction_id"],
                    }
                    data["user_scenario"]["script"] = [f"Quiero disputar la transacción {other['transaction_id']}."]
                    bound.append(Case.model_validate(data))
                    continue
                rows = (
                    (await db.execute(_QUERY, {"rule": rule, "needs_card": rule == "D06-possible-fraud"}))
                    .mappings()
                    .all()
                )
                if not rows:
                    raise ValueError(f"gold lacks an active-card scenario for {rule}")
                row = rows[0]
                txn, product = row["transaction_id"], row["product_id"]
                old = case.user_scenario.known_info
                # Replace references in assertions and scripts before replacing hidden facts.
                raw = case.model_dump_json()
                raw = raw.replace(old["transaction_id"], txn).replace(old["product_id"], product)
                data = json.loads(raw)
                data["session"]["customer_id"] = row["customer_id"]
                not_me = rule == "D06-possible-fraud"
                data["user_scenario"]["known_info"] = {
                    "rule_id": rule,
                    "transaction_id": txn,
                    "product_id": product,
                    "merchant": row["merchant_name"] or "",
                    "amount": str(row["amount"]),
                    "amount_usd": str(row["amount_usd"]),
                    "currency": row["currency"],
                    "transaction_status": row["transaction_status"],
                    "transaction_date": row["transaction_date"].isoformat(),
                    "is_fraud": str(row["is_fraud"]).lower(),
                    "is_repeat_complainer": str(row["is_repeat_complainer"]).lower(),
                    "customer_says_not_me": str(not_me).lower(),
                }
                first = (
                    f"No reconozco la transacción {txn}."
                    if not_me
                    else f"Quiero disputar el monto incorrecto de la transacción {txn}."
                )
                script = [first, "sí"]
                if rule == "D09-eligible":
                    script.append("sí")
                elif not_me:
                    script.append("no" if case.id.endswith("no-block-es") else "sí")
                data["user_scenario"]["script"] = script
                data["notes"] = (
                    "Runtime gold binding; policy-derived label, generated customer wording; "
                    "case writes remain in memory."
                )
                result = Case.model_validate(data)
                check_label(result, facts_from_case(result))
                bound.append(result)
    finally:
        await dispose_engine()
    return bound
