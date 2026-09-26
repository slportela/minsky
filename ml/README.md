# ml

Training and offline evaluation of the **learned router**: intent and dispute reason. It is the brief's required learned component, compared against baselines. The backend only runs inference (`backend/src/minsky_api/router/`) on the artifact produced here.

```
 silver: call_transcripts.customer_text  +  call_center_interactions.contact_reason (label)
     │
     ▼  split by customer AND by time (train: older; val; test: newer), no customer in two splits
     │  leakage check: does detected_intents / main_topics give the label away?
     ▼
 ladder, same splits and metrics (macro-F1, per-class recall, calibration, latency, cost):
   keywords → TF-IDF + logistic regression → fine-tuned multilingual encoder → Laya (ADR 0004) → LLM zero-shot
     │
     ▼
 chosen model + confidence threshold (fitted on val) → artifact + model card → backend/router
```

Planned layout:
- `ml/router/` holds the training scripts;
- `ml/reports/` holds the L1 results.

The Portuguese performance is measured on generated, labeled text and reported as such.
