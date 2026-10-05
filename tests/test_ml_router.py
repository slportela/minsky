"""Router training data and featurizer parity: deterministic generation, disjoint families, numpy == serving."""

import json

import numpy as np
import pytest

from minsky_api.router.classifier import load_model
from minsky_api.router.features import normalize
from ml.router.generate import DATA_DIR, GENERATOR_VERSION, MERCHANTS, TEMPLATES, generate_all, split_family_a
from ml.router.train import featurize, keyword_label, predict_proba


@pytest.fixture(scope="module")
def generated():
    return generate_all()


def _load(split: str) -> list[dict]:
    with (DATA_DIR / f"{split}.jsonl").open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_generation_is_deterministic_and_matches_committed_files(generated):
    again = generate_all()
    assert generated == again
    for split, rows in generated.items():
        committed = _load(split)
        assert [r["text"] for r in committed] == [r.text for r in rows]
        assert {r["generator_version"] for r in committed} == {GENERATOR_VERSION}
        assert {r["provenance"] for r in committed} == {"generated"}


def test_families_share_no_template_or_merchant():
    assert not set(MERCHANTS["A"]) & set(MERCHANTS["B"])
    for lang in ("es", "pt"):
        for label, templates in TEMPLATES["A"][lang].items():
            assert not set(templates) & set(TEMPLATES["B"][lang][label])


def test_test_unseen_uses_only_family_b_and_val_holds_out_whole_templates():
    train, val, test = _load("train"), _load("val"), _load("test_unseen")
    assert {r["template_id"][0] for r in train + val} == {"A"}
    assert {r["template_id"][0] for r in test} == {"B"}
    assert not {r["template_id"] for r in train} & {r["template_id"] for r in val}
    train_ids, val_ids = split_family_a()
    assert {r["template_id"] for r in val} <= val_ids and {r["template_id"] for r in train} <= train_ids
    assert not {r["text"] for r in test} & {r["text"] for r in train + val}


def test_family_b_merchants_never_reach_training_data():
    b_names = [normalize(m) for m in MERCHANTS["B"]]
    a_names = [normalize(m) for m in MERCHANTS["A"]]
    for row in _load("train") + _load("val"):
        text = normalize(row["text"])
        assert not any(name in text for name in b_names), row["text"]
    for row in _load("test_unseen"):
        text = normalize(row["text"])
        assert not any(name in text for name in a_names), row["text"]


def test_numpy_training_side_matches_pure_python_serving():
    model = load_model()
    texts = [r["text"] for r in _load("val")[:50]] + ["hola", "no reconozco esto", "cobrança duplicada"]
    vocab = model.vocab
    x = featurize(texts, vocab, model.idf, model.feature_set)
    probs = predict_proba(x, np.array(model.weights), np.array(model.bias))
    for text, row in zip(texts, probs, strict=True):
        served = model.predict(text)
        assert [served.scores[label] for label in model.labels] == pytest.approx(row.tolist(), abs=1e-9)


def test_keyword_baseline_rules():
    assert keyword_label("me cobraron DOS VECES") == "duplicate"
    assert keyword_label("não reconheço") == "not_me"
    assert keyword_label("hola") == "out_of_scope"
