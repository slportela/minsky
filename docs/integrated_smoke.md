# Live L2 and integrated smoke runbook

Run on dev only. Read `docs/evals.md`; never edit or tune on the locked test split. The existing
scripted smoke is a diagnostic, not model evidence. PostgreSQL `bank.*` comes from gold; disputes,
blocks, handoffs and conversation state are still process-local and disappear on API restart.

## Preconditions

- Corrected #24 backend, #25 eval harness, and #23 frontend for browser tests. Keep source revisions
  separate and recorded; retarget stacked PRs only after their dependencies merge.
- A pinned model, compatible endpoint and API key in the launching process's secure environment.
  Agents must not inspect `.env`, print keys, or receive them in chat. With the user's authorization,
  the backend settings loader can read the local file at runtime: set `MINSKY_ENVIRONMENT=local`
  in the process and, for a worktree, `MINSKY_ENV_FILE=/absolute/path/to/.env`. This populates only
  backend settings and never exports organizer AWS credentials. See `backend/README.md`.
- Explicit uncached input/output rates for that exact model, from the provider's billing configuration.
  The runner does not guess rates. Its accounting is usage-priced, not an invoice; failed-call spend
  may be unknown, so reservations are never refunded.
- Initial **total allowance approved by Sebastián: USD 1**. Split it as USD 0.40 for baseline,
  USD 0.40 for corrected L2 and USD 0.20 for live browser checks. Each process has its own cap;
  summing the allowances prevents the combined runs exceeding the approved initial allocation.
  Print estimates first; reduce trials/cases if they do not fit. Do not increase the combined cap
  without explicit authorization. Scripted checks make no provider calls.

SDK retries are disabled for paid diagnostics so hidden retry charges do not bypass accounting.
A conservative UTF-8 byte allowance covers instructions/input plus schema/framing; output is capped
at 256 tokens for extraction. Reservations stop dispatch before exhaustion. Verify this bound for a
new provider/tokenizer before using it. Unexpected reported usage exhausts the budget and stops later
calls. Timeouts preserve request/call evidence, and later trials still run within the remaining allowance.

## 1. Matched live L2

From the corrected eval checkout, with `INPUT_RATE` and `OUTPUT_RATE` securely/configurably supplied
as non-secret numeric rates (USD per million tokens):

```bash
uv run python -m evals.runner --include-drafts --extractor real --trials 3 \
  --max-cost-usd 0.40 --input-usd-per-million "$INPUT_RATE" \
  --output-usd-per-million "$OUTPUT_RATE" --estimate-only
```

Archive the unmodified pre-correction backend/prompt source from `2213a31` into a separate directory.
Run the *same corrected harness and dev cases* against that source, then the corrected backend:

```bash
PYTHONPATH=/path/to/baseline/backend/src uv run python -m evals.runner \
  --include-drafts --extractor real --trials 3 --legacy-auth-baseline \
  --backend-revision 2213a31 --max-cost-usd 0.40 \
  --input-usd-per-million "$INPUT_RATE" --output-usd-per-million "$OUTPUT_RATE" \
  --output evals/runs/live-before
uv run python -m evals.runner --include-drafts --extractor real --trials 3 \
  --max-cost-usd 0.40 --input-usd-per-million "$INPUT_RATE" \
  --output-usd-per-million "$OUTPUT_RATE" --output evals/runs/live-after
uv run python -m evals.compare evals/runs/live-before evals/runs/live-after \
  --output evals/runs/live-delta.json
```

Estimate both source versions before either paid run. The legacy flag is only for the old API's
legitimate caller contract on policy cases; denied/expired sessions still expect rejection. Metadata
hashes imported backend files and runtime prompt files, with the harness SHA recorded separately.
Save all trial artifacts and review both failures and successes. Report counts/intervals, case-level
all-trials success, partial safety components, separate infrastructure errors, model usage, reserved
and usage-priced cost, and latency. A dev result is not a held-out claim. Unexpected output/latency
failures get a new dev regression before an agent fix; fix harness/label errors first.

## 2. Real PostgreSQL stores

Verify the existing gold load before rebuilding data. Supply `MINSKY_DATABASE_URL` securely; for the
repository's default local test database it points to `127.0.0.1:5433` with the Compose test credentials.
Do not print a custom connection string. `--gold-cases` reads only gold and rechecks labels with policy.

