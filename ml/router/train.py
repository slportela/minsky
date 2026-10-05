"""Train and evaluate the router ladder: majority class, keyword rules, TF-IDF + logistic regression.

Every rung is scored on the same splits. Hyper-parameters and the abstain threshold are chosen on val only;
`test_unseen` (held-out template family) and the handwritten draft set are only ever scored. Every run writes a
tracking report (ADR 0007) to ml/reports/<run_id>.{json,md} and the serving artifact to the backend.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from evals.metrics import percentile, wilson_interval
from minsky_api.router.classifier import LABELS, parse_model
from minsky_api.router.features import FEATURE_SETS, fit_vocabulary, normalize, vectorize
from ml.router.generate import DATA_DIR, GENERATOR_VERSION

ROOT = Path(__file__).resolve().parents[2]
HANDWRITTEN = Path(__file__).with_name("handwritten.jsonl")
REPORTS_DIR = ROOT / "ml" / "reports"
MODEL_PATH = ROOT / "backend" / "src" / "minsky_api" / "router" / "model.json"
MODEL_VERSION = "router-tfidf-lr-1"

L2_GRID = (1e-4, 1e-3, 1e-2)
MIN_DF = 2
MAX_FEATURES = 20000
ITERATIONS = 400
LEARNING_RATE = 2.0  # features are L2-normalized, so the loss is 0.5-smooth and 1/L = 2 is a safe step
TARGET_PRECISION = 0.95
WEIGHT_DECIMALS = 6
PARITY_SAMPLES = 20

# Keyword rules, checked in this order on the accent-folded lowercase text; no match means out_of_scope.
KEYWORDS: dict[str, tuple[str, ...]] = {
    "duplicate": ("dos veces", "duas vezes", "duplicad", "doble", "dobro", "repetid", "duplicidade"),
    "wrong_amount": ("de mas", "a mais", "monto", "importe", "valor", "diferente", "incorrect", "errad", "equivocad"),
    "not_received": ("no llego", "nao chegou", "no recib", "nao receb", "reembolso", "estorno", "devolu", "entreg"),
    "not_me": ("no reconozco", "nao reconheco", "no fui yo", "nao fui eu", "fraude", "no hice", "nao fiz", "clon"),
}


@dataclass(frozen=True)
class Row:
    text: str
    label: str
    lang: str


def load(path: Path) -> list[Row]:
    with path.open(encoding="utf-8") as f:
        return [Row(r["text"], r["label"], r["lang"]) for r in map(json.loads, f)]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---- Rungs ----------------------------------------------------------------------------------------------------------


def keyword_label(text: str) -> str:
    norm = normalize(text)
    for label, words in KEYWORDS.items():
        if any(w in norm for w in words):
            return label
    return "out_of_scope"


@dataclass
class Csr:
    """Minimal CSR matrix: enough for X @ W and X.T @ G without scipy."""

    data: np.ndarray
    cols: np.ndarray
    rows: np.ndarray  # row index of every stored value
    n_rows: int
    n_cols: int

    def dot(self, w: np.ndarray) -> np.ndarray:
        contrib = self.data[:, None] * w[self.cols]
        out = np.zeros((self.n_rows, w.shape[1]))
        for k in range(w.shape[1]):
            out[:, k] = np.bincount(self.rows, weights=contrib[:, k], minlength=self.n_rows)
        return out

    def tdot(self, g: np.ndarray) -> np.ndarray:
        contrib = self.data[:, None] * g[self.rows]
        out = np.zeros((self.n_cols, g.shape[1]))
        for k in range(g.shape[1]):
            out[:, k] = np.bincount(self.cols, weights=contrib[:, k], minlength=self.n_cols)
        return out


def featurize(texts: Sequence[str], vocab: dict[str, int], idf: Sequence[float], feature_set: str) -> Csr:
    data: list[float] = []
    cols: list[int] = []
    rows: list[int] = []
    for i, text in enumerate(texts):
        for col, value in sorted(vectorize(text, vocab, idf, feature_set).items()):
            data.append(value)
            cols.append(col)
            rows.append(i)
    return Csr(np.array(data), np.array(cols, dtype=np.int64), np.array(rows, dtype=np.int64), len(texts), len(vocab))


def softmax_rows(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def fit_logreg(x: Csr, y: np.ndarray, l2: float) -> tuple[np.ndarray, np.ndarray]:
    """Multinomial logistic regression, mean cross-entropy + l2/2 ||W||^2, Nesterov accelerated gradient from zero."""
    k = len(LABELS)
    onehot = np.eye(k)[y]
    w = np.zeros((x.n_cols, k))
    b = np.zeros(k)
    w_prev, b_prev = w.copy(), b.copy()
    for t in range(1, ITERATIONS + 1):
        momentum = (t - 1) / (t + 2)
        w_look = w + momentum * (w - w_prev)
        b_look = b + momentum * (b - b_prev)
        g = (softmax_rows(x.dot(w_look) + b_look) - onehot) / x.n_rows
        w_prev, b_prev = w, b
        w = w_look - LEARNING_RATE * (x.tdot(g) + l2 * w_look)
        b = b_look - LEARNING_RATE * g.sum(axis=0)
    return w, b


def predict_proba(x: Csr, w: np.ndarray, b: np.ndarray) -> np.ndarray:
    return softmax_rows(x.dot(w) + b)


# ---- Metrics --------------------------------------------------------------------------------------------------------


def classification_metrics(gold: Sequence[str], pred: Sequence[str], langs: Sequence[str]) -> dict:
    n = len(gold)
    correct = sum(g == p for g, p in zip(gold, pred, strict=True))
    per_class = {}
    for label in LABELS:
        tp = sum(g == label and p == label for g, p in zip(gold, pred, strict=True))
        n_pred = sum(p == label for p in pred)
        n_gold = sum(g == label for g in gold)
        precision = tp / n_pred if n_pred else 0.0
        recall = tp / n_gold if n_gold else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"precision": precision, "recall": recall, "f1": f1, "support": n_gold}
    by_lang = {}
    for lang in sorted(set(langs)):
        idx = [i for i, lg in enumerate(langs) if lg == lang]
        sub_gold = [gold[i] for i in idx]
        sub_pred = [pred[i] for i in idx]
        hits = sum(g == p for g, p in zip(sub_gold, sub_pred, strict=True))
        by_lang[lang] = {
            "n": len(idx),
            "accuracy": hits / len(idx),
            "accuracy_ci95": wilson_interval(hits, len(idx)),
            "macro_f1": macro_f1(sub_gold, sub_pred),
        }
    return {
        "n": n,
        "accuracy": correct / n,
        "accuracy_ci95": wilson_interval(correct, n),
        "macro_f1": sum(c["f1"] for c in per_class.values()) / len(LABELS),
        "per_class": per_class,
        "confusion": {
            g: {p: sum(a == g and b == p for a, b in zip(gold, pred, strict=True)) for p in LABELS} for g in LABELS
        },
        "by_lang": by_lang,
    }


def macro_f1(gold: Sequence[str], pred: Sequence[str]) -> float:
    total = 0.0
    for label in LABELS:
        tp = sum(g == label and p == label for g, p in zip(gold, pred, strict=True))
        n_pred = sum(p == label for p in pred)
        n_gold = sum(g == label for g in gold)
        if tp:
            precision, recall = tp / n_pred, tp / n_gold
            total += 2 * precision * recall / (precision + recall)
    return total / len(LABELS)


def selective_metrics(gold: Sequence[str], pred: Sequence[str], conf: Sequence[float], threshold: float) -> dict:
    kept = [i for i, c in enumerate(conf) if c >= threshold]
    hits = sum(gold[i] == pred[i] for i in kept)
    return {
        "threshold": threshold,
        "coverage": len(kept) / len(gold),
        "n_routed": len(kept),
        "precision_routed": hits / len(kept) if kept else None,
        "precision_routed_ci95": wilson_interval(hits, len(kept)),
    }


def choose_threshold(gold: Sequence[str], pred: Sequence[str], conf: Sequence[float]) -> float:
    """Lowest confidence threshold whose auto-routed predictions on val reach TARGET_PRECISION (max coverage)."""
    for t in sorted(set(conf)):
        if selective_metrics(gold, pred, conf, t)["precision_routed"] >= TARGET_PRECISION:
            return float(t)
    raise RuntimeError(f"no threshold reaches precision {TARGET_PRECISION} on val; the model must not auto-route")


def latency_ms(fn: Callable[[str], object], texts: Sequence[str]) -> dict:
    times = []
    for text in texts:
        start = time.perf_counter()
        fn(text)
        times.append((time.perf_counter() - start) * 1000)
    return {"p50_ms": percentile(times, 50), "p95_ms": percentile(times, 95)}


# ---- Run ------------------------------------------------------------------------------------------------------------


def git_state() -> dict:
    def run(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()

    return {"sha": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


def model_payload(
    vocab: dict[str, int],
    idf: list[float],
    w: np.ndarray,
    b: np.ndarray,
    feature_set: str,
    threshold: float,
    run_id: str,
) -> dict:
    return {
        "version": MODEL_VERSION,
        "report_id": run_id,
        "labels": list(LABELS),
        "feature_set": feature_set,
        "threshold": threshold,
        "vocab": vocab,
        "idf": [round(v, WEIGHT_DECIMALS) for v in idf],
        "weights": [[round(float(v), WEIGHT_DECIMALS) for v in row] for row in w],
        "bias": [round(float(v), WEIGHT_DECIMALS) for v in b],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--no-artifact", action="store_true", help="write the report only, not the backend model")
    args = parser.parse_args()

    git = git_state()  # before writing anything, so `dirty` describes the inputs
    started = datetime.now(UTC)
    run_id = f"{started:%Y%m%dT%H%M%SZ}-{git['sha'][:7]}"
    paths = {s: DATA_DIR / f"{s}.jsonl" for s in ("train", "val", "test_unseen")} | {"handwritten": HANDWRITTEN}
    splits = {name: load(path) for name, path in paths.items()}
    train = splits["train"]
    y_train = np.array([LABELS.index(r.label) for r in train])

    # Hyper-parameter search on val: feature set x L2 strength, selected by macro-F1.
    search = []
    for feature_set in FEATURE_SETS:
        vocab, idf = fit_vocabulary([r.text for r in train], feature_set, MIN_DF, MAX_FEATURES)
        x_train = featurize([r.text for r in train], vocab, idf, feature_set)
        x_val = featurize([r.text for r in splits["val"]], vocab, idf, feature_set)
        for l2 in L2_GRID:
            w, b = fit_logreg(x_train, y_train, l2)
            pred = [LABELS[i] for i in predict_proba(x_val, w, b).argmax(axis=1)]
            score = macro_f1([r.label for r in splits["val"]], pred)
            search.append({"feature_set": feature_set, "l2": l2, "n_features": len(vocab), "val_macro_f1": score})
            print(f"{feature_set:10s} l2={l2:g} features={len(vocab)} val macro-F1={score:.4f}")
    best = max(search, key=lambda s: (round(s["val_macro_f1"], 6), s["l2"]))
    feature_set, l2 = best["feature_set"], best["l2"]

    vocab, idf = fit_vocabulary([r.text for r in train], feature_set, MIN_DF, MAX_FEATURES)
    w, b = fit_logreg(featurize([r.text for r in train], vocab, idf, feature_set), y_train, l2)
    # Serve exactly what is evaluated: the rounded artifact, scored by the pure-Python classifier.
    payload = model_payload(vocab, idf, w, b, feature_set, threshold=1.0, run_id=run_id)
    val_preds = [parse_model(payload).predict(r.text) for r in splits["val"]]
    threshold = choose_threshold(
        [r.label for r in splits["val"]], [p.label for p in val_preds], [p.confidence for p in val_preds]
    )
    payload["threshold"] = threshold
    model = parse_model(payload)
    rounded_w = np.array(payload["weights"])
    rounded_b = np.array(payload["bias"])
    parity_rows = splits["test_unseen"][:PARITY_SAMPLES]
    parity_probs = predict_proba(
        featurize([r.text for r in parity_rows], vocab, idf, feature_set), rounded_w, rounded_b
    )
    payload["parity_samples"] = [
        {"text": r.text, "scores": [round(float(p), 9) for p in probs]}
        for r, probs in zip(parity_rows, parity_probs, strict=True)
    ]

    majority = Counter(r.label for r in train).most_common(1)[0][0]
    rungs: dict[str, Callable[[str], str]] = {
        "majority": lambda _text: majority,
        "keywords": keyword_label,
        "tfidf_logreg": lambda text: model.predict(text).label,
    }
    results: dict[str, dict] = {name: {} for name in rungs}
    for split in ("val", "test_unseen", "handwritten"):
        rows = splits[split]
        gold, langs, texts = [r.label for r in rows], [r.lang for r in rows], [r.text for r in rows]
        for name, fn in rungs.items():
            metrics = classification_metrics(gold, [fn(t) for t in texts], langs)
            metrics["latency"] = latency_ms(fn, texts)
            results[name][split] = metrics
        preds = [model.predict(t) for t in texts]
        results["tfidf_logreg"][split]["selective"] = selective_metrics(
            gold, [p.label for p in preds], [p.confidence for p in preds], threshold
        )

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    artifact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    artifact_sha = hashlib.sha256(artifact.encode("utf-8")).hexdigest()
    if not args.no_artifact:
        MODEL_PATH.write_text(artifact, encoding="utf-8")
    report = {
        "run_id": run_id,
        "timestamp": started.isoformat(),
        "git": git,
        "generator_version": GENERATOR_VERSION,
        "data": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "n": len(splits[name])}
            for name, path in paths.items()
        },
        "params": {
            "model_version": MODEL_VERSION,
            "feature_set": feature_set,
            "l2": l2,
            "min_df": MIN_DF,
            "max_features": MAX_FEATURES,
            "n_features": len(vocab),
            "iterations": ITERATIONS,
            "learning_rate": LEARNING_RATE,
            "target_precision": TARGET_PRECISION,
            "threshold": threshold,
            "majority_label": majority,
            "keywords": KEYWORDS,
        },
        "search": search,
        "results": results,
        "artifact": {
            "path": str(MODEL_PATH.relative_to(ROOT)),
            "sha256": artifact_sha,
            "written": not args.no_artifact,
        },
    }
    (REPORTS_DIR / f"{run_id}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", "utf-8")
    (REPORTS_DIR / f"{run_id}.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"report: ml/reports/{run_id}.md  threshold={threshold:.4f}  artifact sha256={artifact_sha[:12]}")


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}"


def _ci(ci: Sequence[float] | None) -> str:
    return "n/a" if ci is None else f"[{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]"


def render_markdown(report: dict) -> str:
    p = report["params"]
    lines = [
        f"# Router run {report['run_id']}",
        "",
        f"- Timestamp: {report['timestamp']}; git {report['git']['sha'][:12]} (dirty: {report['git']['dirty']})",
        f"- Generator: {report['generator_version']}; model: {p['model_version']}, features `{p['feature_set']}`, "
        f"L2 {p['l2']:g}, {p['n_features']} features; threshold {p['threshold']:.4f} (val precision target "
        f"{p['target_precision']})",
        f"- Artifact: `{report['artifact']['path']}` sha256 `{report['artifact']['sha256']}`",
        "- Data: generated (train, val, test_unseen) and a team-written draft (handwritten); not real customer text.",
        "",
        "| Data | n | sha256 |",
        "|---|---:|---|",
    ]
    lines += [f"| {name} | {d['n']} | `{d['sha256'][:16]}` |" for name, d in report["data"].items()]
    lines += [
        "",
        "## Ladder (accuracy % with Wilson 95% CI, macro-F1)",
        "",
        "| Rung | Split | n | Accuracy | 95% CI | Macro-F1 | p50 ms | p95 ms |",
        "|---|---|---:|---:|---|---:|---:|---:|",
    ]
    for rung, by_split in report["results"].items():
        for split, m in by_split.items():
            lat = m["latency"]
            lines.append(
                f"| {rung} | {split} | {m['n']} | {_pct(m['accuracy'])} | {_ci(m['accuracy_ci95'])} | "
                f"{m['macro_f1']:.3f} | {lat['p50_ms']:.3f} | {lat['p95_ms']:.3f} |"
            )
    lines += [
        "",
        "## Abstention (tfidf_logreg)",
        "",
        "| Split | n | Coverage % | Routed | Precision of routed % | 95% CI |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for split, m in report["results"]["tfidf_logreg"].items():
        s = m["selective"]
        lines.append(
            f"| {split} | {m['n']} | {_pct(s['coverage'])} | {s['n_routed']} | {_pct(s['precision_routed'])} | "
            f"{_ci(s['precision_routed_ci95'])} |"
        )
    lines += ["", "## By language (accuracy %, n)", "", "| Rung | Split | es | pt |", "|---|---|---|---|"]
    for rung, by_split in report["results"].items():
        for split, m in by_split.items():
            cells = [
                f"{_pct(m['by_lang'][lg]['accuracy'])} (n={m['by_lang'][lg]['n']})" if lg in m["by_lang"] else "n/a"
                for lg in ("es", "pt")
            ]
            lines.append(f"| {rung} | {split} | {cells[0]} | {cells[1]} |")
    lines += ["", "## Per class (tfidf_logreg): precision / recall %", "", "| Split | " + " | ".join(LABELS) + " |"]
    lines.append("|---|" + "---|" * len(LABELS))
    for split, m in report["results"]["tfidf_logreg"].items():
        cells = [f"{_pct(m['per_class'][lb]['precision'])} / {_pct(m['per_class'][lb]['recall'])}" for lb in LABELS]
        lines.append(f"| {split} | " + " | ".join(cells) + " |")
    lines += ["", "## Confusion (tfidf_logreg; rows gold, columns predicted)", ""]
    for split, m in report["results"]["tfidf_logreg"].items():
        lines += [f"**{split}**", "", "| gold \\ pred | " + " | ".join(LABELS) + " |", "|---|" + "---:|" * len(LABELS)]
        lines += [f"| {g} | " + " | ".join(str(m["confusion"][g][pr]) for pr in LABELS) + " |" for g in LABELS]
        lines.append("")
    lines += ["## Search on val", "", "| Features | L2 | n features | val macro-F1 |", "|---|---:|---:|---:|"]
    lines += [
        f"| {s['feature_set']} | {s['l2']:g} | {s['n_features']} | {s['val_macro_f1']:.4f} |" for s in report["search"]
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
