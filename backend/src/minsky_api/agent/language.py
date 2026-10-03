"""Reply-language recognition. The turn handler depends only on LanguageDetector.detect."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

from lingua import Language, LanguageDetectorBuilder


class LanguageDetector(Protocol):
    """Language code for a customer message. Callers store it; they do not name codes."""

    def detect(self, text: str) -> str: ...


class LinguaLanguageDetector:
    """Offline detection with lingua. Another reply language is another Language in the builder."""

    def __init__(self, languages: Sequence[Language] | None = None, *, default: str = "es") -> None:
        chosen = tuple(languages) if languages is not None else (Language.SPANISH, Language.PORTUGUESE)
        self._detector = LanguageDetectorBuilder.from_languages(*chosen).with_preloaded_language_models().build()
        self._default = default

    def detect(self, text: str) -> str:
        found = self._detector.detect_language_of(text)
        if found is None or found.iso_code_639_1 is None:
            return self._default
        return found.iso_code_639_1.name.casefold()


@lru_cache(maxsize=1)
def default_language_detector() -> LinguaLanguageDetector:
    """One shared Spanish/Portuguese detector. Lingua loads each model once."""
    return LinguaLanguageDetector()


# Confirmation is an exact token for the stored language, not another detection pass.
_YES = {
    "es": ("sí", "si", "yes", "ok", "vale", "confirmo", "confirma"),
    "pt": ("sim", "yes", "ok", "confirmo", "confirma"),
}
_NO = {
    "es": ("no", "n", "cancelo", "cancela", "cancel", "negativo"),
    "pt": ("não", "nao", "no", "n", "cancelo", "cancela", "cancel", "negativo"),
}


def _token(text: str) -> str:
    return text.strip().rstrip(".!?").strip().casefold()


def _words(language: str, table: dict[str, tuple[str, ...]]) -> frozenset[str]:
    try:
        words = table[language]
    except KeyError as error:
        raise KeyError(f"no confirmation words for language {language!r}") from error
    return frozenset(word.casefold() for word in words)


def is_yes(text: str, language: str) -> bool:
    return _token(text) in _words(language, _YES)


def is_no(text: str, language: str) -> bool:
    return _token(text) in _words(language, _NO)


def is_any_yes(text: str) -> bool:
    """True when the text is a yes-word in any catalog language. Used by graders."""
    token = _token(text)
    return any(token in _words(language, _YES) for language in _YES)
