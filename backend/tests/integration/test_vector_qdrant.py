"""Real Qdrant collection, isolation, idempotency, and cleanup verification."""

import asyncio
import os
import uuid
from collections.abc import Iterator

import pytest
from qdrant_client import AsyncQdrantClient, models

from app.embeddings.preprocessing import EmbeddingPreprocessor
from app.indexing.failures import IndexingError
from app.indexing.models import ChunkType, ContentChunk
from app.vector.qdrant import (
    QdrantVectorStore,
    VectorRecord,
    VectorScope,
    build_vector_record,
    embedding_model_fingerprint,
    scope_filter,
)
from tests.conftest import make_settings

pytestmark = pytest.mark.integration
_COLLECTION = "repolume_test_vector_isolation"


def qdrant_url() -> str:
    value = os.environ.get("TEST_QDRANT_URL")
    if value is None:
        pytest.fail("TEST_QDRANT_URL must target a disposable Qdrant instance")
    return value


async def delete_collection() -> None:
    client = AsyncQdrantClient(url=qdrant_url(), check_compatibility=False)
    if await client.collection_exists(_COLLECTION):
        await client.delete_collection(_COLLECTION)
    await client.close()


@pytest.fixture(autouse=True)
def clean_collection() -> Iterator[None]:
    asyncio.run(delete_collection())
    yield
    asyncio.run(delete_collection())


def chunk(repository_id: uuid.UUID, *, content: str, ordinal: int = 0) -> ContentChunk:
    return ContentChunk(
        repository_id=repository_id,
        index_version=1,
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
        content_hash=("b" if ordinal == 0 else "c") * 64,
        content=content,
    )


def test_real_qdrant_is_scoped_idempotent_and_deletes_only_one_scope() -> None:
    settings = make_settings(
        qdrant_url=qdrant_url(),
        qdrant_collection_name=_COLLECTION,
    )
    preprocessor = EmbeddingPreprocessor(settings)
    installation = uuid.uuid4()
    repository_a = uuid.uuid4()
    repository_b = uuid.uuid4()
    scope_a_v1 = VectorScope(installation, repository_a, 1)
    scope_a_v2 = VectorScope(installation, repository_a, 2)
    scope_b_v1 = VectorScope(installation, repository_b, 1)

    async def exercise() -> None:
        store = QdrantVectorStore(settings)
        await store.ensure_collection()
        records: list[tuple[VectorScope, VectorRecord]] = []
        for index, (scope, source) in enumerate(
            (
                (scope_a_v1, "def service():\n    return 'a-v1'"),
                (scope_a_v2, "def service():\n    return 'a-v2'"),
                (scope_b_v1, "def service():\n    return 'b-v1'"),
            )
        ):
            prepared = preprocessor.prepare_chunk(
                chunk(scope.repository_id, content=source, ordinal=index)
            )
            vector = [0.0] * settings.embedding_dimension
            vector[index] = 1.0
            records.append(
                (
                    scope,
                    build_vector_record(
                        scope=scope,
                        prepared=prepared,
                        vector=tuple(vector),
                        settings=settings,
                        policy_fingerprint=preprocessor.policy_fingerprint,
                    ),
                )
            )
        for scope, record in records:
            await store.upsert(scope, [record])
            await store.upsert(scope, [record])
            assert await store.count_scope(scope) == 1
            await store.validate_scope(
                scope,
                expected_count=1,
                commit_sha="a" * 40,
                model_fingerprint=embedding_model_fingerprint(
                    settings, preprocessor.policy_fingerprint
                ),
            )

        query = [0.0] * settings.embedding_dimension
        query[0] = 1.0
        retrieved = await store.search(
            scope_a_v1,
            query_vector=tuple(query),
            commit_sha="a" * 40,
            model_fingerprint=embedding_model_fingerprint(
                settings, preprocessor.policy_fingerprint
            ),
            preprocessing_fingerprint=preprocessor.policy_fingerprint,
            limit=10,
            score_threshold=0.1,
        )
        assert len(retrieved) == 1
        assert retrieved[0].content == "def service():\n    return 'a-v1'"
        assert retrieved[0].file_path == "app/service.py"
        assert retrieved[0].score == pytest.approx(1.0)

        wrong_version = await store.search(
            VectorScope(installation, repository_a, 99),
            query_vector=tuple(query),
            commit_sha="a" * 40,
            model_fingerprint=embedding_model_fingerprint(
                settings, preprocessor.policy_fingerprint
            ),
            preprocessing_fingerprint=preprocessor.policy_fingerprint,
            limit=10,
            score_threshold=0.1,
        )
        assert wrong_version == ()

        raw = AsyncQdrantClient(url=qdrant_url(), check_compatibility=False)
        points, _ = await raw.scroll(
            collection_name=_COLLECTION,
            scroll_filter=scope_filter(scope_a_v1),
            with_payload=True,
            with_vectors=False,
        )
        assert len(points) == 1
        assert points[0].payload is not None
        assert points[0].payload["content"] == "def service():\n    return 'a-v1'"
        serialized = repr(points[0].payload)
        assert settings.embedding_service_token.get_secret_value() not in serialized

        await store.delete_scope(scope_a_v1)
        assert await store.count_scope(scope_a_v1) == 0
        assert await store.count_scope(scope_a_v2) == 1
        assert await store.count_scope(scope_b_v1) == 1
        await raw.close()
        await store.close()

    asyncio.run(exercise())


