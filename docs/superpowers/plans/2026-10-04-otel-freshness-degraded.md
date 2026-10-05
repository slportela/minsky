# OTel, freshness, update fixture, degraded mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close four small/medium gaps from `docs/requirements.md`: real OpenTelemetry export to Phoenix, a freshness check on `ops.load_runs`, a late/corrected partition fixture (P4.3), and model-outage handoff (P6.1 degraded mode).

**Architecture:** Keep the existing contracts. OTel is configuration (`OTEL_EXPORTER_OTLP_ENDPOINT`) plus a few spans; Postgres audit stays. Freshness is a read-only check over `ops.load_runs`. The update fixture is a tiny pipeline test, not a production scheduler. Degraded mode reuses/rebases PR #38: verified `create_handoff` on provider/schema failure, never a false promise.

**Tech Stack:** Python 3.12, OpenTelemetry SDK + OTLP HTTP exporter, Phoenix (compose profile `observability`), DuckDB/Postgres pipeline, FastAPI chat route, pytest.

**Spec:** `docs/requirements.md` (P6.1, P4.2, P4.3), ADR 0007, `docs/architecture.md` “Degraded mode”, PR #38 body.

## Global Constraints

- AWS-only / self-hosted extras (AGENTS rule 6). Phoenix stays optional compose; no SaaS APM.
- Do not edit `evals/cases/test/`.
- Model never authorizes; degraded path may only report a handoff after tool read-back.
- No secrets in git. Empty `OTEL_EXPORTER_OTLP_ENDPOINT` means no export (local default).
- Small PRs preferred: one concern each (OTel | freshness+fixture | degraded), or two commits on one branch if time is tight.
- Out of scope: VictoriaLogs, RLS, Cognito/OTP, nl_assertions judge, conversation state in Postgres, demand-pattern analysis.

## File map

| Path | Role |
|---|---|
| `backend/src/minsky_api/observability/` | OTel setup + helpers (`start_span`, configure exporter) |
| `backend/src/minsky_api/agent/orchestrator.py`, `llm/client.py`, `tools/*` | Minimal span sites (turn, model, tool) |
| `backend/pyproject.toml` / `uv.lock` | OTel dependencies |
| `compose.yaml`, `.env.example` | Already have endpoint; document pin + usage |
| `pipeline/check_freshness.py` | Fail if last `ops.load_runs` row is older than threshold |
| `pipeline/tests/` or `tests/test_pipeline_*.py` | Freshness + late/corrected fixture tests |
| `pipeline/fixtures/late_corrected/` | Tiny labeled inputs for P4.3 |
| `backend/src/minsky_api/agent/degraded.py`, `api/chat.py` | From #38, rebased on current `main` |
| `docs/requirements.md`, `pipeline/README.md`, ADR 0007 if status changes | Honest status updates |

---

### Task 1: OpenTelemetry → Phoenix

**Requirements:** With the observability profile up and `OTEL_EXPORTER_OTLP_ENDPOINT` set, one chat turn produces at least a turn span and a child for the model or a tool. With the endpoint empty, the API behaves as today (no crash, no export).

- [ ] Add deps with `uv add --package minsky-api opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http` (pin versions via lock).
- [ ] Implement `backend/src/minsky_api/observability/otel.py`: configure TracerProvider + OTLP HTTP exporter only when endpoint is non-empty; no-op otherwise.
- [ ] Call configure once at app startup (`main.py`).
- [ ] Add spans: `chat.turn` (around `run_turn`), `llm.respond` (around `LLM.respond`), `tool.<name>` (around one or two hot tools, e.g. `classify_reply` and `open_dispute` — same helper for all tools if cheap).
- [ ] Attributes: `conversation_id`, `phase`, `tool`, `model` when known. No customer free text, no secrets.
- [ ] Unit test: with endpoint unset, configure is a no-op; with a fake exporter/in-memory, creating a span does not raise.
- [ ] Manual note in `backend/README.md` / `infra` or `.env.example`: `docker compose --profile observability up` and set `OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006`.
- [ ] Update `docs/requirements.md` P6.1: OTel export done for turn/model/tool; safe fallback still separate until Task 4.
- [ ] `make ci` green.
- [ ] Commit: `Export OpenTelemetry spans to the configured OTLP endpoint.`

---

### Task 2: Freshness check (P4.2)

**Requirements:** A command reads `ops.load_runs` for required tables (at least `transactions`, `customers`, `products`). Exit 0 if the newest `loaded_at` per table is within `MINSKY_FRESHNESS_MAX_AGE_HOURS` (default 168). Exit non-zero otherwise, with a clear message. No Postgres → fail loud.

- [ ] Write failing test that inserts old/new rows into a temp schema or mocks the query result.
- [ ] Add `pipeline/check_freshness.py` (+ `make freshness-check`).
- [ ] Document in `pipeline/README.md`; set P4.2 note to “recorded + checkable; production alarm still pending”.
- [ ] `make ci` / targeted tests green.
- [ ] Commit: `Fail the freshness check when gold loads are too old.`

---

### Task 3: Late / corrected partition fixture (P4.3)

**Requirements:** A small labeled fixture proves that a late-arriving or corrected partition is applied correctly by the pipeline step under test (prefer the smallest real unit: bronze manifest / silver test / gold load contract — pick the layer that already has test harness). Offline only; label as fixture, not production improvement.

- [ ] Add `pipeline/fixtures/late_corrected/README.md` stating what is late vs corrected and the expected row counts/ids.
- [ ] Add fixture files (minimal CSV or parquet copies the chosen step already consumes).
- [ ] Add a pytest that runs the step on the fixture and asserts the corrected values win / late rows appear.
- [ ] Mark P4.3 🟡 or ✅ in `docs/requirements.md` with “fixture only”.
- [ ] Commit: `Add a late and corrected partition fixture for the pipeline.`

---

### Task 4: Degraded mode on model outage (P6.1)

**Requirements:** Provider timeout/connection/5xx and schema validation failures create a verified handoff (`assistant_unavailable`) and a customer-safe reply with the handoff id. Handoff failure → generic 503, no exception text. Rebase #38 onto current `main`; include `compose_speech` / speech `RuntimeError` in the same failure set if it still bypasses the path.

- [ ] Fetch `origin/fix/model-outage-handoff` (PR #38) and rebase onto `main`.
- [ ] Resolve conflicts with post-#37 speech/orchestrator code; map speech grounding failures into the degraded set if they currently 503 without handoff after a partial turn.
- [ ] Keep #38 tests; add one case for a speech/`RuntimeError` failure if missing.
- [ ] Update P6.1: degraded handoff done; OTel from Task 1 referenced.
- [ ] `make ci` green.
- [ ] Commit / PR: `Hand off when the model is unavailable.`

---

### Task 5: Docs pass

- [ ] `docs/requirements.md` statuses match reality for P4.2, P4.3, P6.1.
- [ ] `docs/known_issues.md` or `solution.md` known limitations: conversation state still process-local; VictoriaLogs not used; OTel → Phoenix in dev, ADOT later.
- [ ] Do not claim production CloudWatch export until ADOT is wired.

---

## Verification

```bash
make ci
make freshness-check   # after Task 2; needs Postgres with load_runs
# optional after Task 1:
# docker compose --profile observability up -d
# OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006  # or phoenix service name from API container
```

## Explicit non-goals

- VictoriaLogs / log shipper
- Full auto-instrumentation of FastAPI/httpx
- nl_assertions LLM judge
- RLS, Cognito, conversation persistence in Postgres
