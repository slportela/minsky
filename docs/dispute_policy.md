# Dispute policy (synthetic)

> **Synthetic policy, written for the hackathon.** It does not reproduce the rules of any real bank. Code: `backend/src/minsky_api/policy/disputes.py`; tests: `backend/tests/test_policy_disputes.py`. Rule ids appear in replies, handoffs and traces.

Rules are applied in order to **verified facts** about one transaction of the authenticated customer. The first rule that matches decides.

| Rule | When | Route | What the customer hears |
|---|---|---|---|
| D01-declined-not-charged | The transaction was declined | inform | It was never charged; nothing to dispute |
| D02-already-reversed | It was reversed | inform | The money was already returned |
| D03-pending-not-posted | It is still pending | abstain | It can be disputed once it is posted |
| D04-already-disputed | A dispute already exists for it | inform | The existing case reference and its status |
| D05-outside-window | More than **120 days** old | refuse | Outside the dispute window; offer a human |
| D06-possible-fraud | The customer says it wasn't them, or fraud score ≥ **80** | escalate (fraud) | Offer to block the card (only with an explicit yes), then hand off to the fraud team |
| D07-above-auto-limit | Amount > **USD 500** | escalate (agent) | A dispute agent will take the case; what happens next |
| D08-repeat-complainer | Another complaint in the last 90 days | escalate (agent) | Same as D07 |
| D09-eligible | None of the above | open dispute | Summary, explicit confirmation, then the case reference and the expected resolution time |

The thresholds (120 days, score 80, USD 500) live in `PolicyConfig`; changing one needs an eval delta.

What the system never does:
- move money or grant refunds;
- approve a dispute outcome;
- block a card without an explicit confirmation;
- act on another customer's records.
