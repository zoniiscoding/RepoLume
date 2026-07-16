"""Authorized single-pass retrieval and grounded answer orchestration."""

import asyncio
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select

from app.core.config import Settings
from app.db.models.enums import IndexBuildState, RepositoryIndexingStatus
from app.db.models.repository import Repository
from app.db.models.repository_index_build import RepositoryIndexBuild
from app.db.session import Database
from app.embeddings.client import EmbeddingProviderProtocol
from app.embeddings.preprocessing import EmbeddingPreprocessor
from app.indexing.failures import IndexingError
from app.llm.client import LLMProviderError, LLMProviderProtocol
from app.rag.evidence import CitationValidator, EvidenceSelector
from app.rag.models import (
    Answerability,
    AnswerUncertainty,
    GroundedAnswer,
    NormalizedQuestion,
)
from app.rag.prompt import GroundedPromptBuilder
from app.rag.query import QuestionPreprocessor
from app.services.installations import InstallationService
from app.vector.qdrant import (
    VectorScope,
    VectorStoreProtocol,
    embedding_model_fingerprint,
)

logger = structlog.get_logger(__name__)

_INSUFFICIENT = "The active index does not contain enough evidence to answer this question."
_UNSUPPORTED = "This question requires information outside Milestone 6 static indexed evidence."
_UNAVAILABLE = "Grounded answering is temporarily unavailable. Please try again."


@dataclass(frozen=True, slots=True)
class ActiveIndex:
    repository_id: uuid.UUID
    installation_id: uuid.UUID
    index_version: int
    commit_sha: str
    preprocessing_fingerprint: str


