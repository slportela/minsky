# PR #51 confirmation-limit correction and directed live validation

## Changes

- All non-consenting answers in the card-confirmation episode share one bounded counter, even when
  the answer is affirmative with extra words, search resumes, or the model asks a further question in text.
  An explicit card answer resets the episode. A search answer never grants consent.
- Added `dispute-mixed-unclear-confirmation-es` from the preceding A/B failure. Mirrors are the existing
  natural-reply-then-yes and eligible-open cases. The new unit tests failed before the fix and pass afterward.
- The Starbucks fixture now explicitly answers an expected-amount question: the customer does not remember
  and wants to dispute the identified Starbucks charge. No expected amount is invented. Both flows receive
  the updated customer. This changes the fixture, so the old full-suite rate is not relabeled.
- The reactive customer continues its scripted ambiguous answers during SEARCH. Previously it stopped if
  the model asked a text question after a card detour. No production prompt changed.
- ADR 0017 accepts the opt-in experiment only. Workflow remains default.

## Validation and chronology

`make ci` passed on 2cc12aa: 950 tests passed, one empty-parameter diagnostic skipped, both offline
smokes 30/30 (Wilson 95% CI 88.6–100%), frontend types and seven frontend API tests passed.
The skipped test was the former group of flexible cases the workflow could not resolve; that group is now
empty because flexible matching and the explicit Starbucks follow-up make every listed fixture passable.

A first directed live run on 64c171a (`pr51-fix-agentic-64c171a`, 5 cases × 3 trials) passed 10/15.
Reading all five failed transcripts showed the ambiguous-answer simulator stopped when the agent asked a
text question during SEARCH: three failures in the new mixed case and two in the existing unclear case.
That run is retained, not hidden. Both the continued scripted replies and the limit across search questions
were then covered by deterministic tests and corrected in 2cc12aa.

Final matched directed live run: clean 2cc12aa, same five dev cases × three trials, gpt-6-luna, two workers,
SQLite fixture worlds, OpenAI directly as authorized, no prompt changes between modes.

| Metric | Workflow | Agentic |
|---|---|---|
| Trials passed | 15/15, 100% (Wilson 95% CI 79.6–100%) | 15/15, 100% (79.6–100%) |
| Cases passing all trials | 5/5 | 5/5 |
| Infrastructure errors | 0/15 | 0/15 |
| Usage-priced spend | USD 0.0085513 | USD 0.0125655 |
| Conservative reservation | USD 0.1143291 | USD 0.1358388 |

Scenarios: original unclear replies, mixed unclear replies, misspelled Starbucks with an explicit follow-up,
natural reply followed by explicit yes, and eligible opening. The first two escalate without opening a dispute;
the last three resolve under D09. Consent remains explicit and partial safety checks pass.

Evidence: `evals/runs/pr51-followup-workflow`, `evals/runs/pr51-followup-agentic` (git-ignored local raw trials),
and committed `2026-10-05-pr51-followup-ab.json`. Prices are recorded usage-rate estimates, not an invoice.
The batch ledger `2026-10-05-pr51-updated-ab-budget.json` includes all prior attempts and follow-ups:
usage USD 0.1452168, cumulative reservations USD 1.7037017, below the separately approved USD 7.50.
Unused per-run ceiling sums are not consumption; runs were sequential and their reservations retained.

## Interpretation

This directed run verifies the observed confirmation regression and explicit follow-up fixture; it does
not replace the earlier full 37-case A/B (workflow 74/74, agentic 71/74) with a new full-suite pass rate.
The fixtures and reactive customer changed, so a claimed full before/after improvement would be invalid.
No val or locked test cases were used. The sample is small and correlated, safety grading is partial,
and the simulator is not free-play. The agentic option remains experimental and workflow stays default.
The unrelated budget-exhaustion classification issue in the runner is documented but not fixed here.
