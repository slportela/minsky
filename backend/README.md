# backend

FastAPI service for dispute intake: the orchestrator, the policy, the mock bank tools and identity. Design: [`docs/solution.md`](../docs/solution.md).

## One customer turn through the modules

```
 POST /api/chat/turn
   │
   ▼
 api/            validate input, resolve the session  ──(no session / expired)──▶ 401, re-authenticate
   │
   ▼
 agent/          orchestrator: the state machine (steps 1-8) for this conversation
   │  ├─▶ guardrails/  input checks (injection signals)
   │  ├─▶ router/      intent + dispute reason (learned); low confidence → llm/
   │  ├─▶ llm/         extract details (structured output) · choose the reply act and wording · summarize handoffs
   │  ├─▶ tools/       get_transactions · get_transaction · classify_reply · open_dispute ·
   │  │                get_dispute · block_card · create_handoff; each checks the session and writes an audit record
   │  ├─▶ policy/      decide(facts) → route + rule id (pure, no I/O)
   │  └─▶ guardrails/  output checks: every fact grounded in tool results, reply language
   ▼
 store/          async SQLModel reads over bank.*; cases.* (disputes, handoffs, blocks, audit, case queue)
                 in Postgres (SqlCasesBackend) or in memory (tests), behind the CasesBackend protocol
 observability/  one trace per turn: model calls, tool calls, policy decisions, versions
```

## Rules for this code

- `policy/` has no I/O and no model calls. It is tested rule by rule (`tests/test_policy_disputes.py`).
- `tools/` never take a customer id from the model: they read it from the session. Every tool has a denial test. Plain async + Pydantic (no LangGraph); `open_dispute` / `block_card` require `confirmed=True`. `classify_reply` classifies a reply to a confirmation the system already sent, checks the session, writes an audit row, and does not act.
- `store/` reads `bank.*` by entity key (async, pooled). Customer authorization is checked once in identity/tools, not on every store query. Writes go through the `CasesBackend` protocol: `SqlCasesBackend` (Postgres `cases.*`, `MINSKY_CASES_BACKEND=postgres`, the compose default) or `InMemoryCasesBackend` (tests, offline evals). Every dispute and handoff also queues a triaged back-office case (`tools/casework.py`, `policy/triage.py`), served to agents by `api/console.py` with staff credentials (`MINSKY_STAFF_SESSIONS`, ADR 0013).
- `identity/` resolves a server-provisioned, expiring bearer credential to `ToolSession`; customer ids
  from headers or conversation text do not authenticate (ADR 0009). OTP/Cognito are pending.
  Conversations are customer-bound; history validation and state commit are serialized per conversation.
  Failed turns leave prior history intact; writes use idempotency for safe read-back retries.
- `llm/` talks to an OpenAI-compatible endpoint (ADR 0008). Model ids come from config.
- `agent/` is a code-owned state machine (`run_turn`): extract → find/clarify → policy → confirm →
  open/block/handoff; never says an action is done before the tool result has been read back.
  Budgets: `MINSKY_MAX_TURNS`, `MINSKY_MAX_CLARIFY_ATTEMPTS`.

## Run

```bash
make up                                       # full stack with compose (see infra/README.md)
uv run --package minsky-api pytest backend/tests
MINSKY_ENVIRONMENT=local uv run --package minsky-api uvicorn minsky_api.main:app --reload
```

Direct backend and eval runs load the repository-root `.env` only when the **process environment**
explicitly sets `MINSKY_ENVIRONMENT=local`. Setting it inside `.env` alone does not enable loading.
Existing process variables take priority. For a worktree, set `MINSKY_ENV_FILE` to the absolute path
of your local file; an explicitly selected missing file fails at startup. Without the opt-in (including
demo/production), settings come only from the process environment. Compose already reads `.env`
and injects selected values, so it needs no additional loader.

The loader populates backend `MINSKY_*` settings without exporting dotenv entries into `os.environ`.
It does not activate `AWS_PROFILE` or the organizer's `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`.
For direct AWS clients, select our account's profile in the process environment separately; local
Compose injects `AWS_PROFILE` via `compose.local.yaml`. Never print or serialize resolved settings.

## Trusted test sessions

Provision `MINSKY_TEST_SESSIONS` in the server environment as a JSON object mapping random bearer
credentials to `{customer_id, expires_at}` (timezone-aware ISO datetime; optional `state`). Distribute
each credential only to its synthetic test user; send it as `Authorization: Bearer <credential>`.
Never send credentials to the model or put them in transcripts, git, or logs. Missing/invalid/expired
credentials return 401; missing or malformed server configuration fails closed with 503. An id alone
and the old `X-Minsky-Customer-Id` header do not authenticate. This is a trusted test-session adapter,
not a real identity provider; rotate credentials, use HTTPS, and migrate to OTP/Cognito for production.

## Offline regression delta

`uv run python -m evals.regression_smoke --output evals/runs/pr24-regressions.json` exercises the real
HTTP route, policy, tools, and SQL queries on an isolated SQLite fixture. Extraction is a scripted
test double. This checks identity denial, multi-turn filters, and retry recovery, not production
prompt/model quality or Postgres behavior. See `evals/reports/2026-10-02-pr24-regressions.md`.
