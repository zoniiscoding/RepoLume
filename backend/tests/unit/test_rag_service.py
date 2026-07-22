"""Fail-closed repository question orchestration behavior."""

import asyncio
import uuid
from typing import cast

import pytest

from app.db.models.enums import RepositoryIndexingStatus
from app.db.models.repository import Repository
from app.db.session import Database
from app.embeddings.client import EmbeddingProviderProtocol
from app.embeddings.preprocessing import PreparedEmbedding
from app.indexing.failures import IndexingError
from app.llm.client import (
    DraftAnswerability,
    DraftUncertainty,
    GroundedAnswerDraft,
    GroundedGenerationRequest,
    LLMProviderError,
    LLMProviderProtocol,
)
from app.rag.models import Answerability
from app.rag.service import ActiveIndex, QuestionService
from app.services.installations import InstallationService
from app.vector.qdrant import RetrievalHit, VectorScope, VectorStoreProtocol
from tests.conftest import make_settings


class FakeInstallations:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self.calls = 0

    async def get_authorized_repository(
        self,
        *,
        user_id: uuid.UUID,
        repository_id: uuid.UUID,
        require_fresh_public_visibility: bool = False,
    ) -> Repository:
        del user_id
        assert repository_id == self.repository.id
        assert require_fresh_public_visibility is True
        self.calls += 1
        return self.repository


class FakeEmbeddings:
    def __init__(self) -> None:
        self.calls = 0

    async def embed_query(self, query: PreparedEmbedding) -> tuple[float, ...]:
        assert "query_content_begin" in query.text
        self.calls += 1
        return (1.0,) + (0.0,) * 767


class FakeVectors:
    def __init__(self, *, failure: IndexingError | None = None) -> None:
        self.failure = failure
        self.calls = 0

    async def search(
        self,
        scope: VectorScope,
        **kwargs: object,
    ) -> tuple[RetrievalHit, ...]:
        assert scope.index_version == 1
        assert kwargs["commit_sha"] == "a" * 40
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return (
            RetrievalHit(
                score=0.9,
                file_path="app/service.py",
                language="python",
                chunk_type="function",
                symbol_name="validate",
                qualified_symbol_name="app.service.validate",
                start_line=10,
                end_line=12,
                content="def validate(response):\n    return response.is_valid",
                stable_chunk_hash="b" * 64,
            ),
        )


class FakeLLM:
    def __init__(self, *, failure: LLMProviderError | None = None) -> None:
        self.failure = failure
        self.calls = 0

    async def generate(self, request: GroundedGenerationRequest) -> GroundedAnswerDraft:
        assert "app/service.py" in request.evidence_payload
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return GroundedAnswerDraft(
            answer="The validate function returns the response validity flag.",
            answerability=DraftAnswerability.ANSWERED,
            uncertainty=DraftUncertainty.LOW,
            evidence_ids=["E1"],
        )


def repository() -> Repository:
    return Repository(
        id=uuid.uuid4(),
        installation_id=uuid.uuid4(),
        github_repository_id=9001,
        github_owner="owner",
        github_name="private-repo",
        github_full_name="owner/private-repo",
        github_url="https://github.com/owner/private-repo",
        is_private=True,
        default_branch="main",
        last_indexed_commit_sha="a" * 40,
        index_version=1,
        indexing_status=RepositoryIndexingStatus.COMPLETE,
        active_vector_count=1,
    )


def active_index(item: Repository) -> ActiveIndex:
    return ActiveIndex(
        repository_id=item.id,
        installation_id=item.installation_id,
        index_version=1,
        commit_sha="a" * 40,
        preprocessing_fingerprint="c" * 64,
    )


def question_service(
    item: Repository,
    *,
    vectors: FakeVectors | None = None,
    llm: FakeLLM | None = None,
    **settings_overrides: object,
) -> tuple[QuestionService, FakeInstallations, FakeEmbeddings, FakeVectors, FakeLLM]:
    installations = FakeInstallations(item)
    embeddings = FakeEmbeddings()
    resolved_vectors = vectors or FakeVectors()
    resolved_llm = llm or FakeLLM()
    service = QuestionService(
        database=cast(Database, object()),
        installations=cast(InstallationService, installations),
        embeddings=cast(EmbeddingProviderProtocol, embeddings),
        vectors=cast(VectorStoreProtocol, resolved_vectors),
        llm=cast(LLMProviderProtocol, resolved_llm),
        settings=make_settings(**settings_overrides),
    )
    return service, installations, embeddings, resolved_vectors, resolved_llm


