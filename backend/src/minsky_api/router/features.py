"""Text normalization and TF-IDF featurization shared by router training (ml/) and serving.

One module on both sides so that the model is served on exactly the features it was trained on.
Pure Python on purpose: the backend does not depend on numpy.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

FEATURE_SETS = ("word", "char", "word+char")
_TOKEN = re.compile(r"[a-z0-9]+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
CHAR_NGRAMS = (3, 4, 5)


def normalize(text: str) -> str:
    """Lowercase, fold accents (é→e, ç→c, ñ→n) and keep only [a-z0-9] tokens separated by single spaces."""
    folded = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return _NON_ALNUM.sub(" ", stripped).strip()


def terms(text: str, feature_set: str) -> Counter[str]:
    """Raw term counts: word 1-2 grams (`w:`) and/or char 3-5 grams over the padded string (`c:`)."""
    if feature_set not in FEATURE_SETS:
        raise ValueError(f"unknown feature set {feature_set!r}; expected one of {FEATURE_SETS}")
    norm = normalize(text)
    counts: Counter[str] = Counter()
    if feature_set in ("word", "word+char"):
        tokens = _TOKEN.findall(norm)
        counts.update(f"w:{t}" for t in tokens)
        counts.update(f"w:{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False))
    if feature_set in ("char", "word+char"):
        padded = f" {norm} "
        for n in CHAR_NGRAMS:
            counts.update(f"c:{padded[i : i + n]}" for i in range(len(padded) - n + 1))
    return counts


def fit_vocabulary(
    texts: Sequence[str], feature_set: str, min_df: int = 2, max_features: int | None = None
) -> tuple[dict[str, int], list[float]]:
    """Vocabulary (term → column) and smoothed idf, from training texts only.

    Terms are kept if they appear in at least `min_df` documents; the `max_features` most frequent are
    kept, ties broken alphabetically so the result is deterministic.
    """
    df: Counter[str] = Counter()
    for text in texts:
        df.update(terms(text, feature_set).keys())
    kept = sorted((t for t, c in df.items() if c >= min_df), key=lambda t: (-df[t], t))
    if max_features is not None:
        kept = kept[:max_features]
    kept.sort()
    n_docs = len(texts)
    vocab = {t: i for i, t in enumerate(kept)}
    idf = [math.log((1 + n_docs) / (1 + df[t])) + 1.0 for t in kept]
    return vocab, idf


def vectorize(text: str, vocab: Mapping[str, int], idf: Sequence[float], feature_set: str) -> dict[int, float]:
    """Sparse L2-normalized TF-IDF vector with sublinear tf (1 + log count); unknown terms are dropped."""
    vec: dict[int, float] = {}
    for term, count in terms(text, feature_set).items():
        col = vocab.get(term)
        if col is not None:
            vec[col] = (1.0 + math.log(count)) * idf[col]
    norm = math.sqrt(sum(v * v for v in vec.values()))
    if norm > 0:
        vec = {k: v / norm for k, v in vec.items()}
    return vec


def softmax(logits: Iterable[float]) -> list[float]:
    values = list(logits)
    top = max(values)
    exps = [math.exp(v - top) for v in values]
    total = sum(exps)
    return [e / total for e in exps]
