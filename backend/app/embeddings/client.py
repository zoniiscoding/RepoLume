"""Typed asynchronous client for the private embedding service."""

import asyncio
import math
import secrets
import uuid
from collections.abc import Sequence
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings
from app.embeddings.preprocessing import PreparedEmbedding
from app.indexing.failures import IndexingError

_HTTP_OK = 200
_HTTP_SERVICE_UNAVAILABLE = 503


class _EmbeddingDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1)


class _EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    documents: list[_EmbeddingDocument]


class _EmbeddingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    embedding: list[float]


class _EmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    revision: str
    dimension: int
    normalized: bool
    results: list[_EmbeddingResult]


class _ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    model: str
    revision: str
    dimension: int


class EmbeddingProviderProtocol(Protocol):
    async def is_ready(self) -> bool: ...

    async def embed_documents(
        self, documents: Sequence[PreparedEmbedding]
    ) -> dict[str, tuple[float, ...]]: ...

    async def embed_query(self, query: PreparedEmbedding) -> tuple[float, ...]: ...

    async def close(self) -> None: ...


class EmbeddingServiceClient:
    """Call one fixed authenticated private service with bounded retries."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._model = settings.embedding_model_identifier
        self._revision = settings.embedding_model_revision
        self._dimension = settings.embedding_dimension
        self._batch_size = settings.embedding_batch_size
        self._token = settings.embedding_service_token.get_secret_value()
        self._max_attempts = settings.embedding_max_attempts
        self._retry_base = settings.embedding_retry_base_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=str(settings.embedding_service_url).rstrip("/"),
            follow_redirects=False,
            timeout=httpx.Timeout(
                connect=settings.embedding_connect_timeout_seconds,
                read=settings.embedding_read_timeout_seconds,
                write=settings.embedding_read_timeout_seconds,
                pool=settings.embedding_connect_timeout_seconds,
            ),
        )

    async def is_ready(self) -> bool:
        try:
            response = await self._client.get(
                "/health/ready",
                headers=self._headers(),
            )
            if response.status_code != _HTTP_OK:
                return False
            payload = _ReadinessResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError):
            return False
        return (
            payload.status == "ready"
            and payload.model == self._model
            and payload.revision == self._revision
            and payload.dimension == self._dimension
        )

    async def embed_documents(
        self, documents: Sequence[PreparedEmbedding]
    ) -> dict[str, tuple[float, ...]]:
        all_results: dict[str, tuple[float, ...]] = {}
        for offset in range(0, len(documents), self._batch_size):
            batch = documents[offset : offset + self._batch_size]
            batch_results = await self._embed_batch("document", batch)
            overlap = all_results.keys() & batch_results.keys()
            if overlap:
                raise self._invalid_response("duplicate_embedding_result")
            all_results.update(batch_results)
        return all_results

    async def embed_query(self, query: PreparedEmbedding) -> tuple[float, ...]:
        results = await self._embed_batch("query", [query])
        try:
            return results[query.item_id]
        except KeyError as error:
            raise self._invalid_response("missing_query_embedding") from error

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _embed_batch(
        self,
        kind: str,
        documents: Sequence[PreparedEmbedding],
    ) -> dict[str, tuple[float, ...]]:
        expected_ids = [document.item_id for document in documents]
        if len(set(expected_ids)) != len(expected_ids):
            raise self._invalid_response("duplicate_embedding_request_id")
        payload = _EmbeddingRequest(
            kind=kind,
            documents=[
                _EmbeddingDocument(id=document.item_id, text=document.text)
                for document in documents
            ],
        )
        response = await self._request_with_retry(payload)
        try:
            parsed = _EmbeddingResponse.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise self._invalid_response("malformed_embedding_response") from error
        if (
            parsed.model != self._model
            or parsed.revision != self._revision
            or parsed.dimension != self._dimension
            or not parsed.normalized
        ):
            raise self._invalid_response("embedding_model_mismatch")
        if len(parsed.results) != len(expected_ids):
            raise self._invalid_response("embedding_result_count_mismatch")
        results: dict[str, tuple[float, ...]] = {}
        for result in parsed.results:
            if result.id in results or result.id not in expected_ids:
                raise self._invalid_response("unexpected_embedding_result")
            if len(result.embedding) != self._dimension:
                raise self._invalid_response("embedding_dimension_mismatch")
            if any(not math.isfinite(value) for value in result.embedding):
                raise self._invalid_response("non_finite_embedding")
            norm = math.sqrt(sum(value * value for value in result.embedding))
            if not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
                raise self._invalid_response("embedding_normalization_mismatch")
            results[result.id] = tuple(result.embedding)
        if set(results) != set(expected_ids):
            raise self._invalid_response("missing_embedding_result")
        return results

    async def _request_with_retry(self, payload: _EmbeddingRequest) -> httpx.Response:
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.post(
                    "/v1/embeddings",
                    headers=self._headers(),
                    json=payload.model_dump(mode="json"),
                )
            except (httpx.ConnectError, httpx.TimeoutException) as error:
                if attempt == self._max_attempts:
                    raise IndexingError(
                        code="embedding_service_unavailable",
                        message="The private embedding service is temporarily unavailable",
                        retryable=True,
                    ) from error
                await self._backoff(attempt)
                continue
            if response.status_code == _HTTP_OK:
                return response
            retryable = response.status_code in {408, 425, 429, 500, 502, 503, 504}
            if retryable and attempt < self._max_attempts:
                await self._backoff(attempt)
                continue
            if response.status_code in {401, 403}:
                raise IndexingError(
                    code="embedding_authentication_failed",
                    message="The private embedding service rejected worker authentication",
                    retryable=False,
                )
            if response.status_code == _HTTP_SERVICE_UNAVAILABLE:
                code = "embedding_model_not_ready"
                message = "The embedding model is not ready"
            elif response.status_code in {413, 422}:
                code = "embedding_input_rejected"
                message = "A repository chunk was rejected by the embedding service"
            else:
                code = "embedding_service_failure"
                message = "The private embedding service could not generate embeddings"
            raise IndexingError(code=code, message=message, retryable=retryable)
        raise AssertionError("retry_exhausted")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "X-Request-ID": str(uuid.uuid4()),
        }

    async def _backoff(self, attempt: int) -> None:
        ceiling = self._retry_base * (2 ** (attempt - 1))
        jitter = secrets.randbelow(max(1, int(ceiling * 500))) / 1000
        await asyncio.sleep(ceiling + jitter)

    @staticmethod
    def _invalid_response(category: str) -> IndexingError:
        return IndexingError(
            code=category,
            message="The embedding service returned an invalid response",
            retryable=False,
        )