class QuestionService:
    def __init__(
        self,
        *,
        database: Database,
        installations: InstallationService,
        embeddings: EmbeddingProviderProtocol,
        vectors: VectorStoreProtocol,
        llm: LLMProviderProtocol,
        settings: Settings,
    ) -> None:
        self._database = database
        self._installations = installations
        self._embeddings = embeddings
        self._vectors = vectors
        self._llm = llm
        self._settings = settings
        self._questions = QuestionPreprocessor(settings)
        self._preprocessor = EmbeddingPreprocessor(settings)
        self._selector = EvidenceSelector(settings)
        self._prompt = GroundedPromptBuilder(settings)
        self._citations = CitationValidator()

    def prepare_question(self, raw: str) -> NormalizedQuestion:
        return self._questions.prepare(raw)

    async def answer(
        self,
        *,
        user_id: uuid.UUID,
        repository_id: uuid.UUID,
        question: NormalizedQuestion,
    ) -> GroundedAnswer:
        try:
            async with asyncio.timeout(self._settings.rag_total_timeout_seconds):
                return await self._answer_bounded(
                    user_id=user_id,
                    repository_id=repository_id,
                    question=question,
                )
        except TimeoutError:
            logger.warning(
                "repository_question_timed_out",
                repository_id=str(repository_id),
            )
            return self._no_answer(
                repository_id=repository_id,
                state=Answerability.TEMPORARILY_UNAVAILABLE,
            )

    async def _answer_bounded(  # noqa: PLR0911 -- explicit fail-closed exits are clearer
        self,
        *,
        user_id: uuid.UUID,
        repository_id: uuid.UUID,
        question: NormalizedQuestion,
    ) -> GroundedAnswer:
        repository = await self._installations.get_authorized_repository(
            user_id=user_id,
            repository_id=repository_id,
        )
        active = await self._load_active_index(repository)
        if active is None:
            return self._no_answer(
                repository_id=repository_id,
                state=Answerability.INSUFFICIENT_EVIDENCE,
                repository=repository,
            )
        if self._questions.is_unsupported(question):
            return self._no_answer(
                repository_id=repository_id,
                state=Answerability.UNSUPPORTED_QUESTION,
                active=active,
            )
        try:
            prepared = self._preprocessor.prepare_query(question.text)
            query_vector = await self._embeddings.embed_query(prepared)
            hits = await self._vectors.search(
                VectorScope(active.installation_id, active.repository_id, active.index_version),
                query_vector=query_vector,
                commit_sha=active.commit_sha,
                model_fingerprint=embedding_model_fingerprint(
                    self._settings, active.preprocessing_fingerprint
                ),
                preprocessing_fingerprint=active.preprocessing_fingerprint,
                limit=self._settings.rag_retrieval_overfetch,
                score_threshold=self._settings.rag_retrieval_score_threshold,
            )
        except IndexingError as error:
            logger.warning(
                "repository_retrieval_failed",
                repository_id=str(repository_id),
                error_code=error.code,
            )
            return self._no_answer(
                repository_id=repository_id,
                state=Answerability.TEMPORARILY_UNAVAILABLE,
                active=active,
            )
        evidence = self._selector.select(hits)
        if not evidence:
            return self._no_answer(
                repository_id=repository_id,
                state=Answerability.INSUFFICIENT_EVIDENCE,
                active=active,
            )
        try:
            draft = await self._llm.generate(self._prompt.build(question, evidence))
        except LLMProviderError as error:
            logger.warning(
                "repository_synthesis_failed",
                repository_id=str(repository_id),
                error_code=error.code,
            )
            return self._no_answer(
                repository_id=repository_id,
                state=Answerability.TEMPORARILY_UNAVAILABLE,
                active=active,
                evidence_count=len(evidence),
            )

        final_repository = await self._installations.get_authorized_repository(
            user_id=user_id,
            repository_id=repository_id,
        )
        final_active = await self._load_active_index(final_repository)
        if final_active != active:
            return self._no_answer(
                repository_id=repository_id,
                state=Answerability.TEMPORARILY_UNAVAILABLE,
                repository=final_repository,
            )

        state, citations = self._citations.validate(
            draft,
            evidence,
            commit_sha=active.commit_sha,
        )
        if state is not Answerability.ANSWERED:
            return self._no_answer(
                repository_id=repository_id,
                state=state,
                active=active,
                evidence_count=len(evidence),
            )
        return GroundedAnswer(
            repository_id=repository_id,
            answer=draft.answer,
            answerability=state,
            uncertainty=AnswerUncertainty(draft.uncertainty.value),
            citations=citations,
            commit_sha=active.commit_sha,
            index_version=active.index_version,
            retrieved_evidence_count=len(evidence),
        )

    async def _load_active_index(self, repository: Repository) -> ActiveIndex | None:
        if (
            repository.indexing_status is not RepositoryIndexingStatus.COMPLETE
            or repository.index_version < 1
            or repository.last_indexed_commit_sha is None
            or repository.active_vector_count < 1
        ):
            return None
        async with self._database.session() as session:
            build = await session.scalar(
                select(RepositoryIndexBuild).where(
                    RepositoryIndexBuild.repository_id == repository.id,
                    RepositoryIndexBuild.index_version == repository.index_version,
                    RepositoryIndexBuild.state == IndexBuildState.ACTIVE,
                    RepositoryIndexBuild.commit_sha == repository.last_indexed_commit_sha,
                    RepositoryIndexBuild.embedding_model_identifier
                    == self._settings.embedding_model_identifier,
                    RepositoryIndexBuild.embedding_model_revision
                    == self._settings.embedding_model_revision,
                    RepositoryIndexBuild.embedding_dimension == self._settings.embedding_dimension,
                    RepositoryIndexBuild.preprocessing_fingerprint
                    == self._preprocessor.policy_fingerprint,
                    RepositoryIndexBuild.vector_count == repository.active_vector_count,
                )
            )
        if build is None:
            return None
        return ActiveIndex(
            repository_id=repository.id,
            installation_id=repository.installation_id,
            index_version=build.index_version,
            commit_sha=build.commit_sha,
            preprocessing_fingerprint=build.preprocessing_fingerprint,
        )

    @staticmethod
    def _no_answer(
        *,
        repository_id: uuid.UUID,
        state: Answerability,
        active: ActiveIndex | None = None,
        repository: Repository | None = None,
        evidence_count: int = 0,
    ) -> GroundedAnswer:
        if state is Answerability.UNSUPPORTED_QUESTION:
            answer = _UNSUPPORTED
        elif state is Answerability.TEMPORARILY_UNAVAILABLE:
            answer = _UNAVAILABLE
        else:
            answer = _INSUFFICIENT
        return GroundedAnswer(
            repository_id=repository_id,
            answer=answer,
            answerability=state,
            uncertainty=(
                AnswerUncertainty.HIGH
                if state is Answerability.INSUFFICIENT_EVIDENCE
                else AnswerUncertainty.NOT_APPLICABLE
            ),
            citations=(),
            commit_sha=(
                active.commit_sha
                if active is not None
                else repository.last_indexed_commit_sha
                if repository is not None
                else None
            ),
            index_version=(
                active.index_version
                if active is not None
                else repository.index_version
                if repository is not None
                else 0
            ),
            retrieved_evidence_count=evidence_count,
        )
