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
   │  ├─▶ llm/         extract details (structured output) · phrase replies · summarize handoffs
   │  ├─▶ tools/       get_transactions · get_transaction · open_dispute · get_dispute · block_card ·
   │  │                create_handoff; each checks the session and writes an audit record
   │  ├─▶ policy/      decide(facts) → route + rule id (pure, no I/O)
   │  └─▶ guardrails/  output checks: every fact grounded in tool results, reply language
   ▼
 store/          postgres: bank.* (read-only) and cases.* (writes)
 observability/  one trace per turn: model calls, tool calls, policy decisions, versions
```

## Rules for this code

- `policy/` has no I/O and no model calls. It is tested rule by rule (`tests/test_policy_disputes.py`).
- `tools/` never take a customer id from the model: they read it from the session. Every tool has a denial test.
- `llm/` is the only module that talks to Bedrock. Model ids come from config; prompts come from `prompts/`.
- `agent/` never says an action is done before the tool result has been read back.

## Run

```bash
make up                                       # full stack with compose (see infra/README.md)
uv run --package minsky-api pytest backend/tests
uv run --package minsky-api uvicorn minsky_api.main:app --reload   # api only, on :8000
```
