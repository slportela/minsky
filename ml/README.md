# ml

Training and offline evaluation of the **learned router**: the intent of the customer's first dispute message. It is the brief's required learned component, compared against baselines (`docs/requirements.md` P4.4, J5). The backend only runs inference (`backend/src/minsky_api/router/`) on the artifact produced here.

```
 ml/router/generate.py ── seeded templates, es (MX/CO/AR) + pt-BR ──▶ ml/router/data/{train,val,test_unseen}.jsonl
     family A (templates + merchants) → train / val, split by whole template
     family B (other phrasings, other merchants) → test_unseen only
 ml/router/handwritten.jsonl ── 80 drafted messages, scored only
     │
     ▼  ml/router/train.py: same splits and metrics for every rung
 majority class → keyword rules → TF-IDF (word 1-2 + char 3-5 grams) + logistic regression (numpy)
     │  feature set and L2 chosen on val (macro-F1); abstain threshold chosen on val (precision of routed ≥ 0.95)
     ▼
 backend/src/minsky_api/router/model.json  +  ml/reports/<run_id>.{json,md}
```

Labels: `not_me` (unrecognized charge, possible fraud), `wrong_amount`, `duplicate`, `not_received` (product, service or refund never arrived), `out_of_scope` (balance, loans, cards, address, general questions, asking for a human without a dispute).

## Run

```bash
make router   # = uv run python -m ml.router.generate && uv run python -m ml.router.train  (~2 min on a laptop)
```

`generate` is deterministic (fixed seed; a test checks the committed files equal a fresh run). `train` writes the backend artifact and a report; `--no-artifact` writes the report only. `make router` is not part of `make ci`; the tests (`tests/test_ml_router.py`, `backend/tests/test_router.py`) are.

The featurizer (`backend/src/minsky_api/router/features.py`) is the only implementation: training imports it from the backend, so serving computes the same features. The serving classifier is pure Python (no numpy in the backend) and is evaluated as served: the reported `tfidf_logreg` numbers come from the rounded artifact scored by `classifier.py`. Parity is tested both ways (numpy vs pure Python on 53 messages; `model.json` carries 20 reference probabilities computed at training time).

## Tracking (ADR 0007)

Every run writes `ml/reports/<run_id>.json` and `.md`: git SHA and dirty flag (taken before anything is written), sha256 and size of every data file, generator version, parameters (including the keyword lists), the val search, metrics for every rung and split, and the artifact sha256. `model.json` names the run that produced it (`report_id`). The committed run is [`20261004T014637Z-5cf4d3f`](reports/20261004T014637Z-5cf4d3f.md) (clean tree).

## Latest results

Run `20261004T014637Z-5cf4d3f`. Selected: char 3-5 grams, L2 = 0.001, 12,262 features; threshold **0.357** (softmax confidence over five labels). Accuracy with Wilson 95% intervals; latency is per message, single-threaded, on a laptop.

| Rung | Split | n | Accuracy % | 95% CI | Macro-F1 | p50 / p95 ms |
|---|---|---:|---:|---|---:|---|
| majority | val | 600 | 21.3 | [18.2, 24.8] | 0.070 | <0.001 |
| majority | test_unseen | 600 | 22.8 | [19.7, 26.4] | 0.074 | <0.001 |
| majority | handwritten | 80 | 20.0 | [12.7, 30.0] | 0.067 | <0.001 |
| keywords | val | 600 | 66.2 | [62.3, 69.8] | 0.668 | 0.006 / 0.008 |
| keywords | test_unseen | 600 | 57.3 | [53.3, 61.2] | 0.588 | 0.006 / 0.008 |
| keywords | handwritten | 80 | 56.2 | [45.3, 66.6] | 0.581 | 0.004 / 0.007 |
| tfidf_logreg | val | 600 | 90.2 | [87.5, 92.3] | 0.902 | 0.078 / 0.113 |
| tfidf_logreg | test_unseen | 600 | 81.8 | [78.5, 84.7] | 0.813 | 0.063 / 0.090 |
| tfidf_logreg | handwritten | 80 | 95.0 | [87.8, 98.0] | 0.950 | 0.046 / 0.069 |

Abstention (tfidf_logreg, threshold fitted on val):

