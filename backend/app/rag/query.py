"""Central Unicode-safe question preprocessing and scope classification."""

import hashlib
import re
import unicodedata

from app.core.config import Settings
from app.rag.models import NormalizedQuestion

_TOKEN_PATTERN = re.compile(r"[\w]+|[^\w\s]", flags=re.UNICODE)
_UNSUPPORTED_PATTERNS = (
    re.compile(
        r"\b(?:git\s+history|commit\s+history|pull\s+request|who\s+changed|why\s+was)\b", re.I
    ),
    re.compile(r"\b(?:find\s+(?:all\s+)?callers|what\s+calls|who\s+calls|call\s+graph)\b", re.I),
    re.compile(
        r"\b(?:runtime|current\s+production|production\s+(?:logs?|traffic|state)|current\s+(?:database|network|memory|cpu))\b",
        re.I,
    ),
    re.compile(
        r"\b(?:latest|today|current\s+(?:weather|price)|external\s+(?:service|system)|internet)\b",
        re.I,
    ),
    re.compile(r"\b(?:commit\s+[0-9a-f]{7,40}|unindexed\s+commit|different\s+commit)\b", re.I),
)


class QuestionValidationError(ValueError):
    """Question failed a safe public input constraint."""


class QuestionPreprocessor:
    def __init__(self, settings: Settings) -> None:
        self._min_characters = settings.rag_question_min_characters
        self._max_bytes = settings.rag_question_max_bytes
        self._max_tokens = settings.rag_question_max_tokens
        self._version = settings.llm_prompt_version

    def prepare(self, raw: str) -> NormalizedQuestion:
        if any(
            unicodedata.category(character) == "Cc" and not character.isspace() for character in raw
        ):
            raise QuestionValidationError("question_control_character")
        normalized = unicodedata.normalize("NFC", raw)
        normalized = " ".join(normalized.split())
        if not normalized:
            raise QuestionValidationError("question_empty")
        if len(normalized) < self._min_characters:
            raise QuestionValidationError("question_too_short")
        if len(normalized.encode("utf-8")) > self._max_bytes:
            raise QuestionValidationError("question_too_large")
        estimated_tokens = len(_TOKEN_PATTERN.findall(normalized))
        if estimated_tokens > self._max_tokens:
            raise QuestionValidationError("question_too_many_tokens")
        fingerprint = hashlib.sha256(f"{self._version}\n{normalized}".encode()).hexdigest()
        return NormalizedQuestion(normalized, fingerprint, estimated_tokens)

    @staticmethod
    def is_unsupported(question: NormalizedQuestion) -> bool:
        return any(pattern.search(question.text) is not None for pattern in _UNSUPPORTED_PATTERNS)
