"""Strict provider, tool, evidence, and trace contracts for the direct agent loop."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.rag.models import Answerability, AnswerUncertainty, Evidence


class AgentToolName(StrEnum):
    SEARCH_CODE = "search_code"
    GET_HISTORY = "get_history"


class AgentToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4096)


class SearchCodeArguments(AgentToolArguments):
    pass


class GetHistoryArguments(AgentToolArguments):
    pass


class AgentDecision(BaseModel):
    """One strict provider decision; repository scope is intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["tool", "final"]
    tool_name: AgentToolName | None = None
    arguments: AgentToolArguments | None = None
    answer: str | None = Field(default=None, max_length=32_000)
    answerability: Answerability | None = None
    uncertainty: AnswerUncertainty | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_action_fields(self) -> "AgentDecision":
        if self.action == "tool":
            if (
                self.tool_name is None
                or self.arguments is None
                or self.answer is not None
                or self.answerability is not None
                or self.uncertainty is not None
                or self.evidence_ids
            ):
                raise ValueError("invalid_tool_decision")
        elif (
            self.tool_name is not None
            or self.arguments is not None
            or self.answer is None
            or self.answerability is None
            or self.uncertainty is None
        ):
            raise ValueError("invalid_final_decision")
        return self


class AgentGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: str = Field(min_length=1)
    context_payload: str = Field(min_length=1)


class AgentProviderProtocol(Protocol):
    async def decide(self, request: AgentGenerationRequest) -> AgentDecision: ...


@dataclass(frozen=True, slots=True)
class CommitEvidence:
    evidence_id: str
    commit_sha: str
    message: str
    committed_at: datetime
    author_login: str | None
    parent_shas: tuple[str, ...]
    changed_paths: tuple[str, ...]
    patch_excerpt: str | None
    html_url: str


@dataclass(frozen=True, slots=True)
class PullRequestEvidence:
    evidence_id: str
    number: int
    title: str
    state: str
    author_login: str | None
    merged_at: datetime | None
    merge_commit_sha: str | None
    changed_paths: tuple[str, ...]
    body_excerpt: str | None
    html_url: str


AgentEvidence = Evidence | CommitEvidence | PullRequestEvidence


class AgentStepStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class AgentTraceStep:
    step: int
    tool: AgentToolName
    argument_fingerprint: str
    status: AgentStepStatus
    duration_ms: int
    result_count: int
    failure_code: str | None
    contributed_evidence: bool


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    repository_id: uuid.UUID
    answer: str
    answerability: Answerability
    uncertainty: AnswerUncertainty
    evidence: tuple[AgentEvidence, ...]
    cited_evidence_ids: tuple[str, ...]
    commit_sha: str | None
    index_version: int
    retrieved_evidence_count: int
    trace: tuple[AgentTraceStep, ...]
    duration_ms: int
