# Known data issues

Differences between the supplied data (`data/` in the organizer bucket, dataset v1.0.0) and its documentation (`kickoff_docs/LATAM_Bank_Complete_Data_Dictionary.pdf`, `LATAM_Bank_Dataset_Summary.pdf`).

Every number below was measured on the full dataset (2026-09-25/26), with `notebooks/validate_vs_dictionary.ipynb` and the silver dbt tests (`make silver`). **Handling** says what the pipeline does about it; nothing is silently dropped or invented.

## Impact on the solution

The issues most likely to affect the customer-service workflow:

| Issue | Why it matters |
|---|---|
| [Complaints cannot be linked to interactions](#complaints-cannot-be-linked-to-interactions) | No join from a complaint (PQR) to the call that originated it |
| [Survey scales are truncated](#survey-scales-are-truncated) | NPS has no promoters; CSAT never reaches 5. Satisfaction baselines are biased |
| [Customers and agents do not join to branches](#customers-and-agents-do-not-join-to-branches) | No branch context for a customer or an agent |
| [Values in Spanish, dictionary in English](#values-in-spanish-dictionary-in-english) | Filters written against the dictionary return nothing unless silver is used |
| [Mexico operates in USD; no MXN](#mexico-operates-in-usd-no-mxn-in-products-or-transactions) | `amount_usd` is null for every USD transaction: naive USD totals undercount Mexico |
| [`contact_reason` duplicates `reason_category`](#contact_reason-duplicates-reason_category) | Only 6 coarse contact reasons; finer intents must come from transcripts |
| [Spanish only](#spanish-only) | The brief requires Portuguese; there is no Portuguese data to ground or evaluate it |

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

**Handling:** silver maps them to the documented values with `transform/seeds/enum_mappings.csv`. Bronze keeps the original bytes.

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

**Handling:** none in silver; use `coalesce(amount_usd, case when currency = 'USD' then amount end)` for USD totals, as in `notebooks/query_silver.ipynb`.

### `contact_reason` duplicates `reason_category`

`call_center_interactions.contact_reason` ("main contact reason", documented as VARCHAR(100)) has the same 6 values as `reason_category`, in Spanish, with an exact one-to-one correspondence in all 686,296 rows. There is no finer-grained contact reason.

**Handling:** none; use `reason_category`. Finer intents must come from `call_transcripts` (`detected_intents`, `main_topics`) or from text.

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

**Handling:** to be decided with the workflow (e.g. a labeled, team-generated Portuguese test set), and reported as a limitation.
