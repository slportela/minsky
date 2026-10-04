"""Reply-language recognition. The turn handler depends only on LanguageDetector.detect.

Confirmation of a pending action is a model decision (agent.confirm), not a word list here.
"""

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
