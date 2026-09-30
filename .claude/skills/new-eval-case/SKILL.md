---
name: new-eval-case
description: Add an eval case (a simulated customer scenario with success criteria) to evals/cases. Use when turning a failure, a bug, a requirement or an attack idea into a test case.
---

# New eval case

1. Read `docs/evals.md` (sections "Golden dataset" and "Graders") and `evals/schema.py`. The schema is the contract; do not invent fields.
2. Decide the expected outcome from the **policy and the data**, never from what the agent currently does. If the policy does not decide it, stop and say the policy has a gap.
3. Pick the split: `dev` by default, `val` only when asked. **Never write to `evals/cases/test/`** (locked; see AGENTS.md).
4. Write `evals/cases/<split>/<id>.yaml`; the id is kebab-case and equals the file name.
   - `status: draft` until it is promoted (see "Promotion" in `docs/evals.md`).
   - `provenance: generated` and `generator_model: <your model id>` when you wrote it.
   - Simulator-only information goes in `user_scenario`; the agent never sees it.
   - Grade the end state (`env_assertions`) and forbidden events (`must_not`), not the path.
   - Every case also needs its mirror: if it should escalate, check there is a similar case that should not.
5. Run `make eval-check` and fix every error.
6. In the PR, say which failure or requirement the case comes from.
