"""Typed Qdrant adapter with mandatory repository and index-version scope."""

import asyncio
import hashlib
import math
import secrets
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import ClassVar, Protocol, TypeAlias, TypeVar

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.conversions.common_types import PointId
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.core.config import Settings
from app.embeddings.preprocessing import PreparedEmbedding
from app.indexing.failures import IndexingError

POINT_NAMESPACE = uuid.UUID("cedab252-9197-54f6-9603-b2f936a85e78")
_T = TypeVar("_T")
PayloadValue: TypeAlias = str | int | None


@dataclass(frozen=True, slots=True)
class VectorScope:
    """Server-derived ownership and inactive/active version filter."""

    installation_id: uuid.UUID
    repository_id: uuid.UUID
    index_version: int

    def __post_init__(self) -> None:
        if self.index_version < 1:
            raise ValueError("invalid_index_version")


@dataclass(frozen=True, slots=True)
class VectorRecord:
    point_id: uuid.UUID
    vector: tuple[float, ...]
    payload: dict[str, PayloadValue]


class VectorStoreProtocol(Protocol):
    async def is_ready(self) -> bool: ...

    async def ensure_collection(self) -> None: ...

    async def upsert(self, scope: VectorScope, records: Sequence[VectorRecord]) -> None: ...

    async def count_scope(self, scope: VectorScope) -> int: ...

    async def validate_scope(
        self,
        scope: VectorScope,
        *,
        expected_count: int,
        commit_sha: str,
        model_fingerprint: str,
    ) -> None: ...

    async def delete_scope(self, scope: VectorScope) -> None: ...

    async def close(self) -> None: ...


class VectorReadinessProtocol(Protocol):
    async def is_ready(self) -> bool: ...

    async def close(self) -> None: ...


def deterministic_point_id(scope: VectorScope, prepared: PreparedEmbedding) -> uuid.UUID:
    """Derive a stable UUID without relying on collection or insertion order alone."""
    chunk = prepared.chunk
    if chunk is None:
        raise ValueError("missing_chunk_metadata")
    identity = "\x1f".join(
        (
            str(scope.installation_id),
            str(scope.repository_id),
            str(scope.index_version),
            chunk.file_path,
            chunk.content_hash,
            chunk.chunk_type.value,
            str(chunk.ordinal),
        )
    )
    return uuid.uuid5(POINT_NAMESPACE, identity)


def embedding_model_fingerprint(settings: Settings, policy_fingerprint: str) -> str:
    value = "\x1f".join(
        (
            settings.embedding_model_identifier,
            settings.embedding_model_revision,
            str(settings.embedding_dimension),
            "l2_normalized",
            policy_fingerprint,
        )
    )
    return hashlib.sha256(value.encode()).hexdigest()


def build_vector_record(
    *,
    scope: VectorScope,
    prepared: PreparedEmbedding,
    vector: tuple[float, ...],
    settings: Settings,
    policy_fingerprint: str,
) -> VectorRecord:
    chunk = prepared.chunk
    if chunk is None:
        raise ValueError("missing_chunk_metadata")
    return VectorRecord(
        point_id=deterministic_point_id(scope, prepared),
        vector=vector,
        payload={
            "tenant_id": str(scope.installation_id),
            "installation_id": str(scope.installation_id),
            "repository_id": str(scope.repository_id),
            "index_version": scope.index_version,
            "commit_sha": chunk.commit_sha,
            "file_path": chunk.file_path,
            "language": chunk.language,
            "chunk_type": chunk.chunk_type.value,
            "symbol_name": chunk.symbol_name,
            "qualified_symbol_name": chunk.qualified_name,
            "parent_symbol": chunk.parent_qualified_name,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "content": chunk.content,
            "stable_chunk_hash": chunk.content_hash,
            "preprocessing_fingerprint": prepared.fingerprint,
            "preprocessing_policy_fingerprint": policy_fingerprint,
            "embedding_model_identifier": settings.embedding_model_identifier,
            "embedding_model_revision": settings.embedding_model_revision,
            "embedding_model_fingerprint": embedding_model_fingerprint(
                settings, policy_fingerprint
            ),
        },
    )


