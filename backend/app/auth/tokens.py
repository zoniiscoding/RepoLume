"""RepoLume access, refresh, OAuth-state, and PKCE token primitives."""

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import Settings

ACCESS_TOKEN_ALGORITHM = "HS256"
ACCESS_TOKEN_AUDIENCE = "repolume-api"
ACCESS_TOKEN_ISSUER = "repolume"


class AccessTokenError(ValueError):
    """Raised when an access token is invalid or expired."""


@dataclass(frozen=True, slots=True)
class AccessToken:
    """Serialized short-lived access token and its lifetime."""

    value: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class OAuthCredentials:
    """Fresh OAuth state and PKCE values before their safe storage split."""

    state: str
    state_hash: str
    code_verifier: str
    code_verifier_hash: str
    code_challenge: str


class TokenService:
    """Create and validate application-owned authentication tokens."""

    def __init__(self, settings: Settings) -> None:
        self._access_secret = settings.access_token_secret.get_secret_value()
        self._hash_secret = settings.token_hash_secret.get_secret_value().encode()
        self.access_ttl_seconds = settings.access_token_ttl_seconds
        self.refresh_ttl_seconds = settings.refresh_token_ttl_seconds

    def hash_opaque_token(self, value: str) -> str:
        """Return a keyed one-way digest for a high-entropy opaque token."""
        return hmac.new(self._hash_secret, value.encode(), hashlib.sha256).hexdigest()

    def new_oauth_credentials(self) -> OAuthCredentials:
        """Create an OAuth state and S256 PKCE verifier/challenge pair."""
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        challenge_digest = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge = base64.urlsafe_b64encode(challenge_digest).rstrip(b"=").decode()
        return OAuthCredentials(
            state=state,
            state_hash=self.hash_opaque_token(state),
            code_verifier=code_verifier,
            code_verifier_hash=self.hash_opaque_token(code_verifier),
            code_challenge=code_challenge,
        )

    def new_refresh_token(self) -> tuple[str, str]:
        """Return a fresh opaque refresh token and only its persistable digest."""
        raw_token = secrets.token_urlsafe(48)
        return raw_token, self.hash_opaque_token(raw_token)

    def issue_access_token(
        self,
        user_id: uuid.UUID,
        *,
        now: datetime | None = None,
    ) -> AccessToken:
        """Issue a short-lived signed token for one RepoLume user."""
        issued_at = now or datetime.now(UTC)
        expires_at = issued_at + timedelta(seconds=self.access_ttl_seconds)
        claims: dict[str, Any] = {
            "aud": ACCESS_TOKEN_AUDIENCE,
            "exp": expires_at,
            "iat": issued_at,
            "iss": ACCESS_TOKEN_ISSUER,
            "jti": uuid.uuid4().hex,
            "sub": str(user_id),
            "type": "access",
        }
        value = jwt.encode(claims, self._access_secret, algorithm=ACCESS_TOKEN_ALGORITHM)
        return AccessToken(value=value, expires_in=self.access_ttl_seconds)

    def decode_access_token(self, value: str) -> uuid.UUID:
        """Validate a RepoLume access token and return its user ID."""
        try:
            claims = jwt.decode(
                value,
                self._access_secret,
                algorithms=[ACCESS_TOKEN_ALGORITHM],
                audience=ACCESS_TOKEN_AUDIENCE,
                issuer=ACCESS_TOKEN_ISSUER,
                options={"require": ["aud", "exp", "iat", "iss", "jti", "sub", "type"]},
            )
            if claims.get("type") != "access":
                raise AccessTokenError
            return uuid.UUID(str(claims["sub"]))
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as error:
            raise AccessTokenError from error
