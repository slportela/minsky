# 0013. Cases in Postgres, a triaged back-office queue and an agent console

- Status: proposed
- Date: 2026-10-04

## Context
- The brief scores a human handoff that carries the request, verified facts, actions, evidence and open questions (P3.3), and a frontend for it (J2). The console was an empty page and handoffs carried only a transaction id and a route.
- Cases lived in memory (`InMemoryCasesBackend`): a restart lost every dispute, card block and handoff.
- The data's clearest operational finding is that disputes are not triaged: critical and low-priority cases take the same ~15.6 days and breach the SLA at the same ~20 %. Intake alone does not touch that.

## Decision
- `cases.*` lives in Postgres (`store/cases_sql.py`) behind a `CasesBackend` protocol shared with the in-memory backend; both pass one contract test suite. The API creates its own tables at startup (the pipeline owns `bank.*`). `MINSKY_CASES_BACKEND=postgres` in compose, `memory` in tests and offline evals.
- Every dispute opened and every handoff created also queues a case (`tools/casework.py`). The tool reads the transaction from `bank.*` itself; what the customer said is kept separately and labeled as their claim. Priority, queue and due time come from a written, synthetic triage policy (`policy/triage.py`, `docs/dispute_policy.md`).
- `/api/console` lists, shows, claims and resolves cases. Agents authenticate with staff credentials (`MINSKY_STAFF_SESSIONS`) that are separate from customer sessions, so a customer credential cannot read the queue. Resolution is a note written by a person; the system still never decides an outcome or moves money.

## Consequences
- The story covers both halves: intake in the chat, triage for the back office. Cases survive restarts.
- The case store is synchronous (short indexed statements on a small pool); production moves it to an async driver and `create_all` to migrations, and adds row-level security (`docs/poc_to_prod.md`).
- Staff sessions are a POC adapter like ADR 0009; production uses the bank's workforce SSO.
- Conversation state is still process-local: a restart ends open conversations (cases are kept).
- The triage targets are ours, not the bank's; they would be set with the operations team. Wrong if agents find the order unhelpful, or if overdue counts stay high with the queue in use.
