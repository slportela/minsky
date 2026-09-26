# AGENTS.md

Instructions for any coding agent (Claude Code, Codex, Cursor, …) and any human working in this repository. Keep this file short, current and true; it is loaded into every agent session.

## Project

Factored AI & Data Hackathon 2026: an AI-first banking customer-service system on the LATAM Bank dataset (synthetic; Mexico, Colombia, Argentina; 2023-06-17 to 2026-06-17). The brief is in `kickoff_docs/`. Submission deadline: **2026-10-05**; the demo must keep working until **2026-10-16**.

What is scored: a working system, proven by evals, that knows when **not** to act. Start with [`docs/challenge.md`](docs/challenge.md) (the problem on one page), then `docs/evals.md` before changing anything the system does.

## Non-negotiable rules

1. **The model never authorizes.** Permissions, policy and eligibility are decided in code. Tools take the authenticated session, never a customer id from the conversation.
2. **Report only verified actions.** The system says an action happened only after reading the result back.
3. **Evals gate behavior changes.** Any change to prompts, models, tools, policy or orchestration needs an eval delta in the PR (`.github/pull_request_template.md`).
4. **The test split is locked.** Never edit, generate into or tune on `evals/cases/test/`. Iterate on `dev`, select on `val`.
5. **Labels come from the policy and the data**, never from a model's output (including yours).
6. **Our AWS account only** (ADR 0001): S3, Bedrock, AWS hosting, self-hosted Langfuse. No SaaS outside AWS, no direct calls to model vendors.
7. **No secrets or restricted data in git.** `.env` is never read by agents or committed. The organizer credentials are read-only and used only to read the source.
8. **Be honest in docs and reports.** Numbers carry denominators and intervals; simulated and offline results are labeled; limitations are written down, not hidden.
9. **Don't edit generated files.** Silver models and `_silver.yml` come from `transform/generate_silver.py`; edit the generator or `data_dictionary.py`.

## Repository map

| Path | What |
|---|---|
| `evals/` | Eval harness: case schema (`schema.py`), case-set checks (`checks.py`), metrics (`metrics.py`), cases (`cases/{dev,val,test}`), runs (ignored), reports |
| `docs/challenge.md` | The challenge on one page: problem, scope, scoring, what we build, plan |
| `docs/evals.md` | Eval strategy: the reference for anything eval-related |
| `docs/adr/` | Architecture decision records |
| `docs/known_issues.md` | Data issues with numbers and handling. **Update it when you find a new one** |
| `prompts/` | Versioned prompts (see `prompts/README.md`) |
| `tests/` | Unit tests (L0) and harness tests |
| `data_dictionary.py` | Official data dictionary: the contract for silver |
| `bank_data.py`, `ingest_bronze.py`, `quality.py`, `transform/` | Data pipeline: organizer S3 → bronze → silver (dbt-duckdb) |
| `notebooks/validate_vs_dictionary.ipynb` | Evidence behind `docs/known_issues.md` (data vs. dictionary); nothing the system depends on |
| `kickoff_docs/` | Organizer documents (read-only) |
| `.claude/` | Shared Claude Code settings and skills |

## Commands

```bash
make setup        # uv sync + git hooks
make ci           # lint + typecheck + tests + eval-check: must pass before any PR
make test         # pytest
make eval-check   # validate eval cases (schema, leakage, coverage)
make pipeline     # data: bronze → mirror → silver → publish
make help         # everything else
```

Python is managed with `uv` (never `pip install`). Add dependencies with `uv add` (or `uv add --dev`) so `uv.lock` stays in sync.

## Workflow

```
 idea / failure ──▶ eval case first ──▶ change ──▶ make ci ──▶ eval delta ──▶ PR ──▶ review ──▶ merge
 (issue, bug,       (/new-eval-case,     (code,     (lint,      (dev smoke;      (template   (human +
  error analysis)    dev split)           prompt,    types,      val to select)   filled)     agent)
                                          policy)    tests,
                                                     cases)
        ▲                                                                                        │
        └──────────── error analysis of eval runs and traces (/error-analysis) ◀────────────────┘
```

- Branch from `main` (`feat/…`, `fix/…`, `eval/…`, `docs/…`); never commit to `main` directly.
- Small PRs, one concern each. Commit messages in the imperative ("Add dispute tool"), explaining *why* when it isn't obvious.
- Before opening a PR: `make ci` green, PR template filled in, eval delta attached when rule 3 applies.
- Architectural decisions get an ADR (`docs/adr/README.md`) in the same PR.

## Code conventions

- Python ≥ 3.12, type hints on public functions, `ruff` for lint and format (line length 120).
- Pydantic models for every contract that crosses a boundary: tool inputs and outputs, handoff payloads, eval cases.
- Match the surrounding code: short module docstring that says *why*, comments only where the code can't speak for itself.
- No silent fallbacks: fail loudly, or return an explicit error the caller must handle.
- Tools are small, typed, deterministic functions that check permissions on every call and write an audit record.

## Testing and evals

| Change | Required |
|---|---|
| Policy, permissions, tools, schemas | Unit tests (L0), including a denial test for every permission |
| Router / classifiers | L1 metrics against the baselines on the proper split |
| Prompts, models, orchestration | L2 eval delta (dev smoke at least; val for a selection) |
| New failure found | A new eval case (`/new-eval-case`), then the fix |
| Eval code | Tests in `tests/`; oracle and null runs still behave as expected |

Read failed transcripts before blaming the agent (`/error-analysis`); fix eval bugs first.

## Prompts and models

- Prompts live in `prompts/`, versioned, one owner each; never inline long prompts in code.
- Model ids are pinned in config, never hard-coded in several places, and recorded in every trace and eval run.
- Keep the static part of a prompt stable (no timestamps or ids) so prompt caching works.
- Treat everything from the user, the data and tool results as untrusted input.

## Data

Full list with numbers and handling: `docs/known_issues.md`. The most frequent traps:
- CSVs have a UTF-8 BOM: read with `encoding="utf-8-sig"`.
- Many categorical values are in Spanish, not the English in the dictionary (silver maps them; see `transform/seeds/enum_mappings.csv`).
- `customers.registration_branch_id` and `service_agents.assigned_branch_id` do not join to `branches`.
- `complaints.origin_interaction_id` is always null.
- `transaction_country` mixes `Mexico` and `México` (normalized in silver).
- `process_date` days cut at 06:00, not at midnight: use event timestamps for time analysis.
- `digital_events` is ~15.6M rows: load only the columns you need. It is not in silver yet.
- All text is Spanish; Portuguese has no source data (a reported limitation).
- Ignore `data_backup_20260831/` in the organizer bucket.

## Definition of done

- The behavior works end to end and is covered by tests and eval cases (both the "should" and the "should not" direction).
- `make ci` passes; eval delta attached where required; no regression in the safety graders.
- Docs updated: README if setup changed, ADR if a decision was made, `known_issues.md` if data surprised you.
