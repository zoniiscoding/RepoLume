"""Deterministic vector identity, trusted filters, and fail-closed validation."""

import uuid
from collections.abc import Callable
from typing import cast

import httpx
import pytest
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.embeddings.preprocessing import EmbeddingPreprocessor, PreparedEmbedding
from app.indexing.failures import IndexingError
from app.indexing.models import ChunkType, ContentChunk
from app.vector.qdrant import (
    QdrantVectorStore,
    VectorRecord,
    VectorScope,
    build_vector_record,
    deterministic_point_id,
    retrieval_filter,
    scope_filter,
)
from tests.conftest import make_settings


def prepared(repository_id: uuid.UUID, *, ordinal: int = 0) -> PreparedEmbedding:
    chunk = ContentChunk(
        repository_id=repository_id,
        index_version=2,
        ordinal=ordinal,
        file_path="app/service.py",
        language="python",
        chunk_type=ChunkType.FUNCTION,
        symbol_name="service",
        qualified_name="app.service.service",
        parent_qualified_name=None,
        heading_hierarchy=(),
        imports=(),
        decorators=(),
        signature="def service():",
        docstring=None,
        start_line=1,
        end_line=2,
        commit_sha="a" * 40,
        content_hash="b" * 64,
        content="def service():\n    pass",
    )
    return EmbeddingPreprocessor(make_settings()).prepare_chunk(chunk)


def test_point_ids_are_deterministic_and_do_not_cross_scope() -> None:
    installation = uuid.UUID("10000000-0000-0000-0000-000000000000")
    repository_a = uuid.UUID("20000000-0000-0000-0000-000000000000")
    repository_b = uuid.UUID("30000000-0000-0000-0000-000000000000")
    scope_a = VectorScope(installation, repository_a, 2)

    first = deterministic_point_id(scope_a, prepared(repository_a))
    assert first == deterministic_point_id(scope_a, prepared(repository_a))
    assert first != deterministic_point_id(
        VectorScope(installation, repository_b, 2), prepared(repository_b)
    )
    assert first != deterministic_point_id(
        VectorScope(installation, repository_a, 3), prepared(repository_a)
    )
    assert first != deterministic_point_id(scope_a, prepared(repository_a, ordinal=1))


def test_scope_filter_always_contains_installation_repository_and_version() -> None:
    scope = VectorScope(uuid.uuid4(), uuid.uuid4(), 7)
    built = scope_filter(scope).model_dump(mode="json")
    conditions = {item["key"]: item["match"]["value"] for item in built["must"]}
    assert conditions == {
        "installation_id": str(scope.installation_id),
        "repository_id": str(scope.repository_id),
        "index_version": 7,
    }


def test_retrieval_filter_adds_commit_model_and_preprocessing_identity() -> None:
    scope = VectorScope(uuid.uuid4(), uuid.uuid4(), 3)
    built = retrieval_filter(
        scope,
        commit_sha="a" * 40,
        model_fingerprint="b" * 64,
        preprocessing_fingerprint="c" * 64,
    ).model_dump(mode="json")
    conditions = {item["key"]: item["match"]["value"] for item in built["must"]}
    assert conditions == {
        "installation_id": str(scope.installation_id),
        "repository_id": str(scope.repository_id),
        "index_version": 3,
        "commit_sha": "a" * 40,
        "embedding_model_fingerprint": "b" * 64,
        "preprocessing_policy_fingerprint": "c" * 64,
    }


def vector_store(**settings_overrides: object) -> QdrantVectorStore:
    return QdrantVectorStore(
        make_settings(**settings_overrides),
        client=cast(AsyncQdrantClient, object()),
    )


def scoped_record(scope: VectorScope, vector: tuple[float, ...]) -> VectorRecord:
    return VectorRecord(
        point_id=uuid.uuid4(),
        vector=vector,
        payload={
            "installation_id": str(scope.installation_id),
            "repository_id": str(scope.repository_id),
            "index_version": scope.index_version,
        },
    )


def test_vector_identity_rejects_invalid_versions_and_missing_chunk_metadata() -> None:
    with pytest.raises(ValueError, match="invalid_index_version"):
        VectorScope(uuid.uuid4(), uuid.uuid4(), 0)

    scope = VectorScope(uuid.uuid4(), uuid.uuid4(), 1)
    query = EmbeddingPreprocessor(make_settings()).prepare_query("Where is validate?")
    with pytest.raises(ValueError, match="missing_chunk_metadata"):
        deterministic_point_id(scope, query)
    with pytest.raises(ValueError, match="missing_chunk_metadata"):
        build_vector_record(
            scope=scope,
            prepared=query,
            vector=(1.0,) + (0.0,) * 767,
            settings=make_settings(),
            policy_fingerprint="f" * 64,
        )


