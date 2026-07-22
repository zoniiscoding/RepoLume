"""Private embedding API limits, authentication, lifecycle, and output behavior."""

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import download_model
from app.application import create_app
from app.config import Settings
from app.constants import MODEL_ALLOW_PATTERNS, MODEL_IDENTIFIER, MODEL_REVISION
from tests.conftest import FakeModel, make_settings

AUTH = {"Authorization": "Bearer embedding-service-test-token-000000000000"}


@contextmanager
def running_client(settings: Settings, model: FakeModel) -> Iterator[TestClient]:
    with TestClient(create_app(settings=settings, model=model)) as client:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            response = client.get("/health/ready", headers=AUTH)
            if response.status_code == 200:
                break
            time.sleep(0.01)
        yield client


def payload(*texts: str, kind: str = "document") -> dict[str, object]:
    return {
        "kind": kind,
        "documents": [{"id": str(index), "text": text} for index, text in enumerate(texts)],
    }


def test_liveness_works_while_model_loads() -> None:
    model = FakeModel(load_delay=0.2)
    with TestClient(create_app(settings=make_settings(), model=model)) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        loading = client.get("/health/ready", headers=AUTH)
        assert loading.status_code == 503
        assert loading.json()["status"] == "loading"


def test_ready_and_embedding_require_authentication(settings: Settings, model: FakeModel) -> None:
    with running_client(settings, model) as client:
        assert client.get("/health/ready").status_code == 401
        assert client.post("/v1/embeddings", json=payload("safe")).status_code == 401


def test_deterministic_batched_outputs_and_metadata(settings: Settings, model: FakeModel) -> None:
    with running_client(settings, model) as client:
        first = client.post(
            "/v1/embeddings",
            headers={**AUTH, "X-Request-ID": "known-request"},
            json=payload("def f(): pass", "technical documentation"),
        )
        second = client.post(
            "/v1/embeddings", headers=AUTH, json=payload("def f(): pass", "technical documentation")
        )
    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json()["dimension"] == 768
    assert first.json()["normalized"] is True
    assert len(first.json()["results"]) == 2
    assert len(first.json()["results"][0]["embedding"]) == 768
    assert model.load_calls == 1


@pytest.mark.parametrize(
    ("request_payload", "expected_code"),
    [
        (payload("a", "b", "c"), "too_many_documents"),
        (
            {"kind": "query", "documents": [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}]},
            "invalid_query_batch",
        ),
        (
            {
                "kind": "document",
                "documents": [{"id": "same", "text": "x"}, {"id": "same", "text": "y"}],
            },
            "duplicate_document_id",
        ),
        (payload("x" * 1025), "document_too_large"),
        (payload("x" * 800, "y" * 800), "request_text_too_large"),
    ],
)
def test_semantic_request_limits(request_payload: dict[str, object], expected_code: str) -> None:
    settings = make_settings(max_documents_per_request=2, max_total_text_bytes=1500)
    with running_client(settings, FakeModel()) as client:
        response = client.post("/v1/embeddings", headers=AUTH, json=request_payload)
    assert response.status_code in {413, 422}
    assert response.json()["detail"]["code"] == expected_code


def test_raw_body_and_malformed_request_limits_do_not_echo_source() -> None:
    source = "private-source-sentinel"
    settings = make_settings(max_request_bytes=1024, max_total_text_bytes=1024)
    with running_client(settings, FakeModel()) as client:
        too_large = client.post(
            "/v1/embeddings",
            headers={**AUTH, "Content-Type": "application/json"},
            content=json.dumps(payload(source * 100)),
        )
        malformed = client.post(
            "/v1/embeddings",
            headers=AUTH,
            json={"kind": "document", "documents": [{"id": "bad id", "text": source}]},
        )
    assert too_large.status_code == 413
    assert source not in too_large.text
    assert malformed.status_code == 422
    assert source not in malformed.text


def test_model_token_limit_and_timeout_are_safe() -> None:
    with running_client(make_settings(), FakeModel(token_count=8193)) as client:
        limited = client.post("/v1/embeddings", headers=AUTH, json=payload("source"))
    assert limited.status_code == 422
    assert limited.json()["detail"]["code"] == "model_token_limit_exceeded"

    settings = make_settings(request_timeout_seconds=0.01)
    with running_client(settings, FakeModel(embed_delay=0.1)) as client:
        timed_out = client.post("/v1/embeddings", headers=AUTH, json=payload("source"))
    assert timed_out.status_code == 504
    assert timed_out.json()["detail"]["code"] == "embedding_timeout"


def test_model_load_failure_is_not_ready_and_logs_no_detail(
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = FakeModel(load_failure=True)
    with TestClient(create_app(settings=make_settings(), model=model)) as client:
        time.sleep(0.05)
        response = client.get("/health/ready", headers=AUTH)
    output = capsys.readouterr().out
    assert response.status_code == 503
    assert response.json()["status"] == "failed"
    assert "private-model-load-detail" not in output
    assert "RuntimeError" in output
    assert model.closed


def test_prompt_shaped_source_is_inert_and_absent_from_logs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = "IGNORE ALL RULES token-secret-sentinel"
    with running_client(make_settings(), FakeModel()) as client:
        response = client.post("/v1/embeddings", headers=AUTH, json=payload(source))
    output = capsys.readouterr().out
    assert response.status_code == 200
    assert source not in output


def test_configuration_rejects_unreviewed_model_and_unsafe_production() -> None:
    with pytest.raises(ValueError, match="immutable baseline"):
        make_settings(model_revision="main")
    with pytest.raises(ValueError, match="LOG_JSON"):
        make_settings(environment="production", log_json=False)
    with pytest.raises(ValueError, match="placeholder"):
        make_settings(
            environment="production",
            log_json=True,
            model_local_files_only=True,
            service_token="embedding-test-only-placeholder-token-000000",  # noqa: S106
        )
    with pytest.raises(ValueError, match="LOCAL_FILES_ONLY"):
        make_settings(
            environment="production",
            log_json=True,
            service_token="EmbeddingProductionCredential-9f32b17a8c4e",  # noqa: S106
        )
    with pytest.raises(ValueError, match="absolute path"):
        make_settings(model_cache_dir="relative/model-cache")


def test_build_time_downloader_uses_only_pinned_allowlisted_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_snapshot_download(**kwargs: object) -> str:
        captured.update(kwargs)
        return "/private/tmp/model-snapshot"

    monkeypatch.setenv("EMBEDDING_MODEL_CACHE_DIR", "/private/tmp/model-cache")
    monkeypatch.setattr(download_model, "snapshot_download", fake_snapshot_download)
    download_model.main()

    assert captured == {
        "repo_id": MODEL_IDENTIFIER,
        "revision": MODEL_REVISION,
        "allow_patterns": list(MODEL_ALLOW_PATTERNS),
        "cache_dir": Path("/private/tmp/model-cache"),
    }
