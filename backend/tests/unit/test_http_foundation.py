"""FastAPI health, request context, errors, and security headers."""

import re

import pytest
from fastapi import Query
from fastapi.testclient import TestClient

from app.application import create_app
from app.core.errors import APIError
from app.schemas.errors import ErrorCode
from tests.conftest import FakeDatabase, FakeJobQueue, FakeVectorReadiness, make_settings

REQUEST_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


def test_liveness_is_safe_and_has_request_id(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert REQUEST_ID_PATTERN.fullmatch(response.headers["x-request-id"])
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["permissions-policy"]
    assert "strict-transport-security" not in response.headers


def test_valid_client_request_id_is_preserved(client: TestClient) -> None:
    response = client.get(
        "/api/v1/health/live",
        headers={"X-Request-ID": "client-request_123"},
    )

    assert response.headers["x-request-id"] == "client-request_123"


@pytest.mark.parametrize("request_id", ["", "contains spaces", "x" * 65, "contains/slash"])
def test_invalid_client_request_id_is_replaced(
    client: TestClient,
    request_id: str,
) -> None:
    response = client.get(
        "/api/v1/health/live",
        headers={"X-Request-ID": request_id},
    )

    assert response.headers["x-request-id"] != request_id
    assert REQUEST_ID_PATTERN.fullmatch(response.headers["x-request-id"])


def test_readiness_reports_ready(client: TestClient) -> None:
    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ready", "redis": "ready", "qdrant": "ready"},
    }


def test_readiness_reports_unavailable_without_details() -> None:
    app = create_app(
        settings=make_settings(),
        database=FakeDatabase(ready=False),
        job_queue=FakeJobQueue(),
        vector_store=FakeVectorReadiness(),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "database": "unavailable",
            "redis": "ready",
            "qdrant": "ready",
        },
    }


def test_production_responses_include_hsts() -> None:
    settings = make_settings(
        app_env="production",
        database_url="postgresql+asyncpg://service:secret@db.example.com/repolume",
        log_json=True,
        docs_enabled=False,
        cors_origins=["https://app.repolume.example"],
        trusted_hosts=["api.repolume.example", "testserver"],
        github_oauth_callback_url="https://api.repolume.example/api/v1/auth/github/callback",
    )
    app = create_app(settings=settings, database=FakeDatabase())

    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")

    assert response.headers["strict-transport-security"].startswith("max-age=31536000")


def test_untrusted_host_is_rejected() -> None:
    app = create_app(
        settings=make_settings(trusted_hosts=["api.repolume.example"]),
        database=FakeDatabase(),
    )

    with TestClient(app, base_url="http://attacker.example") as client:
        response = client.get("/api/v1/health/live")

    assert response.status_code == 400


def test_cors_allows_only_configured_origin() -> None:
    app = create_app(
        settings=make_settings(cors_origins=["https://app.repolume.example"]),
        database=FakeDatabase(),
    )

    with TestClient(app) as client:
        allowed = client.options(
            "/api/v1/health/live",
            headers={
                "Origin": "https://app.repolume.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        denied = client.options(
            "/api/v1/health/live",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://app.repolume.example"
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_api_error_uses_shared_envelope() -> None:
    app = create_app(settings=make_settings(), database=FakeDatabase())

    @app.get("/test/conflict")
    async def conflict() -> None:
        raise APIError(
            status_code=409,
            code=ErrorCode.CONFLICT,
            message="The operation conflicts with current state",
        )

    with TestClient(app) as client:
        response = client.get("/test/conflict", headers={"X-Request-ID": "known-id"})

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "conflict",
            "message": "The operation conflicts with current state",
            "request_id": "known-id",
            "details": None,
        }
    }


def test_validation_error_does_not_echo_rejected_input() -> None:
    app = create_app(settings=make_settings(), database=FakeDatabase())

    @app.get("/test/validated")
    async def validated(value: int = Query()) -> dict[str, int]:
        return {"value": value}

    rejected_input = "private-input-sentinel"
    with TestClient(app) as client:
        response = client.get("/test/validated", params={"value": rejected_input})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert rejected_input not in response.text


def test_unhandled_error_hides_exception_message(capsys: pytest.CaptureFixture[str]) -> None:
    app = create_app(settings=make_settings(), database=FakeDatabase())
    secret = "exception-secret-sentinel"

    @app.get("/test/failure")
    async def failure() -> None:
        raise RuntimeError(secret)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test/failure")

    output = capsys.readouterr().out
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert secret not in response.text
    assert secret not in output
    assert "RuntimeError" in output


def test_not_found_ignores_framework_detail(client: TestClient) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert response.json()["error"]["message"] == "Not Found"


def test_github_session_and_webhook_routes_exist_when_google_is_disabled() -> None:
    app = create_app(
        settings=make_settings(google_auth_enabled=False),
        database=FakeDatabase(),
        job_queue=FakeJobQueue(),
        vector_store=FakeVectorReadiness(),
    )

    operations = {
        (method.upper(), path)
        for path, path_item in app.openapi()["paths"].items()
        for method in path_item
    }
    assert {
        ("GET", "/api/v1/auth/github/start"),
        ("GET", "/api/v1/auth/github/callback"),
        ("POST", "/api/v1/auth/refresh"),
        ("POST", "/api/v1/auth/logout"),
        ("POST", "/api/v1/webhooks/github"),
    } <= operations
