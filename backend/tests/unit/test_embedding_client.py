"""Private embedding client batching and hostile-response validation."""

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from app.embeddings.client import EmbeddingServiceClient
from app.embeddings.preprocessing import PreparedEmbedding
from app.indexing.failures import IndexingError
from tests.conftest import make_settings


def prepared(item_id: str) -> PreparedEmbedding:
    return PreparedEmbedding(
        item_id=item_id, text=f"safe-{item_id}", fingerprint="f" * 64, chunk=None
    )


def response_payload(ids: list[str], *, dimension: int = 768) -> dict[str, object]:
    vector = [1.0, *([0.0] * (dimension - 1))]
    return {
        "model": "jinaai/jina-embeddings-v2-base-code",
        "revision": "516f4baf13dec4ddddda8631e019b5737c8bc250",
        "dimension": dimension,
        "normalized": True,
        "results": [{"id": item_id, "embedding": vector} for item_id in ids],
    }


def client_for(
    handler: Callable[[httpx.Request], httpx.Response], **settings: object
) -> EmbeddingServiceClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://embedding.test", transport=transport)
    return EmbeddingServiceClient(
        make_settings(embedding_retry_base_seconds=0.001, **settings),
        client=http_client,
    )


@pytest.mark.asyncio
async def test_batches_and_authenticates_without_logging_source() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        return httpx.Response(
            200, json=response_payload([item["id"] for item in body["documents"]])
        )

    client = client_for(handler, embedding_batch_size=2)
    result = await client.embed_documents([prepared("0"), prepared("1"), prepared("2")])

    assert set(result) == {"0", "1", "2"}
    assert len(requests) == 2
    assert all(request.headers["authorization"].startswith("Bearer ") for request in requests)
    assert all(request.headers["x-request-id"] for request in requests)


@pytest.mark.asyncio
async def test_retries_transient_status_and_reports_authentication_failure() -> None:
    calls = 0

    def transient(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=response_payload(["0"]))

    assert "0" in await client_for(transient).embed_documents([prepared("0")])
    assert calls == 2

    auth_client = client_for(lambda _: httpx.Response(401))
    with pytest.raises(IndexingError) as caught:
        await auth_client.embed_documents([prepared("0")])
    assert caught.value.code == "embedding_authentication_failed"
    assert not caught.value.retryable


@pytest.mark.parametrize(
    "error_type",
    [
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
    ],
)
@pytest.mark.asyncio
async def test_retries_and_safely_classifies_transport_timeouts(
    error_type: type[httpx.TransportError],
) -> None:
    calls = 0

    def unavailable(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise error_type("private transport failure", request=request)

    client = client_for(unavailable, embedding_max_attempts=2)
    with pytest.raises(IndexingError) as caught:
        await client.embed_documents([prepared("0")])

    assert calls == 2
    assert caught.value.code == "embedding_service_unavailable"
    assert caught.value.retryable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"bad": "shape"}, "malformed_embedding_response"),
        (response_payload([]), "embedding_result_count_mismatch"),
        (response_payload(["0", "extra"]), "embedding_result_count_mismatch"),
        (response_payload(["0"], dimension=3), "embedding_model_mismatch"),
        (
            {**response_payload(["0"]), "results": [{"id": "wrong", "embedding": [0.0] * 768}]},
            "unexpected_embedding_result",
        ),
        (
            {
                **response_payload(["0"]),
                "results": [{"id": "0", "embedding": [float("nan")] * 768}],
            },
            "non_finite_embedding",
        ),
        (
            {
                **response_payload(["0"]),
                "results": [{"id": "0", "embedding": [0.0] * 768}],
            },
            "embedding_normalization_mismatch",
        ),
    ],
)
async def test_rejects_malformed_missing_extra_dimension_and_nonfinite_results(
    payload: dict[str, object], code: str
) -> None:
    client = client_for(
        lambda _: httpx.Response(
            200,
            content=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
        )
    )
    with pytest.raises(IndexingError) as caught:
        await client.embed_documents([prepared("0")])
    assert caught.value.code == code


@pytest.mark.asyncio
async def test_readiness_query_and_cancellation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/ready":
            return httpx.Response(
                200,
                json={
                    "status": "ready",
                    "model": "jinaai/jina-embeddings-v2-base-code",
                    "revision": "516f4baf13dec4ddddda8631e019b5737c8bc250",
                    "dimension": 768,
                },
            )
        return httpx.Response(200, json=response_payload(["query"]))

    client = client_for(handler)
    assert await client.is_ready()
    assert len(await client.embed_query(prepared("query"))) == 768

    async def cancelled(_: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    transport = httpx.MockTransport(cancelled)
    raw = httpx.AsyncClient(base_url="http://embedding.test", transport=transport)
    cancelled_client = EmbeddingServiceClient(make_settings(), client=raw)
    with pytest.raises(asyncio.CancelledError):
        await cancelled_client.embed_documents([prepared("0")])
