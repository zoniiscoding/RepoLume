"""Grounded repository-question API contracts."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.rag.models import Answerability, AnswerUncertainty


class RepositoryQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=16_384)


class RepositoryCodeCitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["code"] = "code"
    evidence_id: str
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str | None
    qualified_symbol_name: str | None
    chunk_type: str
    commit_sha: str
    supporting_excerpt: str


class RepositoryCommitCitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["commit"] = "commit"
    evidence_id: str
    commit_sha: str
    message: str
    committed_at: str
    author_login: str | None
    parent_shas: list[str]
    changed_paths: list[str]
    patch_excerpt: str | None
    html_url: str


class RepositoryPullRequestCitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["pull_request"] = "pull_request"
    evidence_id: str
    number: int
    title: str
    state: str
    author_login: str | None
    merged_at: str | None
    merge_commit_sha: str | None
    changed_paths: list[str]
    body_excerpt: str | None
    html_url: str


RepositoryCitationResponse = Annotated[
    RepositoryCodeCitationResponse
    | RepositoryCommitCitationResponse
    | RepositoryPullRequestCitationResponse,
    Field(discriminator="source_type"),
]


class AgentTraceStepResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: int
    tool: str
    argument_fingerprint: str
    status: str
    duration_ms: int
    result_count: int
    failure_code: str | None
    contributed_evidence: bool


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
    tool_call_count: int
    duration_ms: int
    trace: list[AgentTraceStepResponse]
