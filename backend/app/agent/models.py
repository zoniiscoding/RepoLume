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
    FIND_CALLERS = "find_callers"


class AgentToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, min_length=1, max_length=4096)
    symbol_name: str | None = Field(default=None, min_length=1, max_length=1024)
    file_path: str | None = Field(default=None, min_length=1, max_length=1024)


class SearchCodeArguments(AgentToolArguments):
    @model_validator(mode="after")
    def validate_search(self) -> "SearchCodeArguments":
        if self.query is None or self.symbol_name is not None or self.file_path is not None:
            raise ValueError("invalid_search_code_arguments")
        return self


class GetHistoryArguments(AgentToolArguments):
    @model_validator(mode="after")
    def validate_history(self) -> "GetHistoryArguments":
        if self.query is None or self.symbol_name is not None or self.file_path is not None:
            raise ValueError("invalid_get_history_arguments")
        return self


class FindCallersArguments(AgentToolArguments):
    @model_validator(mode="after")
    def validate_callers(self) -> "FindCallersArguments":
        if self.query is not None or self.symbol_name is None:
            raise ValueError("invalid_find_callers_arguments")
        if self.file_path is not None and (
            self.file_path.startswith("/") or ".." in self.file_path.split("/")
        ):
            raise ValueError("invalid_find_callers_arguments")
        return self


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


@dataclass(frozen=True, slots=True)
class CallerEvidence:
    evidence_id: str
    target_symbol_name: str
    target_qualified_name: str
    target_file_path: str
    caller_symbol_name: str
    caller_qualified_name: str
    caller_file_path: str
    caller_start_line: int
    caller_end_line: int
    call_line: int
    call_end_line: int
    call_expression: str
    resolution_type: str
    confidence: str
    commit_sha: str
    index_version: int
    limitation: str = "Static Python analysis; runtime-dispatched calls may be absent."


AgentEvidence = Evidence | CommitEvidence | PullRequestEvidence | CallerEvidence


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
