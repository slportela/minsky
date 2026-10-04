"""Reply-language recognition. The turn handler depends only on LanguageDetector.detect.

Consent for a pending write is explicit_yes / explicit_no in agent.consent, not here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

from lingua import Language, LanguageDetectorBuilder


class LanguageDetector(Protocol):
    """Language code for a customer message. Callers store it; they do not name codes."""

    def detect(self, text: str) -> str: ...


# Below this, lingua is guessing. "D09-eligible" alone scores as Spanish; a sentence
# such as "necesito el comercio y el monto" scores about 0.82.
_CONFIDENT = 0.8
# A rule id is one long token. A customer sentence has several.
_PROSE_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_MIN_PROSE_WORDS = 3


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

    def recognize(self, text: str) -> str | None:
        """Language code when the text is a sentence lingua is confident about.

        Identifier lists and a lone rule id return None. They are not a language sample.
        """
        words = [word for word in _PROSE_WORD.findall(text) if len(word) >= 4]
        if len(words) < _MIN_PROSE_WORDS:
            return None
        values = self._detector.compute_language_confidence_values(text)
        if not values:
            return None
        top = values[0]
        code = top.language.iso_code_639_1
        if top.value < _CONFIDENT or code is None:
            return None
        return code.name.casefold()


@lru_cache(maxsize=1)
def default_language_detector() -> LinguaLanguageDetector:
    """One shared Spanish/Portuguese detector. Lingua loads each model once."""
    return LinguaLanguageDetector()
