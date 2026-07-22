"""Fixed-endpoint Google OpenID Connect client with strict ID-token validation."""

import hmac
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.core.config import Settings

GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class GoogleOIDCError(RuntimeError):
    """A content-free Google OIDC validation or dependency failure."""


class GoogleIdentity(BaseModel):
    """Validated immutable Google identity claims used by RepoLume."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    email_verified: bool
    display_name: str | None = Field(default=None, max_length=255)
    avatar_url: str | None = Field(default=None, max_length=2048)


class _GoogleTokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id_token: SecretStr


class GoogleOIDCClientProtocol(Protocol):
    def authorization_url(self, *, state: str, code_challenge: str, nonce: str) -> str: ...

    async def authenticate(
        self, *, code: str, code_verifier: str, expected_nonce: str
    ) -> GoogleIdentity: ...

    async def close(self) -> None: ...


class GoogleOIDCClient:
    """Perform only Google's code exchange and local ID-token verification."""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.public_github_timeout_seconds),
            follow_redirects=False,
        )
        self._owns_http_client = http_client is None

    def authorization_url(self, *, state: str, code_challenge: str, nonce: str) -> str:
        callback = self._settings.google_oauth_callback_url
        if not self._settings.google_auth_enabled or callback is None:
            raise GoogleOIDCError
        query = urlencode(
            {
                "client_id": self._settings.google_client_id,
                "redirect_uri": str(callback),
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return f"{GOOGLE_AUTHORIZATION_URL}?{query}"

    async def authenticate(
        self, *, code: str, code_verifier: str, expected_nonce: str
    ) -> GoogleIdentity:
        callback = self._settings.google_oauth_callback_url
        if not self._settings.google_auth_enabled or callback is None:
            raise GoogleOIDCError
        try:
            response = await self._http.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": self._settings.google_client_id,
                    "client_secret": self._settings.google_client_secret.get_secret_value(),
                    "code": code,
                    "code_verifier": code_verifier,
                    "grant_type": "authorization_code",
                    "redirect_uri": str(callback),
                },
            )
            response.raise_for_status()
            token = _GoogleTokenResponse.model_validate(response.json()).id_token
            jwks_response = await self._http.get(GOOGLE_JWKS_URL)
            jwks_response.raise_for_status()
            jwks = jwks_response.json()
            claims = self._decode_id_token(token, jwks)
        except (
            httpx.HTTPError,
            ValueError,
            TypeError,
            KeyError,
            ValidationError,
            jwt.InvalidTokenError,
        ) as error:
            raise GoogleOIDCError from error
        nonce = claims.get("nonce")
        issuer = claims.get("iss")
        if (
            not isinstance(nonce, str)
            or not hmac.compare_digest(nonce, expected_nonce)
            or issuer not in GOOGLE_ISSUERS
            or claims.get("email_verified") is not True
        ):
            raise GoogleOIDCError
        try:
            return GoogleIdentity(
                subject=str(claims["sub"]),
                email=str(claims["email"]),
                email_verified=True,
                display_name=claims.get("name"),
                avatar_url=claims.get("picture"),
            )
        except (KeyError, ValidationError) as error:
            raise GoogleOIDCError from error

    def _decode_id_token(self, token: SecretStr, jwks: Any) -> dict[str, Any]:
        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
            raise GoogleOIDCError
        header = jwt.get_unverified_header(token.get_secret_value())
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str):
            raise GoogleOIDCError
        key_data = next(
            (
                item
                for item in jwks["keys"]
                if isinstance(item, dict) and item.get("kid") == kid and item.get("alg") == "RS256"
            ),
            None,
        )
        if key_data is None:
            raise GoogleOIDCError
        key = jwt.PyJWK.from_dict(key_data).key
        claims = jwt.decode(
            token.get_secret_value(),
            key=key,
            algorithms=["RS256"],
            audience=self._settings.google_client_id,
            options={"require": ["aud", "exp", "iat", "iss", "nonce", "sub", "email"]},
        )
        return dict(claims)

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()
