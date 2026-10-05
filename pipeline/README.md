# pipeline

The data pipeline: organizer S3 → bronze → silver → gold (dbt-duckdb) → Postgres `bank.*`. The overview is in the [README](../README.md#5-the-data-pipeline); data issues are in [`docs/known_issues.md`](../docs/known_issues.md).

## Steps

```
 organizer S3 ──bronze──▶ our S3 /bronze ──mirror──▶ data/bronze ──silver──▶ data/lake/silver ──gold──▶ data/lake/gold ──load──▶ Postgres bank.*
 (read-only)   verified    + run manifest   (local)                (dbt-duckdb,                (read models,             (atomic swap,
               copy                                                 ~90 s rebuild)              ~35 s with load)          ops.load_runs)
                                                                          └──────────────── publish ──▶ our S3 /silver, /gold
```

- `make pipeline` runs every step: bronze, mirror, silver, gold (build, export and load into Postgres; needs `make up`), publish. Bronze, mirror and publish are incremental.
- dbt reads local files: reading ~1k small files from S3 takes minutes per table.
- Silver models and `_silver.yml` are generated from `pipeline/data_dictionary.py` by `pipeline/transform/generate_silver.py`. Don't edit them by hand.
- Test policy:
  - **error** = what silver guarantees (primary keys, casts);
  - **warn** = source issues against the dictionary, also flagged per row in `_dq_issues`.
- `make docs` serves dbt docs with lineage.

## First run on a clean checkout

- `make silver` ends with one error. The singular test `tests/gold/transactions_complete.sql` is selected together with the silver models (it depends on `transactions`) but reads `warehouse.bank.transactions`, which only `make gold` creates. The 12 silver models are built before it fails, but `make pipeline` stops there. Run the remaining steps by hand: `make gold`, then `make publish`.
- `pipeline/load_gold.py` does not read `.env`. It connects to `GOLD_DATABASE_URL`, or to `postgresql://minsky:minsky@127.0.0.1:5433/minsky` when that is unset. The Postgres from `make up` takes its password from `POSTGRES_PASSWORD` in `.env` (`change-me` in `.env.example`), so with the example file `make gold` fails with a password error. Set `POSTGRES_PASSWORD=minsky` before the first `make up`, or export `GOLD_DATABASE_URL`.

## Read models (gold)

What the agent reads: `bank.customers`, `bank.products`, `bank.transactions` (full history), `bank.customer_complaint_stats`, `bank.resolution_benchmarks` and `bank.dispute_scenarios`. Tables, columns and conventions: [`docs/read_models.md`](../docs/read_models.md).

```bash
make up                        # Postgres must be running (AWS_PROFILE set in .env or the shell)
make gold                      # build + test (dbt), export to data/lake/gold, load into Postgres bank.*
docker compose -f compose.yaml -f compose.local.yaml exec postgres psql -U minsky -d minsky
```

```sql
select transaction_date, merchant_name, amount, currency, amount_usd, transaction_status
from bank.transactions where customer_id = 'CLI-D3GMCTFDAIOT'
order by transaction_date desc limit 10;
```

Postgres is also reachable from the laptop on `127.0.0.1:5433` (user, password and database `minsky`). Each load is recorded in `ops.load_runs`.
