# Today's process vs Minsky (val split, offline)

Run 2026-10-04T20:38:32+00:00 · commit 79492b2 · 24 cases × 1 trial · scripted extraction (offline: decisions, handoffs and safety, not language understanding). Intervals are Wilson 95 %.

| Metric | Always send to an agent (today) | Minsky |
|---|---|---|
| Handled without an agent | 0/24 = 0% [0–14] | 12/24 = 50% [31–69] |
| Correct outcome | 10/24 = 42% [24–61] | 24/24 = 100% [86–100] |
| Fully correct (outcome, records, reference, handoff content, safety) | 0/24 = 0% [0–14] | 24/24 = 100% [86–100] |
| Unnecessary handoffs (case did not need a person) | 12/12 = 100% [76–100] | 0/12 = 0% [0–24] |
| Missed handoffs (case needed a person) | 0/12 = 0% [0–24] | 0/12 = 0% [0–24] |
| Unsafe outcomes | 0/24 = 0% [0–14] | 0/24 = 0% [0–14] |
| Agent intake minutes (handoffs × 7.2 min) | 172.8 | 86.4 |
| Trials that errored (excluded above) | 0 | 0 |

By language (fully correct): es 13/13 = 100% [77–100], pt 11/11 = 100% [74–100].
No unsafe outcome was observed for Minsky; with n=24 the true rate could still be up to 12% (one-sided 95 %).

How to read it:
- The baseline is correct on every case that needs a person and wrong on every case that does not: that gap is the intake work Minsky removes.
- Agent minutes count intake only; investigating an opened dispute is back-office work in both systems.
- Agent minutes use the measured median complaint call (7.2 min); the case mix is the eval workload (one case per scenario), not the bank's real mix of disputes.
- Offline and scripted: a live run with the production model is needed before quoting these as system performance (docs/evals.md).
- val is the selection split: failures found on it are fixed, so these are not held-out test numbers. The locked test split is still empty and must be written by people (AGENTS rule 4).
- The labels come from the same policy code (policy.disputes.decide) that Minsky runs, and the outcome is read partly from the system's own state. Offline, with scripted understanding, this mostly checks that the system wires the policy, tools, handoffs and safety checks correctly on real records; a policy bug would not show here. Hand-labeled cases and a live run are what test the policy and the model.
