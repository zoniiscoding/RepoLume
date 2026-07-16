"""Grounded repository-question API contracts."""

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.rag.models import Answerability, AnswerUncertainty


class RepositoryQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=16_384)


class RepositoryCitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str | None
    qualified_symbol_name: str | None
    chunk_type: str
    commit_sha: str
    supporting_excerpt: str


class RepositoryQuestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: uuid.UUID
    answer: str
    answerability: Answerability
    uncertainty: AnswerUncertainty
    citations: list[RepositoryCitationResponse]
    indexed_commit_sha: str | None
    active_index_version: int
    retrieved_evidence_count: int