@pytest.mark.asyncio
async def test_readiness_fails_closed_when_collection_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = vector_store()

    async def ready() -> None:
        return None

    monkeypatch.setattr(store, "ensure_collection", ready)
    assert await store.is_ready() is True

    async def unavailable() -> None:
        raise IndexingError(code="qdrant_unavailable", message="safe", retryable=True)

    monkeypatch.setattr(store, "ensure_collection", unavailable)
    assert await store.is_ready() is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "vector",
    [
        (float("nan"),) + (0.0,) * 767,
        (0.0,) * 768,
    ],
)
async def test_upsert_rejects_nonfinite_or_unnormalized_vectors(
    vector: tuple[float, ...],
) -> None:
    scope = VectorScope(uuid.uuid4(), uuid.uuid4(), 1)
    with pytest.raises(IndexingError) as captured:
        await vector_store().upsert(scope, [scoped_record(scope, vector)])

    assert captured.value.code == "invalid_vector"
    assert captured.value.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "vector",
    [
        (float("inf"),) + (0.0,) * 767,
        (0.0,) * 768,
    ],
)
async def test_search_rejects_nonfinite_or_unnormalized_query_vectors(
    vector: tuple[float, ...],
) -> None:
    scope = VectorScope(uuid.uuid4(), uuid.uuid4(), 1)
    with pytest.raises(IndexingError) as captured:
        await vector_store().search(
            scope,
            query_vector=vector,
            commit_sha="a" * 40,
            model_fingerprint="b" * 64,
            preprocessing_fingerprint="c" * 64,
            limit=6,
            score_threshold=0.25,
        )

    assert captured.value.code == "invalid_query_vector"
    assert captured.value.retryable is False


def unexpected_response(status_code: int) -> UnexpectedResponse:
    return UnexpectedResponse(
        status_code=status_code,
        reason_phrase="provider detail",
        content=b"sensitive provider body",
        headers=httpx.Headers(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_factory", "expected_code", "retryable"),
    [
        (lambda: unexpected_response(401), "qdrant_authentication_failed", False),
        (lambda: unexpected_response(400), "test_operation_failed", False),
        (lambda: unexpected_response(503), "test_operation_failed", True),
        (lambda: TimeoutError("sensitive timeout"), "test_operation_failed", True),
    ],
)
async def test_vector_operation_failures_are_safely_classified(
    failure_factory: Callable[[], Exception],
    expected_code: str,
    retryable: bool,
) -> None:
    store = vector_store(qdrant_max_attempts=1)

    async def fail() -> None:
        raise failure_factory()

    with pytest.raises(IndexingError) as captured:
        await store._run(fail, code="test_operation_failed")

    assert captured.value.code == expected_code
    assert captured.value.retryable is retryable
    assert "sensitive" not in str(captured.value)
    assert "provider detail" not in str(captured.value)


@pytest.mark.asyncio
async def test_vector_operation_retries_transient_failure_then_returns() -> None:
    store = vector_store(qdrant_max_attempts=2, qdrant_retry_base_seconds=0.001)
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise unexpected_response(503)
        return "ready"

    result = await store._run(operation, code="test_operation_failed")

    assert result == "ready"
    assert attempts == 2


def valid_search_point(scope: VectorScope, **overrides: object) -> models.ScoredPoint:
    payload: dict[str, object] = {
        "installation_id": str(scope.installation_id),
        "repository_id": str(scope.repository_id),
        "index_version": scope.index_version,
        "commit_sha": "a" * 40,
        "embedding_model_fingerprint": "b" * 64,
        "preprocessing_policy_fingerprint": "c" * 64,
        "file_path": "app/service.py",
        "language": "python",
        "chunk_type": "function",
        "symbol_name": "validate",
        "qualified_symbol_name": "app.service.validate",
        "start_line": 10,
        "end_line": 12,
        "content": "def validate():\n    return True",
        "stable_chunk_hash": "d" * 64,
    }
    score = overrides.pop("score", 0.9)
    payload.update(overrides)
    assert isinstance(score, float)
    return models.ScoredPoint(
        id=uuid.uuid4(),
        version=1,
        score=score,
        payload=payload,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"score": float("nan")},
        {"installation_id": str(uuid.uuid4())},
        {"start_line": "10"},
        {"start_line": 0},
        {"end_line": 9},
        {"file_path": "/etc/passwd"},
        {"file_path": "app/../secret.py"},
        {"content": ""},
        {"stable_chunk_hash": "short"},
        {"symbol_name": 7},
        {"qualified_symbol_name": 7},
    ],
)
def test_retrieval_hit_parser_rejects_malformed_or_cross_scope_evidence(
    overrides: dict[str, object],
) -> None:
    scope = VectorScope(uuid.uuid4(), uuid.uuid4(), 1)
    point = valid_search_point(scope, **overrides)

    with pytest.raises(IndexingError) as captured:
        QdrantVectorStore._parse_retrieval_hit(
            scope,
            point,
            commit_sha="a" * 40,
            model_fingerprint="b" * 64,
            preprocessing_fingerprint="c" * 64,
        )

    assert captured.value.code == "qdrant_malformed_search_result"
    assert captured.value.retryable is False
