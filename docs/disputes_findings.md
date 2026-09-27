# Disputes in the data: findings

What the LATAM Bank dataset offers for a **transaction-dispute workflow** (a customer asks to reverse a charge; the bank accepts or rejects it), what it does not, and what that implies for the system and its evals.

All numbers were measured on the full silver tables (`data/warehouse.duckdb`, built by `make silver`) on 2026-09-26. The data issues behind them are also listed, with handling, in [`known_issues.md`](known_issues.md). Queries to reproduce them are at the end.

## Summary

- Disputes live in **`complaints`**: subcategories `Cargo no reconocido` (unrecognized charge) and `Cobro indebido` (wrongful fee). **27,133 cases, 40 % of all complaints.**
- They are good for **aggregate baselines** of today's service: backlog, resolution time, SLA breach, rejection rate, compensation.
- They are **not usable as case-level ground truth**: a complaint cannot be traced to the disputed transaction, claimed amounts ignore the currency, and all text is templated.
- The **call transcripts** contain no dispute conversations at all (two balance-inquiry templates).
- So dispute cases for the system and its evals must be **built from `transactions` plus an explicit synthetic dispute policy**, with generated (and labeled) conversations.

## Where disputes are

| Table | Dispute signal | Rows |
|---|---|---|
| `complaints` | `category = Transactions`, `subcategory = Cargo no reconocido` | 12,297 (+1,283 with a null subcategory) |
| `complaints` | `category = Fees`, `subcategory = Cobro indebido` | 12,194 (+1,359 with a null subcategory) |
| `transactions` | `transaction_status = Reversed` | 44,750 (1.0 % of 4,425,008) |
| `transactions` | `is_fraud = true` | 4,316 (0.10 %) |
| `call_center_interactions` | `reason_category = Complaint` | 117,021 (17.1 % of 686,296) |
| `call_transcripts` | none | 0 |

Within `Cargo no reconocido`, the case types are Complaint 7,466, Claim 3,014, Request 1,214 and Suggestion 603.

Useful fields on a dispute complaint: `case_type`, `claimed_amount` + `currency`, `affected_product_id`, `priority`, `status`, assignment / first response / resolution / closing dates, `sla_breached`, `resolution_days`, `compensation_granted`, `resolution_satisfaction`, `is_repeat_complainer`.

## Baseline of the current service

Over the 27,133 dispute-like complaints (both categories):

| Status | Cases | Share |
|---|---|---|
| Open or In Process | 18,943 | 69.8 % |
| Escalated | 1,377 | 5.1 % |
| Resolved or Closed | 6,555 | 24.2 % |
| Rejected | 258 | 1.0 % |

- **Compensation**: recorded for 1,940 of the 6,555 resolved or closed cases (always > 0 where recorded). Median compensation ≈ 250-270 against a median claim ≈ 2,300-2,700: about **10 % of the claim** (median ratio 0.09-0.11).
- **Time**: median **15-16 days** to resolution for resolved and closed cases.
- **SLA**: breached in **~20 %** of cases (17.6-22.9 % depending on status).
- **Contact center**: calls with reason `Complaint` are resolved at first contact **43.6 %** of the time, against 91.5 % for transactional calls.

These support the case for automating dispute intake: a large share of complaints, a backlog of ~70 % open cases, two-week resolution times and low first-contact resolution. They are aggregate numbers only (see the limitations below).

## Limitations

### A complaint cannot be traced to the disputed transaction

- `complaints` has no transaction id, and `origin_interaction_id` is null in every row, so there is no link to the call either.
- Matching by product, amount and date: of the 8,143 `Cargo no reconocido` cases with an `affected_product_id`, only **2,107 (26 %)** have any transaction on that product in the 30 days before the complaint, **none** with an amount equal to `claimed_amount`, 19 with a reversed transaction and 5 with a fraud-flagged one.
- In the other direction, only **115 of 44,750** reversed transactions are followed by a `Cargo no reconocido` complaint from the same customer within 30 days.

Complaints and transactions were generated independently: the dataset has no "this charge was disputed, and this was the decision" record.

### Claimed amounts ignore the currency

`claimed_amount` is roughly uniform between 0 and 5,000 in every currency: the median is ≈ 2,500 in USD, MXN, COP and ARS alike (2,500 COP is under US$1). Only 33 % of dispute complaints have a claimed amount at all.

### All text is templated

- `description`: 2 distinct values across the 27,133 cases ("Queja relacionada con transactions" / "fees").
- `resolution`: 5 distinct texts.
- `call_transcripts`: all 171,321 transcripts are variants of **two balance-inquiry conversations**, with unfilled placeholders (`{monto}`, `{moneda}`, `{limite}`). `detected_intents` is always `consulta_general`. No transcript mentions a charge, a fee, a complaint or fraud, including the 29,198 attached to Complaint calls.

There is no real customer phrasing to learn intents from, seed a simulator with, or ground a dispute dialogue on.

### Fraud flags are not a learning signal

`fraud_score` is at most 30 for every non-fraud transaction, so any score above 30 is fraud with 100 % precision (75.6 % recall). A fraud classifier on this score is trivial and cannot count as the learned component.

### Other issues that affect disputes

- Mexico operates in USD: there are no MXN products or transactions, but 5,487 complaints claim amounts in MXN.
- `transactions.amount_usd` is null for every USD transaction.
- Spanish only: no Portuguese source data.

## Implications for the system

| Need | Source |
|---|---|
| Why disputes, and today's baseline | `complaints` and `call_center_interactions`, aggregated (numbers above) |
| The disputed charge and its facts (amount, merchant, date, status) | `transactions`, looked up for the authenticated customer |
| Whether a dispute is accepted, rejected or escalated | An explicit, written **synthetic dispute policy** (thresholds, time windows, evidence), labeled as synthetic. The data cannot provide it |
| Eval cases and their expected outcomes | Built from real `transactions` records + the policy (`provenance: data-derived`), never from `complaints` outcomes |
| Conversations (Spanish and Portuguese) | Generated, with `provenance: generated` and the generator model recorded |
| The learned component | Not fraud on `fraud_score` (leaky). An intent/routing model trained on generated, policy-labeled utterances, compared against keyword and TF-IDF baselines, is a candidate |

## How to reproduce

```python
import duckdb
con = duckdb.connect("data/warehouse.duckdb", read_only=True)

# Dispute-like complaints by status
con.sql("""
    select status, count(*) as n
    from complaints
    where category in ('Transactions', 'Fees')
    group by 1 order by 2 desc
""").show()

# Can a complaint be matched to a transaction on the same product?
con.sql("""
    select
        count(distinct c.complaint_id) filter (where t.transaction_id is not null) as any_tx_prior_30d,
        count(distinct c.complaint_id) filter (where t.amount = c.claimed_amount) as exact_amount
    from complaints c
    left join transactions t
      on t.product_id = c.affected_product_id
     and t.transaction_date between c.creation_date - interval 30 day and c.creation_date
    where c.subcategory = 'Cargo no reconocido' and c.affected_product_id is not null
""").show()

# Claimed amounts by currency
con.sql("""
    select currency, count(*) as n, median(claimed_amount) as median_claim
    from complaints
    where category in ('Transactions', 'Fees') and claimed_amount is not null
    group by 1
""").show()

# What the transcripts are about
con.sql("""
    select regexp_extract(full_text, 'Cliente: ([^\\n]*)', 1) as first_customer_line, count(*) as n
    from call_transcripts group by 1 order by 2 desc
""").show()
```
