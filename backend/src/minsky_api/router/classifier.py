"""Serve the learned router: intent of the customer's first dispute message, or "unsure".

Inference only, pure Python, on the artifact written by `ml/router/train.py`. The router never authorizes
anything: it only suggests which intake path to start. Below the threshold fitted on val it abstains, and the
caller must ask the customer or fall back to the LLM instead of guessing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from minsky_api.router.features import softmax, vectorize

DEFAULT_MODEL_PATH = Path(__file__).with_name("model.json")
LABELS = ("not_me", "wrong_amount", "duplicate", "not_received", "out_of_scope")


@dataclass(frozen=True)
class RouterPrediction:
    label: str  # argmax label, reported even when abstained (for logs); act on it only if not abstained
    confidence: float
    scores: dict[str, float]
    abstained: bool


@dataclass(frozen=True)
class RouterModel:
    version: str
    report_id: str
    labels: tuple[str, ...]
    feature_set: str
    vocab: dict[str, int]
    idf: tuple[float, ...]
    weights: tuple[tuple[float, ...], ...]  # one row per vocabulary term, one column per label
    bias: tuple[float, ...]
    threshold: float

    def predict(self, text: str) -> RouterPrediction:
        logits = list(self.bias)
        for col, value in vectorize(text, self.vocab, self.idf, self.feature_set).items():
            row = self.weights[col]
            for k in range(len(logits)):
                logits[k] += value * row[k]
        probs = softmax(logits)
        best = max(range(len(probs)), key=probs.__getitem__)
        return RouterPrediction(
            label=self.labels[best],
            confidence=probs[best],
            scores=dict(zip(self.labels, probs, strict=True)),
            abstained=probs[best] < self.threshold,
        )


def parse_model(raw: dict) -> RouterModel:
    labels = tuple(raw["labels"])
    if set(labels) != set(LABELS):
        raise ValueError(f"router model labels {labels} do not match the contract {LABELS}")
    vocab = {str(t): int(i) for t, i in raw["vocab"].items()}
    weights = tuple(tuple(float(w) for w in row) for row in raw["weights"])
    if len(weights) != len(vocab) or len(raw["idf"]) != len(vocab) or len(raw["bias"]) != len(labels):
        raise ValueError("router model is inconsistent: vocab, idf, weights and bias sizes differ")
    return RouterModel(
        version=str(raw["version"]),
        report_id=str(raw["report_id"]),
        labels=labels,
        feature_set=str(raw["feature_set"]),
        vocab=vocab,
        idf=tuple(float(v) for v in raw["idf"]),
        weights=weights,
        bias=tuple(float(b) for b in raw["bias"]),
        threshold=float(raw["threshold"]),
    )


@lru_cache
def load_model(path: Path = DEFAULT_MODEL_PATH) -> RouterModel:
    return parse_model(json.loads(path.read_text(encoding="utf-8")))


def classify(text: str) -> RouterPrediction:
    """Classify one customer message with the committed model (loaded once per process)."""
    return load_model().predict(text)
