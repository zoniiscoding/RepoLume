"""Authenticated grounded repository question endpoint."""

import uuid
from typing import cast

from fastapi import APIRouter, Request, status

from app.agent.models import CallerEvidence, CommitEvidence, PullRequestEvidence
from app.agent.provider import resolve_agent_provider
from app.agent.service import AgentQuestionService
from app.agent.tools import AgentToolRegistry, FindCallersTool, GetHistoryTool, SearchCodeTool
from app.auth.dependencies import CurrentUser
from app.core.config import Settings
from app.core.errors import APIError
from app.db.session import Database
from app.embeddings.client import EmbeddingProviderProtocol
from app.github.client import GitHubClientProtocol, GitHubHistoryClientProtocol
from app.llm.client import LLMProviderProtocol
from app.rag.models import Evidence
from app.rag.query import QuestionValidationError
from app.schemas.errors import ErrorCode
from app.schemas.questions import (
    AgentTraceStepResponse,
    RepositoryCallerCitationResponse,
    RepositoryCitationResponse,
    RepositoryCodeCitationResponse,
    RepositoryCommitCitationResponse,
    RepositoryPullRequestCitationResponse,
    RepositoryQuestionRequest,
    RepositoryQuestionResponse,
)
from app.services.installations import InstallationAccessError, InstallationService
from app.vector.qdrant import VectorStoreProtocol

router = APIRouter()


def _service(request: Request) -> AgentQuestionService:
    database = cast(Database, request.app.state.database)
    settings = cast(Settings, request.app.state.settings)
    installations = InstallationService(
        database=database,
        github=cast(GitHubClientProtocol, request.app.state.github_client),
        settings=settings,
    )
    embeddings = cast(EmbeddingProviderProtocol, request.app.state.embedding_provider)
    vectors = cast(VectorStoreProtocol, request.app.state.vector_store)
    history_github = cast(GitHubHistoryClientProtocol, request.app.state.github_client)
    return AgentQuestionService(
        database=database,
        installations=installations,
        provider=resolve_agent_provider(cast(LLMProviderProtocol, request.app.state.llm_provider)),
        registry=AgentToolRegistry(
            (
                SearchCodeTool(embeddings=embeddings, vectors=vectors, settings=settings),
                GetHistoryTool(
                    installations=installations,
                    github=history_github,
                    settings=settings,
                ),
                FindCallersTool(
                    database=database,
                    installations=installations,
                    settings=settings,
                ),
            )
        ),
        settings=settings,
    )


@router.post("/{repository_id}/questions")
async def ask_repository_question(
    repository_id: uuid.UUID,
    payload: RepositoryQuestionRequest,
    request: Request,
    user: CurrentUser,
) -> RepositoryQuestionResponse:
    service = _service(request)
    try:
        question = service.prepare_question(payload.question)
        result = await service.answer(
            user_id=user.id,
            repository_id=repository_id,
            question=question,
        )
    except QuestionValidationError as error:
        raise APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.VALIDATION_ERROR,
            message="Question is invalid",
        ) from error
    except InstallationAccessError as error:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message="Repository was not found",
        ) from error
    by_id = {item.evidence_id: item for item in result.evidence}
    citations: list[RepositoryCitationResponse] = []
    for evidence_id in result.cited_evidence_ids:
        item = by_id[evidence_id]
        if isinstance(item, Evidence):
            citations.append(
                RepositoryCodeCitationResponse(
                    evidence_id=item.evidence_id,
                    file_path=item.file_path,
                    start_line=item.start_line,
                    end_line=item.end_line,
                    symbol_name=item.symbol_name,
                    qualified_symbol_name=item.qualified_symbol_name,
                    chunk_type=item.chunk_type,
                    commit_sha=result.commit_sha or "",
                    supporting_excerpt=item.content,
                )
            )
        elif isinstance(item, CommitEvidence):
            citations.append(
                RepositoryCommitCitationResponse(
                    evidence_id=item.evidence_id,
                    commit_sha=item.commit_sha,
                    message=item.message,
                    committed_at=item.committed_at.isoformat(),
                    author_login=item.author_login,
                    parent_shas=list(item.parent_shas),
                    changed_paths=list(item.changed_paths),
                    patch_excerpt=item.patch_excerpt,
                    html_url=item.html_url,
                )
            )
        elif isinstance(item, CallerEvidence):
            citations.append(
                RepositoryCallerCitationResponse(
                    evidence_id=item.evidence_id,
                    target_symbol_name=item.target_symbol_name,
                    target_qualified_name=item.target_qualified_name,
                    target_file_path=item.target_file_path,
                    caller_symbol_name=item.caller_symbol_name,
                    caller_qualified_name=item.caller_qualified_name,
                    caller_file_path=item.caller_file_path,
                    caller_start_line=item.caller_start_line,
                    caller_end_line=item.caller_end_line,
                    call_line=item.call_line,
                    call_end_line=item.call_end_line,
                    call_expression=item.call_expression,
                    resolution_type=item.resolution_type,
                    confidence=item.confidence,
                    commit_sha=item.commit_sha,
                    index_version=item.index_version,
                    limitation=item.limitation,
                )
            )
        elif isinstance(item, PullRequestEvidence):
            citations.append(
                RepositoryPullRequestCitationResponse(
                    evidence_id=item.evidence_id,
                    number=item.number,
                    title=item.title,
                    state=item.state,
                    author_login=item.author_login,
                    merged_at=item.merged_at.isoformat() if item.merged_at else None,
                    merge_commit_sha=item.merge_commit_sha,
                    changed_paths=list(item.changed_paths),
                    body_excerpt=item.body_excerpt,
                    html_url=item.html_url,
                )
            )
    return RepositoryQuestionResponse(
        repository_id=result.repository_id,
        answer=result.answer,
        answerability=result.answerability,
        uncertainty=result.uncertainty,
        citations=citations,
        indexed_commit_sha=result.commit_sha,
        active_index_version=result.index_version,
        retrieved_evidence_count=result.retrieved_evidence_count,
        tool_call_count=len(result.trace),
        duration_ms=result.duration_ms,
        trace=[
            AgentTraceStepResponse(
                step=item.step,
                tool=item.tool.value,
                argument_fingerprint=item.argument_fingerprint,
                status=item.status.value,
                duration_ms=item.duration_ms,
                result_count=item.result_count,
                failure_code=item.failure_code,
                contributed_evidence=item.contributed_evidence,
            )
            for item in result.trace
        ],
    )
