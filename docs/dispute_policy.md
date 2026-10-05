# Dispute policy (synthetic)

> **Synthetic policy, written for the hackathon.** It does not reproduce the rules of any real bank. Code: `backend/src/minsky_api/policy/disputes.py`; tests: `backend/tests/test_policy_disputes.py`. Rule ids appear in replies, handoffs and traces.

Rules are applied in the order of this table to **verified facts** about one transaction of the authenticated customer. The first rule that matches decides. Rule ids are stable identifiers (cited in replies, traces and eval cases), so a rule keeps its id when the order changes: D06 is checked before D05.

| Rule | When | Route | What the customer hears |
|---|---|---|---|
| D01-declined-not-charged | The transaction was declined | inform | It was never charged; nothing to dispute |
| D02-already-reversed | It was reversed | inform | The money was already returned |
| D03-pending-not-posted | It is still pending | abstain | It can be disputed once it is posted |
| D04-already-disputed | A dispute already exists for it | inform | The existing case reference and its status |
| D06-possible-fraud | The customer says it wasn't them, or the bank flagged the transaction as fraud (`is_fraud`), **whatever its age** | escalate (fraud) | Offer to block the card (only with an explicit yes), then hand off to the fraud team |
| D05-outside-window | More than **120 days** old | refuse | Outside the dispute window; offer a human |
| D07-above-auto-limit | Amount > **USD 500** | escalate (agent) | A dispute agent will take the case; what happens next |
| D08-repeat-complainer | Another complaint in the last 90 days | escalate (agent) | Same as D07 |
| D09-eligible | None of the above | open dispute | Summary, explicit confirmation, then the case reference and the expected resolution time |

The thresholds (120 days, USD 500) live in `PolicyConfig`; changing one needs an eval delta. "Today" is fixed at 2026-06-18, the day the data ends (`MINSKY_TODAY`).

Why these rules, from the data (2026-09-30):
- **Fraud uses the bank's flag, not `fraud_score`.** The score leaks the flag (every score above 30 is fraud; `docs/known_issues.md`), and a threshold of 80 missed 1,700 flagged transactions scoring 30-80.
- **Fraud before the window.** A card that may be compromised needs the block offer whatever the charge's age; with the window first, a "not me" on any charge older than 120 days was refused without it.
- **USD 500 keeps card disputes automatic.** No approved purchase or withdrawal in the last 120 days is above USD 500 (medians about USD 250), so D07 only sends transfers, payments, deposits and adjustments to an agent (46.5 % of approved transactions in the window).

Open question: deposits (money in) are not charges, but the rules let a customer dispute one (about 9 % of approved deposits in the window are at or below USD 500 and would be opened automatically). A rule that informs "a deposit cannot be disputed" is a policy decision still to take.

What the system never does:
- move money or grant refunds;
- approve a dispute outcome;
- block a card without an explicit confirmation;
- act on another customer's records.

## Card block offer

D06 offers a card block only when the charge is on one of the customer's own cards (`evaluate_dispute` reads the product). A transfer or an account payment has no card to block, so the case goes straight to the fraud team without asking a question that cannot be acted on.

## Triage (synthetic)

> **Synthetic targets, written for the hackathon.** The data has no SLA deadline to derive them from: `sla_breached` is unrelated to resolution time, and critical and low-priority disputes take the same ~15.6 days today (`docs/known_issues.md`). Code: `backend/src/minsky_api/policy/triage.py`; tests: `backend/tests/test_policy_triage.py`.

Every dispute the system opens and every handoff it creates becomes a back-office case. The first matching row decides its priority, queue and due time:

| When | Priority | Queue | Due within |
|---|---|---|---|
| Possible fraud (D06), or the card was blocked | Critical | fraud | 4 hours |
| Amount above USD 500 | High | disputes | 2 days |
| Handoff for a repeat complainer (D08) or another policy rule | Medium | disputes | 5 days |
| Handoff without a rule (out of scope, clarification, unanswered question or turn limit; only after the customer confirmed the charge or accepted an offered agent) | Medium | general | 5 days |
| Dispute opened automatically (D09) | Low | disputes | 10 days |

The console orders open cases by priority, then due time. Each case also shows the bank's historical median resolution time for that priority (`bank.resolution_benchmarks`), so the agent and the customer get a realistic expectation. Changing a target needs an eval delta, like the policy thresholds.
