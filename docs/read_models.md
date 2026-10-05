# Read models (gold → Postgres `bank.*`)

The simulated bank the agent reads from. Built by dbt from silver (`pipeline/transform/models/gold/`), exported to the lake (`data/lake/gold/`, `s3://<bucket>/gold/`) and loaded into Postgres by `pipeline/load_gold.py`. `make gold` does all three; run `make up` first so Postgres is there.

## Schemas

| Schema | Holds | Written by | Read by |
|---|---|---|---|
| `bank` | Read models of the bank (this document) | The pipeline: every load replaces the whole schema | The API, through a read-only role |
| `cases` | Disputes, handoffs, sessions, audit log | The backend | The backend and the agent console |
| `ops` | `load_runs`: every gold load (run id, time, table, row count, git commit) | The pipeline, append-only | Freshness checks, audit |

Loads are atomic: tables are built in `bank_next`, indexed, and swapped in with a schema rename in one transaction, together with the `ops.load_runs` rows. Readers see the old bank or the new one, never a mix.

## Conventions

- Column names are the data dictionary's wherever the value comes straight from silver, so every column can be traced to the source. Derived columns have explicit names (`amount_usd_source`, `product_number_last4`, `is_repeat_complainer`).
- dbt contracts fix the column names and types: changing them breaks `make gold` until `models/gold/_gold.yml` is updated in the same PR.
- **Data minimisation**: only what the agent needs. Identity documents, contact details, address, income, credit score, location and branch stay in silver.
- **Simulated today**: everything time-relative is computed as of `2026-06-18`, the day the data ends (last transactions before 06:00, last complaints that morning), the dbt var `as_of_date`. It must equal the backend's `MINSKY_TODAY` (`backend/src/minsky_api/config.py`); `dispute_scenarios` fails the build if they differ.

## Tables

| Table | Grain · key | Rows | Used by |
|---|---|---|---|
| `bank.customers` | customer · `customer_id` | 150,000 | Identity, greeting, reply register, handoff context |
| `bank.products` | product · `product_id` | 400,000 | Showing the card in question; `block_card` |
| `bank.transactions` | transaction · `transaction_id` | 4,425,008 | `find_transactions`, `get_transactions`, `get_transaction`; the facts behind rules D01-D07 |
| `bank.customer_complaint_stats` | customer · `customer_id` | 150,000 | Rule D08 (repeat complainer) |
| `bank.resolution_benchmarks` | category × priority · both | 26 | Step 8, "your case usually takes ~15 days"; the service baseline |
| `bank.dispute_scenarios` | rule × transaction · `rule_id, customer_says_not_me, transaction_id` | 160 | Data-derived eval cases; demo test users |

### `bank.customers`
`customer_id`, `first_name`, `country` (Mexico, Colombia, Argentina), `segment`, `customer_status`, `detected_accent` (mexican, colombian, argentine, neutral: drives the reply register, e.g. voseo for Argentina).

### `bank.products`
`product_id`, `customer_id`, `product_type` (documented English values), `is_card`, `product_number_last4`, `currency`, `product_status` (Active, Blocked, Closed, Suspended), `opening_date`, `expiration_date`, `has_linked_app`.

### `bank.transactions`
The full history (2023-06-17 to 2026-06-18). `transaction_id`, `customer_id`, `product_id`, `transaction_date`, `process_date`, `transaction_type`, `transaction_category`, `amount`, `currency`, `amount_usd`, `amount_usd_source`, `channel`, `merchant_name`, `merchant_category`, `transaction_country`, `transaction_city`, `transaction_status` (Approved, Declined, Pending, Reversed), `response_code`, `is_fraud`, `fraud_score`.

- **`amount_usd` is never null.** The source leaves it null for every USD transaction and for ~5 % of ARS/COP ones. `amount_usd_source` says where the value comes from: `reported` (the source's value, 1,887,552 rows), `native_usd` (the currency is USD, so `amount`; 2,437,979) or `converted` (`amount` × the process date's rate; 99,477). On rows where the source reported a value, the same conversion reproduces it exactly (median ratio 1.0).
- Indexed on `(customer_id, transaction_date desc)`: a customer's latest transactions come back in ~1 ms.
- `fraud_score` leaks `is_fraud` (any score above 30 is fraud); see `known_issues.md`.

### `bank.customer_complaint_stats`
`customer_id`, `complaints_total`, `complaints_last_90d`, `last_complaint_at`, `is_repeat_complainer` (at least one complaint in the 90 days before the simulated today; 5,461 customers). One row per customer, so no row never has to mean "no complaints".

### `bank.resolution_benchmarks`
`category`, `priority` (`'all'` rolls a dimension up), `cases`, `resolved_cases`, `median_resolution_days`, `p75_resolution_days`, `sla_breach_rate`, `rejection_rate`. For example, Transactions / all: 13,580 cases, median 15 days, 20.2 % SLA breached, 0.9 % rejected.

### `bank.dispute_scenarios`
Real (customer, transaction) pairs that trigger each policy rule, decided by the backend's own policy (`minsky_api.policy.disputes.decide`), so the table follows the policy when it changes. Up to 20 per rule, from a deterministic 2 % sample of customers. Columns: `rule_id`, `route`, `offer_card_block`, `customer_says_not_me` (true: the "it wasn't me" claim is simulated), `customer_id`, `transaction_id`, and the facts the rule used (`transaction_status`, `transaction_date`, `days_before_as_of`, `amount_usd`, `is_fraud`, `fraud_score`, `is_repeat_complainer`).

- D04 (already disputed) is not here: it needs an existing dispute, which lives in `cases.*`.
- D06 is reached both by the bank's fraud flag (`is_fraud`) and by the simulated "not me" claim (`customer_says_not_me = true`).

## How to query

```bash
make up && make gold
docker compose -f compose.yaml -f compose.local.yaml exec postgres psql -U minsky -d minsky
```

From the laptop, Postgres is on `127.0.0.1:5433` (user, password and database `minsky`). The same tables are in `data/warehouse.duckdb` (schema `bank`) and as Parquet in `data/lake/gold/`.

## Not yet

- A read-only Postgres role (`app_read`) and Row-Level Security: in the design (`docs/poc_to_prod.md`), not in the local compose.
- `digital_events` (not in silver).
- Injected scenarios the data does not have (duplicate charges, prior disputes) belong to the eval harness as labeled fixtures, not to `bank.*`.
