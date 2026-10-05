# Run the demo end to end (local, Docker)

From an empty checkout to the three demo paths and the agent console, on one machine. Nothing here deploys anything. Production is `docs/architecture.md`.

## 1. Configure

```bash
cp .env.example .env
```

Fill in, in `.env`:
- the organizer's read-only keys (`S3_BUCKET_NAME`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`);
- `MINSKY_LLM_API_KEY`, the model key with a budget limit (ADR 0008);
- leave `AWS_PROFILE`, `BRONZE_URI` and `BRONZE_AWS_PROFILE` empty unless you use Bedrock or the team bucket. A placeholder profile name makes boto3 fail.

## 2. Data (about 1.6 GB, 15-30 minutes)

```bash
make setup
uv run python pipeline/ingest_bronze.py --dest data/bronze --tables \
  branches call_center_interactions call_transcripts campaign_sends complaints customers \
  daily_exchange_rates marketing_campaigns products satisfaction_surveys service_agents transactions
make silver        # data/warehouse.duckdb (~90 s)
```

`digital_events` (3.8 GB) is skipped: silver does not use it. The direct local download needs no team bucket; `make bronze` and `make mirror` do.

## 3. Start the stack and load the read models

```bash
make up            # Caddy + web + API + Postgres → https://localhost
make gold          # builds bank.* and loads it into Postgres
make demo-sessions # prints MINSKY_TEST_SESSIONS and MINSKY_STAFF_SESSIONS + a cheat sheet
```

Paste the two printed lines into `.env`, then `make up` again so the API picks them up. Keep the cheat sheet: it has one real customer per policy rule, with the message to type in Spanish and Portuguese. Credentials expire (14 days by default, `--days`).

## 4. Walk through it

Open https://localhost/chat, paste a credential from the cheat sheet, and type its message.

| Credential | What you will see |
|---|---|
| `demo-d09-…` | The charge is found and confirmed, then a dispute is opened and read back; the customer gets a `DSP-` reference |
| `demo-d01-…` / `demo-d02-…` | Nothing to dispute: declined (never charged) or already reversed, explained in plain words |
| `demo-d06-…` | "It wasn't me": a card-block offer (only for a card charge), then a handoff to the fraud team with an `HO-` reference |
| `demo-d07-…` / `demo-d08-…` | Above USD 500 or a repeat complainer: a dispute agent takes it |
| `demo-d05-…` | Older than 120 days: explained, a person is offered |

Write in Portuguese to get replies in Portuguese: the language of the first message sets the conversation's language.

Then open https://localhost/console with a staff credential. Every case from the chats is there, fraud first, with a due time, the verified facts, what the customer said, the actions taken, the open questions and the tool audit trail. Claim a case, then resolve it with a note. Cases are in Postgres and survive `make down` / `make up`.

Rehearsing changes the data: once the D09 customer's dispute is open, the next try answers "already open" (rule D04), and a blocked card stays blocked. Before the real demo, run `make demo-reset` (it deletes every case and restarts the API; the bank read models are untouched).

## 5. Evidence

```bash
make ci                                                        # all checks, offline
uv run python -m evals.compare_systems --split val             # today's process vs Minsky, held-out val (needs make gold)
make router                                                    # retrain and re-evaluate the learned router (~2 min)
```

The case store's contract tests run on memory and SQLite by default. To run them on the compose Postgres, in a
separate database (they drop and recreate its `cases` schema, so never point them at `minsky`):

```bash
docker compose exec postgres createdb -U minsky cases_test
MINSKY_TEST_POSTGRES_URL=postgresql+psycopg://minsky:minsky@localhost:5433/cases_test \
  uv run pytest backend/tests/test_cases_backends.py
```

The comparison and the router report are committed in `evals/reports/` and `ml/reports/`. Both are offline: a live run with the model key (`evals/README.md`, "Real extraction") is still needed before quoting them as system performance.
