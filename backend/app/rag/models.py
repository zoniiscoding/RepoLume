"""Typed internal retrieval, evidence, and answer models."""

import uuid
from dataclasses import dataclass
from enum import StrEnum


class Answerability(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNSUPPORTED_QUESTION = "unsupported_question"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"


class AnswerUncertainty(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class NormalizedQuestion:
    text: str
    fingerprint: str
    estimated_tokens: int


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    score: float
    file_path: str
    language: str
    chunk_type: str
    symbol_name: str | None
    qualified_symbol_name: str | None
    start_line: int
    end_line: int
    stable_chunk_hash: str
    content: str


@dataclass(frozen=True, slots=True)
class Citation:
    evidence_id: str
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str | None
    qualified_symbol_name: str | None
    chunk_type: str
    commit_sha: str
    supporting_excerpt: str


@dataclass(frozen=True, slots=True)
class GroundedAnswer:
    repository_id: uuid.UUID
    answer: str
    answerability: Answerability
    uncertainty: AnswerUncertainty
    citations: tuple[Citation, ...]
    commit_sha: str | None
    index_version: int
    retrieved_evidence_count: int
