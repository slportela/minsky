# minsky

Factored Datathon 2026: LATAM Bank dataset, a synthetic banking dataset for Mexico, Colombia and Argentina (2023-06-17 to 2026-06-17).

## Setup
- Python env managed with uv: `uv sync`, then `uv run jupyter lab`.
- Credentials in `.env` (see `.env.example`); never commit `.env`.
- Data lives in S3 under `data/` (13 tables). Ignore `data_backup_20260831/`.

## Code
- `bank_data.py`: S3 reads (`load_table`, `read_table`, `headers`), caches Parquet in `data/cache/`.
- `data_dictionary.py`: the official data dictionary (columns, PKs, NOT NULL, enums, FKs).
- `quality.py`: checks against the dictionary.
- `notebooks/`: `explore_bucket` (bucket inventory), `validate_vs_dictionary` (data vs dictionary findings).
- Reference docs: `kickoff_docs/`.

## Data gotchas
- CSVs have a UTF-8 BOM: read with `encoding="utf-8-sig"`.
- Many categorical values are in Spanish, not the English listed in the dictionary.
- `customers.registration_branch_id` and `service_agents.assigned_branch_id` do not join to `branches`.
- `process_date` day cuts at 06:00, not midnight.
- `digital_events` is ~15.6M rows: load only the columns you need.
