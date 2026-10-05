"""Router serving: the committed artifact loads, predicts what it was trained to, and abstains when unsure."""

import json

import pytest

from minsky_api.router.classifier import DEFAULT_MODEL_PATH, LABELS, RouterPrediction, classify, load_model, parse_model
from minsky_api.router.features import normalize, terms, vectorize


def test_model_loads_once_with_the_label_contract():
    model = load_model()
    assert model is load_model()
    assert model.labels == LABELS
    assert 0.0 < model.threshold < 1.0
    assert model.report_id and model.version


@pytest.mark.parametrize(
    "text,label",
    [
        ("Yo no hice esa compra en Super Ahorro, no la reconozco", "not_me"),
        ("não reconheço essa compra na Uber, eu não fiz", "not_me"),
        ("me cobraron dos veces la misma compra en Cine Premium", "duplicate"),
        ("fui cobrado duas vezes pela mesma compra", "duplicate"),
        ("¿cuál es el saldo de mi cuenta?", "out_of_scope"),
        ("qual é o saldo da minha conta?", "out_of_scope"),
    ],
)
def test_obvious_messages_are_routed(text, label):
    prediction = classify(text)
    assert isinstance(prediction, RouterPrediction)
    assert prediction.label == label
    assert not prediction.abstained
    assert prediction.scores[label] == prediction.confidence
    assert sum(prediction.scores.values()) == pytest.approx(1.0)


def test_predictions_are_deterministic():
    text = "el cargo de Uber está duplicado"
    assert classify(text) == classify(text)


@pytest.mark.parametrize("text", ["zzqx vbnm plok", "", "12345"])
def test_abstains_on_gibberish(text):
    assert classify(text).abstained


def test_featurizer_normalizes_accents_case_and_punctuation():
    assert normalize("¿NO RECONOZCO esta compra en Óptica Visión?") == "no reconozco esta compra en optica vision"
    assert normalize("não reconheço") == "nao reconheco"
    counts = terms("Cobro doble", "word+char")
    assert counts["w:cobro doble"] == 1 and counts["c: co"] == 1


def test_serving_matches_training_side_probabilities():
    # model.json carries probabilities computed by ml/router/train.py (numpy) on the rounded weights.
    raw = json.loads(DEFAULT_MODEL_PATH.read_text(encoding="utf-8"))
    model = load_model()
    assert raw["parity_samples"]
    for sample in raw["parity_samples"]:
        scores = model.predict(sample["text"]).scores
        assert [scores[label] for label in model.labels] == pytest.approx(sample["scores"], abs=1e-6)


def test_vector_is_unit_norm_and_unknown_terms_are_dropped():
    model = load_model()
    vec = vectorize("me cobraron dos veces", model.vocab, model.idf, model.feature_set)
    assert sum(v * v for v in vec.values()) == pytest.approx(1.0)
    assert vectorize("ñññ", {}, [], model.feature_set) == {}


def test_inconsistent_model_is_rejected():
    raw = json.loads(DEFAULT_MODEL_PATH.read_text(encoding="utf-8"))
    raw["labels"] = ["a", "b", "c", "d", "e"]
    with pytest.raises(ValueError):
        parse_model(raw)