```bash
uv run python -m evals.runner --include-drafts --database postgres --gold-cases \
  --output evals/runs/postgres-scripted
```

This runs eight scenarios through the real API and pooled PostgreSQL store using scripted extraction.
It checks D01, D07, D09, both D06 block choices, cross-customer denial, legacy-header denial and expired
bearer denial. The eleven-case SQLite workload retains competing-filter and injected-fault controls.
Neither workload silently substitutes for the other. A paid PostgreSQL run uses the same price/cap
flags and needs an allocation within the total allowance; do not add another USD 1 run implicitly.

## 3. Browser → API → PostgreSQL

Build/start the corrected #23 UI in its checkout on a separate port:

```bash
npm --prefix frontend run build
npm --prefix frontend run start -- --hostname 127.0.0.1 --port 3011
```

In the corrected harness checkout, start the loopback gateway:

```bash
uv run python -m evals.browser_smoke --output evals/runs/browser-scripted --port 8011
# For live extraction, add:
# --extractor real --max-cost-usd 0.20 --input-usd-per-million "$INPUT_RATE" --output-usd-per-million "$OUTPUT_RATE"
```

Open `http://127.0.0.1:8011/chat`. The gateway creates ephemeral test sessions and saves a private
`test-credentials.json` beside the evidence; do not commit or publish this file. It is removed on
normal shutdown. Use its scripts and credentials; read-only gold is never modified. It records
HTTP results, model calls, tool audit and verified case writes, without headers/credentials.

Record each check with observed HTTP/state evidence and screenshots:

- D09: select transaction, confirm opening, match the displayed reference to the stored dispute.
- D07: handoff exists, reference and rule are present; no dispute/card block.
- D06: accepting blocks only the owned card; rejecting produces handoff without a new block.
  Restart the diagnostic gateway between these trials because the selected gold product may be the
  same and case writes are process-local. Fresh ephemeral credentials are generated each time.
- Foreign transaction: no successful read or disclosure. Missing/expired bearer: HTTP 401 before tools.
- Sí/No preserves an unsent draft; double-click does not add a duplicate accepted turn/write.
- “Nueva conversación” clears history/id; “Cambiar sesión” clears the credential and history.
- Injected backend failure/retry: repeat the isolated regression, then check UI recovery on the
  integrated fault fixture: start the gateway with `--fail-policy-once`. The next policy call fails
  once with HTTP 502, and the repeated confirmation must recover without a duplicate write.
- Restart API: verify documented loss of case/conversation state. This is a persistence limitation,
  not a successful durability check. Use a fresh diagnostic process, not the shared demo API.

The loopback gateway excludes Caddy/TLS and deployment. After integration/merge, repeat normal,
ambiguous and handoff paths through the real Compose/deployed origin. Do not bypass TLS warnings;
install/use the trusted local CA through the user's normal setup or use a valid demo certificate.

## Gates and recorded progress

- Local `make ci` passes; fixture/oracle/negative controls pass.
- Initial PostgreSQL diagnostic: 8/8, one scripted trial, no provider calls.
- Browser diagnostic: D09 read-back, D07 handoff, both D06 block choices, ownership denial,
  ambiguity, double-click, draft/session resets, expired identity and injected-failure recovery observed.
  Restart demonstrably loses case state; Caddy/TLS remains pending.
- Live-model dev delta is now recorded: baseline + amount-schema compatibility **21/36** versus
  corrected **36/36**, twelve cases × three trials, no infrastructure errors in the matched final runs.
  See `evals/reports/2026-10-02-live-l2.md`; earlier provider failures and the 35/36 first fix are retained.
- Native browser → corrected API → gold PostgreSQL with live extraction and controlled failure/retry
  is recorded in `evals/reports/2026-10-02-live-integrated.md`. Local dev gates are verified;
  deployed-origin smoke remains pending. Reported usage-priced cost for all attempts is USD 0.0124252,
  with conservative reservations of USD 0.2085836 against the approved USD 1.
- Grounding, language and prompt-injection graders remain unsupported and cannot be called green.
  Spanish-only smoke does not close Portuguese coverage. Writes are not PostgreSQL-persistent.

Attach matched evidence/report to the PRs and update requirements honestly. Merge #24 → #23 → #25 only
when the required live-model and integrated gates are met; rerun a final smoke on their combined source.