def scope_filter(scope: VectorScope) -> models.Filter:
    """Construct the only supported repository-version selector from typed values."""
    return models.Filter(
        must=[
            models.FieldCondition(
                key="installation_id",
                match=models.MatchValue(value=str(scope.installation_id)),
            ),
            models.FieldCondition(
                key="repository_id",
                match=models.MatchValue(value=str(scope.repository_id)),
            ),
            models.FieldCondition(
                key="index_version",
                match=models.MatchValue(value=scope.index_version),
            ),
        ]
    )


class QdrantVectorStore:
    """Own collection configuration and all scoped point operations."""

    _PAYLOAD_SCHEMA: ClassVar[dict[str, models.PayloadSchemaType]] = {
        "tenant_id": models.PayloadSchemaType.KEYWORD,
        "installation_id": models.PayloadSchemaType.KEYWORD,
        "repository_id": models.PayloadSchemaType.KEYWORD,
        "index_version": models.PayloadSchemaType.INTEGER,
        "commit_sha": models.PayloadSchemaType.KEYWORD,
        "embedding_model_fingerprint": models.PayloadSchemaType.KEYWORD,
    }

    def __init__(
        self,
        settings: Settings,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        self._collection = settings.qdrant_collection_name
        self._dimension = settings.embedding_dimension
        self._model_identifier = settings.embedding_model_identifier
        self._model_revision = settings.embedding_model_revision
        self._batch_size = settings.qdrant_upsert_batch_size
        self._max_attempts = settings.qdrant_max_attempts
        self._retry_base = settings.qdrant_retry_base_seconds
        self._timeout = max(1, math.ceil(settings.qdrant_timeout_seconds))
        self._owns_client = client is None
        api_key = settings.qdrant_api_key.get_secret_value() or None
        self._client = client or AsyncQdrantClient(
            url=str(settings.qdrant_url).rstrip("/"),
            api_key=api_key,
            timeout=self._timeout,
            prefer_grpc=False,
            cloud_inference=False,
            check_compatibility=False,
        )

    async def is_ready(self) -> bool:
        try:
            await self.ensure_collection()
        except IndexingError:
            return False
        return True

    async def ensure_collection(self) -> None:
        exists = await self._run(
            lambda: self._client.collection_exists(self._collection),
            code="qdrant_unavailable",
        )
        if not exists:
            await self._run(
                lambda: self._client.create_collection(
                    collection_name=self._collection,
                    vectors_config=models.VectorParams(
                        size=self._dimension,
                        distance=models.Distance.COSINE,
                    ),
                    metadata={
                        "embedding_model_identifier": self._model_identifier,
                        "embedding_model_revision": self._model_revision,
                        "embedding_dimension": self._dimension,
                        "normalization": "l2",
                    },
                    timeout=self._timeout,
                ),
                code="qdrant_collection_creation_failed",
            )
        info = await self._run(
            lambda: self._client.get_collection(self._collection),
            code="qdrant_unavailable",
        )
        vectors = info.config.params.vectors
        if not isinstance(vectors, models.VectorParams):
            raise self._configuration_mismatch()
        if vectors.size != self._dimension or vectors.distance != models.Distance.COSINE:
            raise self._configuration_mismatch()
        metadata = info.config.metadata or {}
        expected_metadata: Mapping[str, object] = {
            "embedding_model_identifier": self._model_identifier,
            "embedding_model_revision": self._model_revision,
            "embedding_dimension": self._dimension,
            "normalization": "l2",
        }
        if any(metadata.get(key) != value for key, value in expected_metadata.items()):
            raise self._configuration_mismatch()
        for field_name, field_type in self._PAYLOAD_SCHEMA.items():
            existing = info.payload_schema.get(field_name)
            if existing is not None and existing.data_type != field_type:
                raise self._configuration_mismatch()
            if existing is None:
                await self._run(
                    partial(
                        self._client.create_payload_index,
                        collection_name=self._collection,
                        field_name=field_name,
                        field_schema=field_type,
                        wait=True,
                        timeout=self._timeout,
                    ),
                    code="qdrant_payload_index_failed",
                )

    async def upsert(self, scope: VectorScope, records: Sequence[VectorRecord]) -> None:
        for record in records:
            self._validate_record_scope(scope, record)
            if len(record.vector) != self._dimension or any(
                not math.isfinite(value) for value in record.vector
            ):
                raise IndexingError(
                    code="invalid_vector",
                    message="An embedding failed vector validation",
                    retryable=False,
                )
            norm = math.sqrt(sum(value * value for value in record.vector))
            if not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
                raise IndexingError(
                    code="invalid_vector",
                    message="An embedding failed vector validation",
                    retryable=False,
                )
        for offset in range(0, len(records), self._batch_size):
            batch = records[offset : offset + self._batch_size]
            points = [
                models.PointStruct(
                    id=str(record.point_id),
                    vector=list(record.vector),
                    payload=record.payload,
                )
                for record in batch
            ]
            await self._run(
                partial(
                    self._client.upsert,
                    collection_name=self._collection,
                    points=points,
                    wait=True,
                    timeout=self._timeout,
                ),
                code="qdrant_upsert_failed",
            )

    async def count_scope(self, scope: VectorScope) -> int:
        result = await self._run(
            lambda: self._client.count(
                collection_name=self._collection,
                count_filter=scope_filter(scope),
                exact=True,
                timeout=self._timeout,
            ),
            code="qdrant_validation_failed",
        )
        return result.count

    async def validate_scope(
        self,
        scope: VectorScope,
        *,
        expected_count: int,
        commit_sha: str,
        model_fingerprint: str,
    ) -> None:
        if await self.count_scope(scope) != expected_count:
            raise IndexingError(
                code="vector_count_mismatch",
                message="The inactive vector index failed count validation",
                retryable=False,
            )
        seen = 0
        offset: PointId | None = None
        while True:
            records, next_offset = await self._run(
                partial(
                    self._client.scroll,
                    collection_name=self._collection,
                    scroll_filter=scope_filter(scope),
                    limit=min(self._batch_size, 256),
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                    timeout=self._timeout,
                ),
                code="qdrant_validation_failed",
            )
            for record in records:
                payload = record.payload or {}
                if (
                    payload.get("installation_id") != str(scope.installation_id)
                    or payload.get("repository_id") != str(scope.repository_id)
                    or payload.get("index_version") != scope.index_version
                    or payload.get("commit_sha") != commit_sha
                    or payload.get("embedding_model_fingerprint") != model_fingerprint
                ):
                    raise IndexingError(
                        code="vector_metadata_mismatch",
                        message="The inactive vector index failed metadata validation",
                        retryable=False,
                    )
                seen += 1
            if next_offset is None:
                break
            offset = next_offset
        if seen != expected_count:
            raise IndexingError(
                code="vector_count_mismatch",
                message="The inactive vector index failed count validation",
                retryable=False,
            )

    async def delete_scope(self, scope: VectorScope) -> None:
        await self._run(
            lambda: self._client.delete(
                collection_name=self._collection,
                points_selector=models.FilterSelector(filter=scope_filter(scope)),
                wait=True,
                timeout=self._timeout,
            ),
            code="qdrant_cleanup_failed",
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    async def _run(self, operation: Callable[[], Awaitable[_T]], *, code: str) -> _T:
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await operation()
            except UnexpectedResponse as error:
                if error.status_code in {401, 403}:
                    raise IndexingError(
                        code="qdrant_authentication_failed",
                        message="Qdrant rejected service authentication",
                        retryable=False,
                    ) from error
                retryable = error.status_code in {408, 425, 429, 500, 502, 503, 504}
                if not retryable or attempt == self._max_attempts:
                    raise IndexingError(
                        code=code,
                        message="The vector store operation failed",
                        retryable=retryable,
                    ) from error
            except (ResponseHandlingException, TimeoutError, OSError) as error:
                if attempt == self._max_attempts:
                    raise IndexingError(
                        code=code,
                        message="The vector store is temporarily unavailable",
                        retryable=True,
                    ) from error
            ceiling = self._retry_base * (2 ** (attempt - 1))
            jitter = secrets.randbelow(max(1, int(ceiling * 500))) / 1000
            await asyncio.sleep(ceiling + jitter)
        raise AssertionError("retry_exhausted")

    @staticmethod
    def _validate_record_scope(scope: VectorScope, record: VectorRecord) -> None:
        if (
            record.payload.get("installation_id") != str(scope.installation_id)
            or record.payload.get("repository_id") != str(scope.repository_id)
            or record.payload.get("index_version") != scope.index_version
        ):
            raise IndexingError(
                code="vector_scope_mismatch",
                message="A vector record did not match its trusted repository scope",
                retryable=False,
            )

    @staticmethod
    def _configuration_mismatch() -> IndexingError:
        return IndexingError(
            code="qdrant_collection_mismatch",
            message="Qdrant collection configuration does not match the embedding model",
            retryable=False,
        )
