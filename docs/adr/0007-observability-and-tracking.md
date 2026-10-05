# 0007. Observability and ML tracking: OpenTelemetry first, tools pluggable

- Status: accepted for local OTLP export; ADOT/CloudWatch pending
- Date: 2026-09-26
- Updated: 2026-10-04 — turn/model/tool spans export when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; Phoenix remains the optional compose UI

## Context
A production-ready system needs:
- **execution records** for every turn: the brief's audit artifact, and what the agent console shows;
- **traces** to debug and monitor LLM behavior;
- **tracking** of how the learned router was trained (a judging criterion).

The platforms that do this (Langfuse, Arize Phoenix, MLflow, CloudWatch) differ in weight and features, and our needs will grow as we build. We want to start light without closing any door.

## Decision
- **OpenTelemetry is the contract.** The backend is instrumented with OpenTelemetry. Any backend can receive the traces; adding or switching one is configuration (`OTEL_EXPORTER_OTLP_ENDPOINT`), not code.
- **Execution records in Postgres.** Append-only trace and audit tables: turn, tool calls, policy rule, model and prompt versions, tokens, latency. They feed the agent console and the eval reports.
- **Trace UI in development: Arize Phoenix** (one container, OpenTelemetry-native, datasets and eval experiments), as the optional compose profile `observability`. Check that its licence fits our use.
- **Production: CloudWatch + X-Ray** through ADOT (AWS-native) plus the audit tables. An LLM-specific UI (Phoenix or Langfuse) runs on ECS if the team needs it.
- **ML tracking: committed run reports.** Every router training run writes `ml/reports/<run>.json` + `.md`: git SHA, data snapshot, splits, parameters, metrics, model file hash. The backend config names the model it serves. **MLflow** plugs in if experiments grow (sweeps, many runs, a model registry).

## Consequences
- Few services to run now; the demo host stays small.
- We build the trace tables and the console view ourselves; both are deliverables anyway.
- Pluggable slots, each added through configuration:
  - **Langfuse** for richer LLM observability;
  - **MLflow** for experiment tracking;
  - any OTLP backend for traces.
