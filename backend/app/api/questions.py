"""Authenticated grounded repository question endpoint."""

import uuid
from typing import cast

from fastapi import APIRouter, Request, status

from app.auth.dependencies import CurrentUser
from app.core.config import Settings
from app.core.errors import APIError
from app.db.session import Database
from app.embeddings.client import EmbeddingProviderProtocol
from app.github.client import GitHubClientProtocol
from app.llm.client import LLMProviderProtocol
from app.rag.query import QuestionValidationError
from app.rag.service import QuestionService
from app.schemas.errors import ErrorCode
from app.schemas.questions import (
    RepositoryCitationResponse,
    RepositoryQuestionRequest,
    RepositoryQuestionResponse,
)
from app.services.installations import InstallationAccessError, InstallationService
from app.vector.qdrant import VectorStoreProtocol

router = APIRouter()


def _service(request: Request) -> QuestionService:
    database = cast(Database, request.app.state.database)
    settings = cast(Settings, request.app.state.settings)
    installations = InstallationService(
        database=database,
        github=cast(GitHubClientProtocol, request.app.state.github_client),
        settings=settings,
    )
    return QuestionService(
        database=database,
        installations=installations,
        embeddings=cast(EmbeddingProviderProtocol, request.app.state.embedding_provider),
        vectors=cast(VectorStoreProtocol, request.app.state.vector_store),
        llm=cast(LLMProviderProtocol, request.app.state.llm_provider),
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
    return RepositoryQuestionResponse(
        repository_id=result.repository_id,
        answer=result.answer,
        answerability=result.answerability,
        uncertainty=result.uncertainty,
        citations=[
            RepositoryCitationResponse(
                evidence_id=item.evidence_id,
                file_path=item.file_path,
                start_line=item.start_line,
                end_line=item.end_line,
                symbol_name=item.symbol_name,
                qualified_symbol_name=item.qualified_symbol_name,
                chunk_type=item.chunk_type,
                commit_sha=item.commit_sha,
                supporting_excerpt=item.supporting_excerpt,
            )
            for item in result.citations
        ],
        indexed_commit_sha=result.commit_sha,
        active_index_version=result.index_version,
        retrieved_evidence_count=result.retrieved_evidence_count,
    )
