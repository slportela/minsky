# minsky

Factored AI & Data Hackathon 2026: an **AI-first banking customer-service system** on the LATAM Bank dataset (synthetic; Mexico, Colombia, Argentina; 2023-06-17 to 2026-06-17). Submission deadline 2026-10-05.

## Read in this order

```
  1. docs/challenge.md   what we must build and how it is scored (one page)
          │
  2. AGENTS.md           how we work: rules, commands, conventions (agents and humans)
          │
  3. docs/evals.md       how we prove it works
          │
  4. docs/adr/           decisions and why;   docs/known_issues.md: data surprises
```

## Repository map

```
minsky/
├── README.md              you are here
├── AGENTS.md              rules for agents and humans (CLAUDE.md imports it)
├── docs/
│   ├── challenge.md       START HERE: the problem, scope, scoring, plan
│   ├── evals.md           eval strategy: golden dataset, graders, online, self-improving loop
│   ├── known_issues.md    data vs. documentation, with numbers and handling
│   ├── adr/               architecture decision records
│   └── references.md      sources behind the design
├── evals/                 eval harness: case schema, set checks, metrics, cases/{dev,val,test}
├── prompts/               versioned prompts (rules in prompts/README.md)
├── tests/                 unit tests and eval-harness tests
│
├── data_dictionary.py     ┐
├── bank_data.py           │  data pipeline: organizer S3 → bronze → silver
├── ingest_bronze.py       │  (the dictionary is the contract; transform/ is dbt-duckdb)
├── quality.py             │
├── transform/             ┘
├── notebooks/             evidence behind known_issues.md
├── kickoff_docs/          organizer documents (read-only)
└── .claude/  .github/     shared Claude Code settings and skills; PR template
```

## Status

| Built | Next |
|---|---|
| Data pipeline to silver; data issues documented | Choose the workflow from the data (`docs/challenge.md`) |
| Team rules, ADRs, local CI | Workflow policy, mock bank tools, orchestrator |
| Eval strategy, case schema, set checks, metrics, 5 example cases | Eval runner, customer simulator, graders |

## Setup

```bash
cp .env.example .env   # organizer (read-only) credentials + our bucket; never commit .env
make setup             # uv sync + git hooks
make ci                # lint, typecheck, tests, eval-case checks: must pass before any PR
make help              # all targets
```

## Data pipeline

```
 organizer S3 ──bronze──▶ our S3 /bronze ──mirror──▶ data/bronze ──silver──▶ data/lake/silver ──publish──▶ our S3 /silver
 (read-only)   verified    + run manifest   (local)                (dbt-duckdb,                (local)
               copy                                                 ~90 s rebuild)
```

- `make pipeline` runs all four steps. Bronze, mirror and publish are incremental.
- dbt reads local files: reading ~1k small files from S3 takes minutes per table.
- Silver models and `_silver.yml` are generated from `data_dictionary.py` by `transform/generate_silver.py`. Don't edit them by hand.
- Test policy:
  - **error** = what silver guarantees (primary keys, casts);
  - **warn** = source issues against the dictionary, also flagged per row in `_dq_issues`.
- `make docs` serves dbt docs with lineage. `BRONZE_URI` / `BRONZE_AWS_PROFILE` in `.env` point at our bucket.