def test_real_qdrant_rejects_collection_and_record_scope_mismatch() -> None:
    settings = make_settings(
        qdrant_url=qdrant_url(),
        qdrant_collection_name=_COLLECTION,
    )

    async def exercise() -> None:
        raw = AsyncQdrantClient(url=qdrant_url(), check_compatibility=False)
        await raw.create_collection(
            collection_name=_COLLECTION,
            vectors_config=models.VectorParams(size=3, distance=models.Distance.DOT),
        )
        store = QdrantVectorStore(settings)
        with pytest.raises(IndexingError) as collection_error:
            await store.ensure_collection()
        assert collection_error.value.code == "qdrant_collection_mismatch"

        scope = VectorScope(uuid.uuid4(), uuid.uuid4(), 1)
        wrong = VectorRecord(
            point_id=uuid.uuid4(),
            vector=tuple([0.0] * settings.embedding_dimension),
            payload={
                "installation_id": str(scope.installation_id),
                "repository_id": str(uuid.uuid4()),
                "index_version": scope.index_version,
            },
        )
        with pytest.raises(IndexingError) as scope_error:
            await store.upsert(scope, [wrong])
        assert scope_error.value.code == "vector_scope_mismatch"
        await store.close()
        await raw.close()

    asyncio.run(exercise())


def test_real_qdrant_rejects_malformed_scoped_search_payload() -> None:
    settings = make_settings(qdrant_url=qdrant_url(), qdrant_collection_name=_COLLECTION)
    preprocessor = EmbeddingPreprocessor(settings)
    scope = VectorScope(uuid.uuid4(), uuid.uuid4(), 1)

    async def exercise() -> None:
        store = QdrantVectorStore(settings)
        await store.ensure_collection()
        raw = AsyncQdrantClient(url=qdrant_url(), check_compatibility=False)
        vector = [0.0] * settings.embedding_dimension
        vector[0] = 1.0
        await raw.upsert(
            collection_name=_COLLECTION,
            wait=True,
            points=[
                models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    payload={
                        "installation_id": str(scope.installation_id),
                        "repository_id": str(scope.repository_id),
                        "index_version": 1,
                        "commit_sha": "a" * 40,
                        "embedding_model_fingerprint": embedding_model_fingerprint(
                            settings, preprocessor.policy_fingerprint
                        ),
                        "preprocessing_policy_fingerprint": preprocessor.policy_fingerprint,
                        "file_path": "malformed.py",
                    },
                )
            ],
        )
        with pytest.raises(IndexingError) as captured:
            await store.search(
                scope,
                query_vector=tuple(vector),
                commit_sha="a" * 40,
                model_fingerprint=embedding_model_fingerprint(
                    settings, preprocessor.policy_fingerprint
                ),
                preprocessing_fingerprint=preprocessor.policy_fingerprint,
                limit=1,
                score_threshold=0.1,
            )
        assert captured.value.code == "qdrant_malformed_search_result"
        await store.close()
        await raw.close()

    asyncio.run(exercise())
