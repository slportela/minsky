# 0002. Eval methodology: τ²-style simulation, end-state grading

- Status: proposed
- Date: 2026-09-26

## Context
The brief scores safe automated resolution, escalation quality, unsafe outcomes, latency and cost on held-out cases, by language and segment, with a validated judge. Anthropic's guidance for conversational agents is verifiable end-state outcomes plus rubrics, with a second LLM simulating the user, and it cites τ-bench/τ²-bench. τ²-bench (MIT) provides the domain structure (policy, tools, DB, tasks, user tools), a user simulator, and a reward that is the product of the listed components (DB, communicate, ...).

## Decision
- Cases are simulated conversations graded on the end state and on forbidden events; see `docs/evals.md`.
- The case format (`evals/schema.py`) follows the τ² task shape and adds provenance, slices, fault injection, safety and handoff criteria.
- The harness is τ²-bench if a half-day spike shows our system plugs in cleanly as a custom agent in a custom domain; otherwise we use our own runner on the same format. Our own graders (safety, grounding, handoff) run on saved trajectories in both cases.
- Headline metric: pass^k. Every rate is reported with its denominator and a 95% interval.

## Consequences
- The deterministic graders need a deterministic mock bank (seeded ids, fixed clock) and a tool-layer audit log.
- The simulator is a component that can fail, so it gets its own failure category.
- Spike criteria: custom agent adapter working, fault injection possible, trajectories exportable, Python version compatible.
