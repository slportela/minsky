# 0015. Flexible transaction matching: near amounts, days and merchants are proposed, never selected

- Status: proposed
- Date: 2026-10-05

## Context
- The search matched exactly: the amount in the transaction's own currency, the calendar range as given, the merchant as a case-insensitive substring. A customer who says "123 USD" for a charge of 123.10, or "yesterday" for a charge posted just after midnight, heard "I did not find a charge that matches", with nothing about what was close.
- Customers remember amounts and days roughly, name merchants with typos, and think in dollars while the charge is in COP or ARS. A system that is scored on a working dispute flow cannot answer all of that with "not found".
- What the data says (`bank.transactions`, approved, 2026-02-18 to 2026-06-18, 454,286 rows; DuckDB over the gold model on 2026-10-05):
  - A median customer has 3 such transactions (p90 7, p99 12) and at most 150 in the whole history; 98.2 % of customer-days have one transaction. Candidate sets are tiny, so a wide net is cheap here. That says nothing about a production customer with thousands of rows.
  - The share of transactions that have another one of the same customer within a relative amount tolerance: 2.2 % at 1 %, 10.1 % at 5 %, 18.4 % at 10 %, 31.7 % at 20 %. Past about 10 % the risk of proposing the wrong charge is real.
  - Only 23.1 % of transactions have a merchant: 95 % of purchases and none of the withdrawals, transfers, payments, deposits or adjustments (24 distinct names). A merchant cannot be the way to find most charges; kind, category and channel can narrow them.
  - The currencies are USD, COP and ARS; the previous search compared the customer's number with the own-currency amount only.

## Decision
- The matching is code, not the model (AGENTS rule 1). The model only extracts what the customer said; `matching/` grades each of the customer's own charges against it.
- Each described thing is graded `exact`, `near` or `miss`: amount, day, merchant, kind, category. The result is the first tier that has a charge, never a mix: **exact** (everything matches exactly), **near** (nothing misses, something is close), **closest** (exactly one thing misses and a strong one holds), **none**. An exact charge is never listed next to a near one.
- Rules (thresholds in `SearchConfig`; changing one needs an eval delta):
  - F1 near amount: within max(1 % , 0.10); within max(10 % , 1.00) when the customer says "about" (`approximate`).
  - F2 currency: with a currency stated, USD is compared with `amount_usd` and a local currency with `amount`; with none stated either may be meant; a currency the charge does not carry cannot match.
  - F3 day: the calendar range as said, plus or minus 1 day as near. Relative days ("yesterday") are extracted as `days_ago` and resolved by code with the system's today, because the extraction prompt is static (prompt caching) and the model does not know the date.
  - F4 combined: all described things are graded together; near or closest charges come with how each one differs.
  - F5 merchant: folded for case, accents and punctuation, in any word order (what the substring match always accepted); a misspelling is near by text similarity (0.8), and a name under 4 characters must match exactly.
  - F6 kind and category narrow, they do not find: without an amount, a day or a merchant there is nothing to propose, so "a purchase" never lists the whole history.
- A near or closest charge is a **proposal, never a selection**: the reply says what was not found, what exists and how it differs, and the customer must say yes (one charge) or pick a number (several). The policy then decides on the charge's real facts, and the handoff records `match_tier`. The sentence is written by code (`agent/wording.py`, es and pt), not the model: every figure in it is the customer's own words or a verified row.
- Status is not a filter: a declined or reversed charge must be found so that rules D01 and D02 can answer.
- New tool `find_transactions` (session-scoped, audited with the tier, reads one customer's history from `TransactionStore.search_pool`, which fails instead of truncating if a history ever exceeds 300 rows). `get_transactions` stays as the exact filter. The extraction prompt is v3 with `currency`, `approximate`, `days_ago`, `transaction_type` and `category`.

## Consequences
- The customer hears what was close instead of "not found", and the system does not widen what it acts on: confirmation and the policy are unchanged.
- A correction made while a proposal is pending ("no, it was 80") is not re-extracted: in `confirm_txn` only a plain yes or no is read, as before for exact proposals. A plain "no" goes back to asking for details.
- "Pesos" alone is not a currency (MXN, COP and ARS), so it is left unknown and either amount may match. "Last Monday" and other relative expressions beyond today, yesterday, the day before and "N days ago" are not resolved yet.
- Evidence so far is offline: 7 new dev cases with scripted extraction (dev smoke 24/24, Wilson 95 % CI 86.2-100 %), about 60 new unit tests of the matcher, the extraction, the wording, the tool, the store and the flow, and a control run where 6 of the 7 cases fail with the matcher forced back to exact only (the seventh, a day with two charges, is what exact matching already did once the day is known). The 1 % and 10 % thresholds come from the collision shares above, not from customer behavior; no live model has been run on the new prompt or fields. Wrong if live extraction fills the new fields badly (type or currency invented), or if customers who confirm a near proposal are later found to have meant another charge.
- The thresholds, the 0.8 similarity and the tiers are ours. They would be tuned with real conversations, on `val`, never on the locked test split.
