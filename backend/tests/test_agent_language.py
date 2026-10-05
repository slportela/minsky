"""Language detector strategy: lingua now, any later class with the same method."""

from lingua import Language

from minsky_api.agent.language import LinguaLanguageDetector


def test_lingua_returns_pt_or_default_es():
    detector = LinguaLanguageDetector()
    assert detector.detect("Quero disputar uma cobrança de 25 USD") == "pt"
    assert detector.detect("Obrigado, preciso de ajuda com um débito") == "pt"
    assert detector.detect("Quiero disputar un cargo de 25 USD") == "es"
    assert detector.detect("Las voces del comercio no coinciden con el cargo") == "es"
    assert detector.detect("El vocero confirmó el cargo de ayer") == "es"


def test_undecided_text_stays_spanish():
    detector = LinguaLanguageDetector()
    assert detector.detect("123 456") == "es"


def test_third_language_is_another_lingua_language():
    detector = LinguaLanguageDetector(languages=(Language.SPANISH, Language.FRENCH), default="es")
    assert detector.detect("Bonjour, je voudrais contester cette opération") == "fr"
    assert detector.detect("hola") == "es"
