"""Security attributes for browser-held authentication cookies."""

from fastapi import Response

from app.auth.cookies import set_pkce_cookie, set_refresh_cookie
from app.core.config import AppEnvironment
from tests.conftest import make_settings


def test_production_refresh_cookie_is_secure_http_only_and_cross_site() -> None:
    settings = make_settings(
        app_env=AppEnvironment.PRODUCTION,
        database_url="postgresql+asyncpg://service:secret@db.example.com/repolume",
        log_json=True,
        docs_enabled=False,
        cors_origins=["https://app.repolume.example"],
        trusted_hosts=["api.repolume.example"],
        github_oauth_callback_url="https://api.repolume.example/api/v1/auth/github/callback",
    )
    response = Response()

    set_refresh_cookie(response, "test-refresh-token", settings)

    header = response.headers["set-cookie"]
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "SameSite=none" in header
    assert "Path=/api/v1/auth" in header


def test_pkce_cookie_is_scoped_http_only_and_lax() -> None:
    response = Response()

    set_pkce_cookie(response, "test-pkce-verifier", make_settings())

    header = response.headers["set-cookie"]
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/api/v1/auth" in header
    assert "Secure" not in header
