"""Google OIDC issuer, audience, expiry, nonce, and email verification tests."""

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.google import GoogleOIDCClient, GoogleOIDCError
from tests.conftest import make_settings


def _fixture(claim_overrides: dict[str, Any] | None = None) -> tuple[GoogleOIDCClient, str]:
    settings = make_settings(
        google_auth_enabled=True,
        google_client_id="google-client-id.apps.googleusercontent.com",
        google_client_secret="google-client-secret-for-tests-only-0000000",  # noqa: S106
        google_oauth_callback_url="http://testserver/api/v1/auth/google/callback",
    )
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private_key.public_key().public_numbers()

    def encoded(number: int) -> str:
        width = (number.bit_length() + 7) // 8
        return jwt.utils.base64url_encode(number.to_bytes(width, "big")).decode()

    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": settings.google_client_id,
        "sub": "google-subject-123",
        "email": "verified@example.test",
        "email_verified": True,
        "nonce": "expected-nonce",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "name": "Verified User",
    }
    claims.update(claim_overrides or {})
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "key-1"})
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "kid": "key-1",
                "n": encoded(numbers.n),
                "e": encoded(numbers.e),
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"id_token": token})
        return httpx.Response(200, json=jwks)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GoogleOIDCClient(settings, http), "expected-nonce"


@pytest.mark.asyncio
async def test_validates_complete_google_identity() -> None:
    client, nonce = _fixture()
    identity = await client.authenticate(
        code="one-time-code", code_verifier="verifier", expected_nonce=nonce
    )
    assert identity.subject == "google-subject-123"
    assert identity.email_verified is True
    await client.close()


def test_google_authorization_url_is_fixed_and_contains_state_nonce_and_pkce() -> None:
    client, _ = _fixture()

    url = client.authorization_url(
        state="one-time-state",
        code_challenge="pkce-challenge",
        nonce="one-time-nonce",
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.google.com"
    assert parsed.path == "/o/oauth2/v2/auth"
    assert query["state"] == ["one-time-state"]
    assert query["nonce"] == ["one-time-nonce"]
    assert query["code_challenge"] == ["pkce-challenge"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["openid email profile"]


@pytest.mark.asyncio
async def test_google_client_fails_closed_when_provider_is_disabled() -> None:
    disabled = GoogleOIDCClient(make_settings(google_auth_enabled=False))

    with pytest.raises(GoogleOIDCError):
        disabled.authorization_url(state="state", code_challenge="challenge", nonce="nonce")
    with pytest.raises(GoogleOIDCError):
        await disabled.authenticate(code="code", code_verifier="verifier", expected_nonce="nonce")

    await disabled.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "jwks",
    [
        {},
        {"keys": "not-a-list"},
        {"keys": []},
    ],
)
async def test_google_client_rejects_malformed_or_unmatched_jwks(jwks: object) -> None:
    settings = make_settings(
        google_auth_enabled=True,
        google_client_id="google-client-id.apps.googleusercontent.com",
        google_client_secret="google-client-secret-for-tests-only-0000000",  # noqa: S106
        google_oauth_callback_url="http://testserver/api/v1/auth/google/callback",
    )
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"id_token": "not-a-valid-jwt"}
                if request.url.path.endswith("/token")
                else jwks,
            )
        )
    )
    client = GoogleOIDCClient(settings, http)

    with pytest.raises(GoogleOIDCError):
        await client.authenticate(
            code="one-time-code", code_verifier="verifier", expected_nonce="nonce"
        )

    await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://evil.example"},
        {"aud": "another-client"},
        {
            "aud": ["google-client-id.apps.googleusercontent.com", "another-client"],
            "azp": "google-client-id.apps.googleusercontent.com",
        },
        {"azp": "another-client"},
        {"exp": datetime.now(UTC) - timedelta(seconds=1)},
        {"nonce": "mismatched-nonce"},
        {"email_verified": False},
    ],
)
async def test_rejects_invalid_oidc_security_claims(claims: dict[str, Any]) -> None:
    client, nonce = _fixture(claims)
    with pytest.raises(GoogleOIDCError):
        await client.authenticate(
            code="one-time-code", code_verifier="verifier", expected_nonce=nonce
        )
    await client.close()
