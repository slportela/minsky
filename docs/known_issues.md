# Known data issues

Differences between the supplied data (`data/` in the organizer bucket, dataset v1.0.0) and its documentation (`kickoff_docs/LATAM_Bank_Complete_Data_Dictionary.pdf`, `LATAM_Bank_Dataset_Summary.pdf`).

Every number below was measured on the full dataset (2026-09-25/26), with `pipeline/notebooks/validate_vs_dictionary.ipynb` and the silver dbt tests (`make silver`). **Handling** says what the pipeline does about it; nothing is silently dropped or invented.

## Impact on the solution

The issues most likely to affect the customer-service workflow:

| Issue | Why it matters |
|---|---|
| [Columns are generated independently](#columns-are-generated-independently) | No business relationships to learn or explain: history gives aggregate numbers, not drivers; decisions must come from the written policy |
| [Complaints cannot be linked to interactions](#complaints-cannot-be-linked-to-interactions) | No join from a complaint (PQR) to the call that originated it |
| [Survey scales are truncated](#survey-scales-are-truncated) | NPS has no promoters; CSAT never reaches 5. Satisfaction baselines are biased |
| [Customers and agents do not join to branches](#customers-and-agents-do-not-join-to-branches) | No branch context for a customer or an agent |
| [Values in Spanish, dictionary in English](#values-in-spanish-dictionary-in-english) | Filters written against the dictionary return nothing unless silver is used |
| [Mexico operates in USD; no MXN](#mexico-operates-in-usd-no-mxn-in-products-or-transactions) | `amount_usd` is null for every USD transaction: naive USD totals undercount Mexico |
| [`contact_reason` duplicates `reason_category`](#contact_reason-duplicates-reason_category) | Only 6 coarse contact reasons, and the transcripts do not add finer ones |
| [Disputes cannot be traced to a transaction](#disputes-cannot-be-traced-to-a-transaction) | Complaints give aggregate baselines, not case-level ground truth for a dispute workflow |
| [Transcripts are two templates](#transcripts-are-two-templates) | No real conversation text: no source for intents, phrasing or dispute dialogues |
| [`fraud_score` leaks `is_fraud`](#fraud_score-leaks-is_fraud) | A fraud "model" on this score is trivial; it cannot be the learned component |
| [Survey comments are 13 templates](#survey-comments-are-13-templates) | No real feedback text; comment sentiment is a lookup of the template |
| [No duplicate charges](#no-duplicate-charges) | A duplicate-charge scenario has no source data; it must be injected into the mock bank |
| [Delinquency is independent of credit score](#delinquency-is-independent-of-credit-score) | No signal for a credit-risk model |
| [Decline codes are uniformly distributed](#decline-codes-are-uniformly-distributed) | Decline reasons carry no pattern to explain or learn |
| [`accepts_marketing` is not honored](#accepts_marketing-is-not-honored) | Consent does not filter campaign sends or engagement |
| [`avg_csat` does not match measured satisfaction](#avg_csat-does-not-match-measured-satisfaction) | The agent attribute cannot be used for routing or quality claims |
| [`customer_status` has no behavioral footprint](#customer_status-has-no-behavioral-footprint) | No churn model is possible from this data |
| [Spanish only](#spanish-only) | The brief requires Portuguese; there is no Portuguese data to ground or evaluate it |

## Columns are generated independently

The dataset has no business relationships between columns: each one looks drawn on its own, keeping only its distribution. The few relationships that exist are mechanical derivations (`resolution_days` from the dates, `nps_category` from the score, `comment_sentiment` from the template text, the range of `fraud_score` from `is_fraud`). Measured on the 15,363 resolved or closed complaints:

| Relationship a real complaint process would show | In the data |
|---|---|
| Breaching the SLA means taking longer | No: 15.4 days on average when breached, 15.7 when not; both span 1-30 days |
| Critical cases are resolved faster | No: Critical 15.5 days, High 15.6, Medium 15.6, Low 15.7; the SLA is breached in ~20 % of cases at every priority |
| Compensation follows the claimed amount | No: correlation 0.04 |
| Longer resolution lowers satisfaction | No: correlation −0.03 |
| A slow first response predicts an SLA breach | No: correlation 0.002 |
| `resolution_days` matches the dates | Yes, exactly (a derivation) |

The same pattern shows elsewhere: delinquency does not depend on credit score, decline codes are uniform, complaint and transcript texts are templates (sections below).

**Handling:** use history for aggregate, descriptive numbers only (share of disputes, backlog, median resolution time), never as drivers or causes. `sla_breached` is not an outcome metric: it is unrelated to the time taken. Breakdowns such as `bank.resolution_benchmarks` by category and priority add nothing over the overall row. Decisions come from the written policy (`docs/dispute_policy.md`), and the learned component is trained on generated, policy-labeled text, not on structured history.

## Volume

### Row counts differ from the documentation

| Table | Documented | Actual | Difference |
|---|---|---|---|
| transactions | 5,000,000 | 4,425,008 | −11.5 % |
| call_center_interactions | 800,000 | 686,296 | −14.2 % |
| call_transcripts | 200,000 | 171,321 | −14.3 % |
| satisfaction_surveys | 250,000 | 212,759 | −14.9 % |
| complaints | 80,000 | 67,095 | −16.1 % |
| campaign_sends | 2,000,000 | 1,746,801 | −12.7 % |
| digital_events | 10,000,000 | 15,620,994 | +56.2 % |
| daily_exchange_rates | 3,000 | 13,164 | +338.8 % |

Dimension tables (customers, products, branches, service_agents, marketing_campaigns) match exactly. `daily_exchange_rates` = 1,097 days × 12 currency pairs.

**Handling:** none needed; report actual counts, not documented ones.

### `campaign_sends` starts two weeks later

Every daily table covers 2023-06-17 → 2026-06-17 (1,097 days, no gaps) except `campaign_sends`, which starts on 2023-07-01 (1,083 days).

**Handling:** none; account for it in any time series that includes campaigns.

## Documented challenges that are not present

### No duplicates

The summary announces ~2 % duplicate records. Across all tables there are **0** duplicated primary keys, **0** fully identical rows and **0** rows identical except for their ID. `digital_events` was checked on its key columns only (id, dates, customer, session, event type/category, channel, platform, product, is_mobile).

**Handling:** silver still deduplicates by primary key (latest ingestion wins), so duplicates delivered later are handled.

### No schema evolution

The documentation says schemas may change over time. The headers of all 7,671 files match the dictionary exactly, column by column and in order.

**Handling:** silver reads CSVs with `union_by_name`, so an added column would not break the build; the dbt contracts would flag it.

### No late arrivals

No event lands in a partition later than its own date. See [`process_date` cuts at 06:00](#process_date-cuts-at-0600) for the actual date behavior.

## Referential integrity

### Complaints cannot be linked to interactions

`complaints.origin_interaction_id` is **null in all 67,095 rows**, although the dictionary documents it as an FK to `call_center_interactions`.

**Handling:** none possible; complaints can only be related to interactions through `customer_id` and dates.

### Customers and agents do not join to branches

| FK | Non-null values | Orphans |
|---|---|---|
| `customers.registration_branch_id → branches.branch_id` | 150,000 | 149,995 (99.997 %) |
| `service_agents.assigned_branch_id → branches.branch_id` | 833 (367 null) | 831 (99.8 %) |

`registration_branch_id` has 150,000 distinct values for 150,000 customers: it is a random ID per customer, not a branch. The other 22 documented FKs have **0 orphans**.

**Handling:** values are kept; dbt `relationships` tests warn. No branch is inferred.

### Uniqueness violations

| Column (documented UNIQUE) | Duplicated values |
|---|---|
| `products.product_number` | 6 |
| `service_agents.employee_code` | 13 |

**Handling:** rows are kept; dbt `unique` tests warn. Do not use these columns as keys.

## Values

### Values in Spanish, dictionary in English

The dictionary lists English values; several columns hold Spanish ones:

| Column | Spanish values → documented value |
|---|---|
| `products.product_type` | Cuenta Corriente, Cuenta Ahorro, Tarjeta Crédito, Tarjeta Débito, Préstamo Personal, Préstamo Hipotecario, Inversión → Checking Account, Savings Account, … |
| `call_center_interactions.reason_category` | Transaccional, Producto, Técnico, Comercial, Queja → Transactional, Product, … |
| `call_center_interactions.detected_sentiment` | Positivo, Negativo, Muy Negativo → Positive, Negative, Very Negative |
| `branches.geographic_zone` | Urbana → Urban (the only value) |
| `customers.document_type` | Pasaporte → Passport |
| country columns | México → Mexico |

**Handling:** silver maps them to the documented values with `pipeline/transform/seeds/enum_mappings.csv`. Bronze keeps the original bytes.

### Undocumented values

| Column | Value | Rows | Silver value |
|---|---|---|---|
| `products.product_type` | Seguro | 2,049 | Insurance |
| `call_center_interactions.reason_category` | Retención | 20,578 | Retention |
| `call_center_interactions.detected_sentiment` | Muy Positivo | 18,973 | Very Positive |
| `call_center_interactions.channel` | Web | 3,395 | Web (not mapped: ambiguous with "Web Chat") |

**Handling:** kept, translated where unambiguous, and flagged per row as `undocumented_value:<column>` in `_dq_issues`; dbt `accepted_values` tests warn.

### Mexico operates in USD; no MXN in products or transactions

The documentation says transactions carry local currency (MXN/COP/ARS) plus a USD conversion. In the data:

- **No product and no transaction is in MXN.** All 200,398 products of Mexican customers are in USD, and 2,126,409 of the 2,146,309 transactions in Mexico are in USD (the rest are COP/ARS).
- MXN does appear in `complaints.currency` (5,487 claimed amounts) and in `daily_exchange_rates`.
- `transactions.amount_usd` is **null in 100 % of USD transactions** (no conversion needed, but not filled with `amount`) and in ~5 % of ARS/COP transactions. Where present, `amount_usd / amount` matches the day's exchange rate (ARS 0.002857 vs 0.002859; COP 0.000250).

Summing `amount_usd` therefore undercounts Mexico by two orders of magnitude: US$28.9 M instead of US$3,307 M for approved transactions.

**Handling:** none in silver; use `coalesce(amount_usd, case when currency = 'USD' then amount end)` for USD totals, as in `pipeline/notebooks/query_silver.ipynb`.

### `contact_reason` duplicates `reason_category`

`call_center_interactions.contact_reason` ("main contact reason", documented as VARCHAR(100)) has the same 6 values as `reason_category`, in Spanish, with an exact one-to-one correspondence in all 686,296 rows. There is no finer-grained contact reason.

**Handling:** none; use `reason_category`. The transcripts do not provide finer intents either (see [Transcripts are two templates](#transcripts-are-two-templates)).

## Disputes and text

### Disputes cannot be traced to a transaction

Charge disputes live in `complaints`: subcategory `Cargo no reconocido` (category Transactions, 12,297 rows + 1,283 with a null subcategory) and `Cobro indebido` (category Fees, 12,194 + 1,359). Together they are 27,133 cases, 40 % of all complaints. They support aggregate baselines, but not case-level ground truth:

- **No link to the disputed transaction.** There is no transaction id, and `origin_interaction_id` is always null. Matching by product, amount and date: of the 8,143 `Cargo no reconocido` cases with an `affected_product_id`, only 2,107 (26 %) have any transaction on that product in the 30 days before the complaint, **none** with an amount equal to `claimed_amount`, 19 with a reversed transaction and 5 with a fraud-flagged one. In the other direction, 115 of 44,750 reversed transactions are followed by a `Cargo no reconocido` complaint from the same customer within 30 days.
- **Claimed amounts ignore the currency**: roughly uniform between 0 and 5,000 in every currency (median ≈ 2,500 in USD, MXN, COP and ARS alike; 2,500 COP is under US$1). Where granted, compensation is ≈ 10 % of the claim (median ratio 0.09-0.11).
- **Templated text**: 2 distinct `description` values across the 27,133 cases ("Queja relacionada con transactions" / "fees") and 5 distinct `resolution` texts.
- **Outcomes**: 70 % still Open or In Process, ~1,400 Escalated, 6,555 (24 %) Resolved or Closed, 258 (1 %) Rejected. Compensation is recorded for 1,940 of the resolved/closed cases. SLA breached in ~20 % of cases; median 15-16 days to resolution.

**Handling:** none in silver. Use the complaints for aggregate baselines (volume, resolution time, SLA breach, rejection rate). Dispute cases for the system and its evals have to be built from `transactions` plus an explicit (synthetic, labeled) dispute policy.

### Transcripts are two templates

All 171,321 `call_transcripts` are variants of **two** balance-inquiry conversations ("consultar el saldo de mi tarjeta de crédito" / "saber cuál es mi saldo actual en mi cuenta de ahorros"), with 546 distinct texts in total:

- Every transcript contains unfilled template placeholders: `{monto}`, `{moneda}`, `{limite}`.
- `detected_intents` has a single value, `consulta_general`.
- No transcript mentions a charge, a fee, a complaint or fraud (keyword search), including the 29,198 whose interaction reason is Complaint. `main_topics` equals `contact_reason` on **100 % of rows** (171,321 checked), so it is a leakage column for any intent model.

**Handling:** none. The transcripts cannot ground intents, customer phrasing, simulator personas or dispute dialogues; those must be generated and labeled as such.

### `fraud_score` leaks `is_fraud`

In `transactions`, `fraud_score` is between 0 and 30 for every non-fraud transaction (max 30.00), and spreads over 0-100 for the 4,316 fraud ones (median 48.9). Any score above 30 is fraud with 100 % precision (3,264 of 4,316 fraud transactions, 75.6 % recall); 1,052 fraud transactions score ≤ 30.

**Handling:** none. Do not present a fraud classifier trained on this score as a learned component; report the leakage if fraud is used at all.

### Survey comments are 13 templates

`satisfaction_surveys.open_comments` is filled in 101,196 of 212,759 surveys with only **13 distinct texts** (e.g. "Tardaron mucho en atenderme.", "Aceptable.", "Muy satisfecho con el servicio."). The question texts are fixed as well (3, 1 and 1 distinct values for questions 1, 2 and 3). `comment_sentiment` is determined by the template: each text always has the same label (or null, ~5 %), and it follows the score (average `main_score` 2.6 for negative texts, 4.0 for positive ones). No NPS survey has a positive comment.

**Handling:** none. The comments support counts, not NLP: a sentiment model would reach 100 % by looking up the text.

## Transactions and credit

### No duplicate charges

There are **no duplicated charges** in `transactions`: 0 pairs with the same customer, product, amount, transaction type and merchant within one day. The query is not the reason: the same customer and product within one day gives 53,982 pairs. Pairs with the same customer, product and amount (127 in three years) are at least 5 days apart (median 332 days), i.e. coincidences of amount.

**Handling:** none. A duplicate-charge scenario must be injected into the mock bank (e.g. a real transaction copied with a new id seconds later) and labeled as such in the eval cases.

### Delinquency is independent of credit score

`products.days_past_due` is filled only for credit products (Credit Card, Personal Loan, Mortgage), and about 15 % of them are past due whatever the customer: 15.4 % in the lowest credit-score quintile (average score 555) and 14.9 % in the highest (769); 14.7-15.2 % across segments.

**Handling:** none. There is no signal for a credit-risk model; a credit workflow would need its risk estimate to be synthetic and labeled as such.

### Decline codes are uniformly distributed

Declined transactions (221,234) carry one of four `response_code` values in almost equal shares: 51 (23.9 %), 14 (23.8 %), 54 (23.7 %), 05 (23.6 %), plus 5 % null. The decline rate is the same for local and foreign transactions (5.0 % vs 4.9 %).

**Handling:** none. A decline can be explained by looking up its code, but the codes carry no pattern to learn from.

### Inconsistent country spelling

`transactions.transaction_country` mixes `Mexico` and `México` in the same column; `customers`, `branches` and `campaign_sends` use `México`; `service_agents` and `marketing_campaigns` use `Mexico`. Transactions also occur in countries outside the three operating ones (USA 40,621, Spain 40,542, Brazil 40,472), which is plausible for card use abroad.

**Handling:** silver normalizes every country column to `Mexico`.

### Survey scales are truncated

| Survey type | Documented scale | Observed values |
|---|---|---|
| CSAT | 1–5 | 1–4 |
| CES | not documented | 1–4 |
| NPS | 0–10 | 2–7 |

With NPS scores between 2 and 7 there are **no promoters** (9–10): every categorized answer is Detractor (0–6) or Passive (7–8), consistently with the score. `nps_category` is null in 3,274 of 63,668 NPS answers (5.1 %).

**Handling:** none; any NPS or CSAT computed from this data is structurally biased and must be reported as such.

### NOT NULL violation

`call_transcripts.duration_seconds` is documented NOT NULL but is null in 24,029 rows (14.0 %).

**Handling:** rows kept, flagged as `not_null:duration_seconds`; the dbt `not_null` test warns.

## Campaigns and agents

Found in the learnability sweep (`docs/data_findings.md`, `pipeline/notebooks/explore_learnability.ipynb`).

### `accepts_marketing` is not honored

Customers with `accepts_marketing = false` (874,417 of 1,746,801 sends) receive campaigns at the same rate as those with it true, and engage identically: 38.6 % vs 38.6 % opens, 0.57 % vs 0.55 % conversions.

**Handling:** none in silver. Consent is not modeled as a filter; if campaign tooling is ever exposed, the consent flag must be checked in code, not inferred from the data.

### `avg_csat` does not match measured satisfaction

`service_agents.avg_csat` correlates r = −0.03 with the satisfaction actually observed in the agent's own surveys (1,090 agents with ≥ 30 surveys; `docs/data_findings.md` H31). The declared attribute is unrelated to measured performance.

**Handling:** none. Do not use `avg_csat` for routing, scoring or quality claims; use measured survey outcomes instead.

### `customer_status` has no behavioral footprint

Active (127,700), Inactive (14,914), Suspended (4,407) and Closed (2,979) customers are indistinguishable on transaction volume (32.6–32.9), complaint count (0.41–0.45), products held (2.82–2.87), delinquency (11.2–11.8 %) and transaction recency (43–45 days) — and equally on digital engagement (79.1–79.4 `digital_events` per customer, 9.8 sessions).

**Handling:** none. No churn or attrition model is possible from these tables; do not claim one.

### UTM parameters are decorative

In `digital_events`, the 839,045 rows carrying `utm_source`/`utm_medium` (of 15,620,994) have **0.000 %** `Purchase` and **0.000 %** `Error` events — conversions and errors only occur on rows without UTM tags. Campaign attribution through the clickstream is impossible.

**Handling:** none in silver (`digital_events` is not modeled). If UTM-based attribution is ever needed, treat the fields as decorative and say so.

### Campaign event columns are post-hoc

In `campaign_sends`, `open_device` and `open_country` are filled only when `was_opened` (438,377 of 487,309 opens have a device; never on non-opens), `click_date` only when `was_clicked`, and `conversion_value` only when `had_conversion`. WhatsApp and Voice sends (403,673 rows) have no open tracking at all (`was_opened` null).

**Handling:** none. These columns are leakage for any funnel model; use only `send_*` columns known at send time as features.

## Dates

### `process_date` cuts at 06:00

`process_date` (the partition key) is not the calendar date of the event: events from 00:00 to about 06:00 are assigned to the previous day's partition. For example, 1,106,307 transactions (25 %) have a `transaction_date` one day after their `process_date`; all but 51 of them are between 00:00 and 05:59 (the 51 are at 06:xx). Satisfaction surveys arrive up to 2 days after their partition date.

**Handling:** none; use the event timestamp (`transaction_date`, `interaction_date`, …) for time analysis and `process_date` only for incremental loading.

## Format

| Issue | Handling |
|---|---|
| CSV headers start with a UTF-8 BOM (first column read as `﻿product_id`) | Read with `utf-8-sig`; DuckDB handles it |
| Integer columns written as floats (`"180.0"`, `"2.0"`) | Silver casts through double and rejects real fractions (`cast_failed:<column>`) |
| Booleans as `True`/`False` | Cast to boolean |
| Exchange rates have 6 decimals, so COP→USD (~0.000250) keeps only 3 significant digits: up to ~0.2 % conversion error | None; it is the source precision |

## Bucket contents

- `data_backup_20260831/` (4,833 files, 4.4 GB) is a partial copy of `data/`; ignored.
- `marketing_campaigns.csv` also exists at the bucket root, outside `data/`; ignored.

## Language coverage

### Spanish only

All text (transcripts, complaints, survey comments) is Spanish (`call_transcripts.detected_language` is always `es`). The brief requires Spanish **and Portuguese** interactions; there is no Portuguese data to ground answers or to evaluate on.

**Handling:** the bank text stays Spanish. The reply language is chosen once from the first customer message by lingua, restricted to Spanish and Portuguese; if lingua cannot decide, the reply stays Spanish. Generated dev drafts (`dispute-eligible-open-pt`, `dispute-above-limit-pt`, `dispute-eligible-open-mixed`) reuse the Spanish policy fixtures; the wording does not change the label. Portuguese replies are phrase-catalog translations. There is still no Portuguese source text, and these drafts are not a held-out result.