@pytest.mark.asyncio
async def test_question_timeout_cancels_work_and_returns_safe_unavailable_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = repository()
    service, _, _, _, _ = question_service(
        item,
        rag_total_timeout_seconds=0.01,
        llm_read_timeout_seconds=0.005,
    )
    cancelled = asyncio.Event()

    async def blocked_answer(
        instance: QuestionService,
        *,
        user_id: uuid.UUID,
        repository_id: uuid.UUID,
        question: object,
    ) -> object:
        del instance, user_id, repository_id, question
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError("unreachable")

    monkeypatch.setattr(QuestionService, "_answer_bounded", blocked_answer)
    result = await service.answer(
        user_id=uuid.uuid4(),
        repository_id=item.id,
        question=service.prepare_question("How does validate work?"),
    )

    assert cancelled.is_set()
    assert result.answerability is Answerability.TEMPORARILY_UNAVAILABLE
    assert result.citations == ()
    assert result.commit_sha is None
    assert result.index_version == 0


@pytest.mark.asyncio
async def test_retrieval_failure_skips_synthesis_and_returns_safe_unavailable_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = repository()
    vectors = FakeVectors(
        failure=IndexingError(
            code="qdrant_search_failed",
            message="provider details are not safe",
            retryable=True,
        )
    )
    service, _, embeddings, _, llm = question_service(item, vectors=vectors)
    active = active_index(item)

    async def load_active(instance: QuestionService, current: Repository) -> ActiveIndex:
        del instance
        assert current is item
        return active

    monkeypatch.setattr(QuestionService, "_load_active_index", load_active)
    result = await service.answer(
        user_id=uuid.uuid4(),
        repository_id=item.id,
        question=service.prepare_question("How does validate work?"),
    )

    assert embeddings.calls == 1
    assert vectors.calls == 1
    assert llm.calls == 0
    assert result.answerability is Answerability.TEMPORARILY_UNAVAILABLE
    assert result.commit_sha == "a" * 40


@pytest.mark.asyncio
async def test_llm_failure_discards_evidence_and_returns_safe_unavailable_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = repository()
    llm = FakeLLM(failure=LLMProviderError("llm_unavailable", retryable=True))
    service, _, _, vectors, _ = question_service(item, llm=llm)
    active = active_index(item)

    async def load_active(instance: QuestionService, current: Repository) -> ActiveIndex:
        del instance
        assert current is item
        return active

    monkeypatch.setattr(QuestionService, "_load_active_index", load_active)
    result = await service.answer(
        user_id=uuid.uuid4(),
        repository_id=item.id,
        question=service.prepare_question("How does validate work?"),
    )

    assert vectors.calls == 1
    assert llm.calls == 1
    assert result.answerability is Answerability.TEMPORARILY_UNAVAILABLE
    assert result.citations == ()
    assert result.retrieved_evidence_count == 1


@pytest.mark.asyncio
async def test_access_or_active_index_change_after_generation_discards_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = repository()
    service, installations, _, _, llm = question_service(item)
    current = active_index(item)
    indexes = iter((current, current, None))

    async def load_active(
        instance: QuestionService,
        current: Repository,
    ) -> ActiveIndex | None:
        del instance
        assert current is item
        return next(indexes)

    monkeypatch.setattr(QuestionService, "_load_active_index", load_active)
    result = await service.answer(
        user_id=uuid.uuid4(),
        repository_id=item.id,
        question=service.prepare_question("How does validate work?"),
    )

    assert installations.calls == 3
    assert llm.calls == 1
    assert result.answerability is Answerability.TEMPORARILY_UNAVAILABLE
    assert result.citations == ()
    assert "validity flag" not in result.answer
