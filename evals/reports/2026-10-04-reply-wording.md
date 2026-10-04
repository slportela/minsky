# PR fix/chat-reply-wording: Reply wording offline delta

## What changed

**Defect (a) — transaction type noun in confirm_txn reply:**
Before: "Encontré este cargo: comercio desconocido, 5966.41 USD, 2025-12-30 (ref. TRX-...)."
After: "Encontré esta transferencia de 5966.41 USD del 2025-12-30 (ref. TRX-...)." (no merchant shown when null)

The noun is chosen from `transaction_type` (Transfer→transferencia, Withdrawal→retiro, Payment→pago,
Purchase→compra, Deposit→depósito, Adjustment→ajuste, unknown/null→movimiento).
Merchant is shown only when `merchant_name` is present.
`clarify_many` template is unchanged to keep its graded literal ("Encontré varios") and existing tests intact.

**Defect (c) — already_done after D03 pending/abstain:**
Before: "Esta conversación ya terminó. Si necesitas otro reclamo, inicia una conversación nueva."
After (D03): "Ese movimiento sigue pendiente de registro. Este sistema no tiene fecha de acreditación y no puede estimarla. Puedes consultar de nuevo más tarde o escribir 'asesor' si quieres hablar con una persona."
After (other flows): "Esta conversación ya terminó. Si tienes otro reclamo, inicia una conversación nueva."

No action is claimed in either reply. No posting date is invented (verified by unit test regex check).

## Offline before/after numbers

Run command: `uv run python -m evals.runner --include-drafts --output <path>`
Extraction mode: scripted (no provider calls, no model, no secrets, no cost).

| Run | Cases | Passed | Pass rate | 95% CI | Errors |
|---|---|---|---|---|---|
| Before (branch point = origin/main) | 12 | 12 | 100.0% | 75.7%–100.0% | 0/12 |
| After (this branch) | 13 | 13 | 100.0% | 77.2%–100.0% | 0/13 |

The "after" run includes the 12 original cases (all still pass) plus the new case
`dispute-transfer-null-merchant-es` (passes on the first run).
The sets differ by one case, so the automated `compare.py` tool was not used
(it refuses unmatched case sets); the delta is described manually above.

## What the offline runs do and do NOT prove

**Do prove:**
- No regression in the 12 pre-existing scripted cases.
- The new eval case (D01 Declined Transfer, null merchant) passes: the agent identifies the
  transaction, reports "no se cobró" (D01 inform), and the graders pass outcome/env/communicate/safety.
- The reply wording changes are verified by 14 new L0 unit tests (all deterministic, no model).
- All templates render without error under StrictUndefined Jinja2.

**Do NOT prove:**
- Production model extraction quality (scripted extraction replaces the LLM).
- Portuguese, mixed-language, or adversarial robustness.
- Live pass^k or multi-trial stability.
- The pending follow-up case (D03 after DONE) is NOT included (separate PR planned).

## Live run

Skipped. The worktree has no .env; the live extractor requires AWS Bedrock credentials.
Only deterministic reply code was changed; the extraction path is untouched.
Spend reserved for this PR: USD 0.00. Ledger not updated.