| Split | n | Coverage % | Routed | Precision of routed % | 95% CI |
|---|---:|---:|---:|---:|---|
| val | 600 | 73.5 | 441 | 95.0 | [92.6, 96.7] |
| test_unseen | 600 | 66.0 | 396 | 94.2 | [91.4, 96.1] |
| handwritten | 80 | 90.0 | 72 | 97.2 | [90.4, 99.2] |

Reading the numbers:
- The learned model beats the keyword rules by 24.5 points on unseen phrasings (81.8 vs 57.3; intervals do not overlap). Both drop from val to test_unseen; the rules also drop to 56% on the handwritten draft.
- Unseen phrasing costs ~8 points (val 90.2 → test_unseen 81.8). The main confusions on test_unseen are `not_received` → `wrong_amount` (24 of 122) and `not_me` → `out_of_scope` (26 of 118); per-language and per-class tables are in the report. Portuguese is weaker on val (82.8% vs 95.0% Spanish, n = 239 / 361).
- At the val threshold, precision of the auto-routed messages holds on unseen phrasing (94.2%, CI [91.4, 96.1]) while coverage drops from 73.5% to 66.0%: the model routes less when the phrasing is new, rather than guessing more.
- The handwritten score (95.0%) is higher than test_unseen and should **not** be read as real-world accuracy: the draft was written by the same coding agent that wrote the templates, so it likely shares their vocabulary. Its errors are still informative: "Gracias, eso era todo" → `wrong_amount` (0.56, routed), "la tienda dice que ya me devolvió pero yo no veo nada" → `not_me` (routed); "¿por qué rechazaron mi tarjeta?" and "asdf qwer" abstain.

## Model card

- **Intended use**: suggest the intake path for the first customer message of a chat (four dispute reasons or out of scope), and abstain when unsure. The router never authorizes: identity, eligibility and the dispute decision stay in code (`policy/`). An abstention means "ask the customer or let the LLM read it".
- **Model**: multinomial logistic regression on TF-IDF features (sublinear tf, L2-normalized, accent-folded, lowercased), trained with full-batch Nesterov gradient descent from zero (400 iterations, deterministic). Artifact ~0.9 MB JSON; weights rounded to 6 decimals.
- **Data provenance**: all training and test text is **generated** from templates (`provenance: "generated"`, `generator_version: router-gen-1`). The dataset has no dispute conversations: call transcripts are two balance-inquiry templates with 42 variants and `main_topics` is the label (`docs/data_findings.md` H24, H25). The organizers accept generated training data if justified; labels are the intent of the template, never a model's judgment. Merchant names are real `transactions.merchant_name` values from the bank data.
- **Held-out design**: `val` uses family A templates never seen in training (2 per label and language), `test_unseen` uses family B: different phrasings, different merchants and product nouns. It measures generalization to new templates written by the same author, not to real customers.
- **Handwritten set**: `ml/router/handwritten.jsonl` (80 messages, 16 per label, 52 es / 28 pt, with hard cases: very short, indirect, two intents, code-switching). `provenance: "team-written-draft"`: it was **drafted by a coding agent** and must be reviewed and extended by a human before it counts as the held-out test. It is never used for training or threshold selection.
- **Limitations**:
  - No real customer text anywhere; results on generated data are not real-world performance.
  - Portuguese has no source data at all: the Portuguese text is generated, like everything else.
  - Templates are short single-intent messages; long messages, multi-turn context and messages mixing two disputes are barely covered.
  - The threshold is fitted on val, which shares the generator with test_unseen; on real traffic coverage and precision will move and must be re-measured.
  - Confidences are softmax scores of a regularized model (the max is often 0.3-0.6), not calibrated probabilities.
  - Not integrated into the orchestrator yet (`agent/` is being changed separately).

## Why this matters for the business

Most first messages are one of a few frequent requests. The router recognizes them in well under a millisecond on a CPU, without a model call, so the common path costs no tokens and adds no latency. Its abstain threshold is what makes the system ask instead of guessing: when a message does not look like anything it learned (66% of unseen phrasings are routed, at ~94% precision), it hands over to a clarifying question or to the LLM rather than starting the wrong dispute.
