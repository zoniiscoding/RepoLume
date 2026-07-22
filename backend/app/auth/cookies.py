"""Browser cookie controls for rotating refresh tokens and OAuth PKCE."""

from fastapi import Response

from app.core.config import Settings

PKCE_COOKIE_NAME = "repolume_oauth_pkce"
OIDC_NONCE_COOKIE_NAME = "repolume_oidc_nonce"
AUTH_COOKIE_PATH = "/api/v1/auth"


def set_pkce_cookie(response: Response, verifier: str, settings: Settings) -> None:
    """Set the short-lived PKCE verifier without exposing it to JavaScript."""
    response.set_cookie(
        key=PKCE_COOKIE_NAME,
        value=verifier,
        max_age=settings.oauth_state_ttl_seconds,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path=AUTH_COOKIE_PATH,
    )


def clear_pkce_cookie(response: Response, settings: Settings) -> None:
    """Delete the PKCE verifier after any callback attempt."""
    response.delete_cookie(
        key=PKCE_COOKIE_NAME,
        secure=settings.is_production,
        httponly=True,
        samesite="lax",
        path=AUTH_COOKIE_PATH,
    )


def set_oidc_nonce_cookie(response: Response, nonce: str, settings: Settings) -> None:
    """Bind the Google callback to a nonce unavailable to browser scripts."""
    response.set_cookie(
        key=OIDC_NONCE_COOKIE_NAME,
        value=nonce,
        max_age=settings.oauth_state_ttl_seconds,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path=AUTH_COOKIE_PATH,
    )


def clear_oidc_nonce_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=OIDC_NONCE_COOKIE_NAME,
        secure=settings.is_production,
        httponly=True,
        samesite="lax",
        path=AUTH_COOKIE_PATH,
    )


def set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    """Set a scoped browser-only refresh token with environment-safe flags."""
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=True,
        secure=settings.is_production,
        samesite="none" if settings.is_production else "lax",
        path=AUTH_COOKIE_PATH,
    )


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    """Remove the browser refresh token on logout or rejected rotation."""
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        secure=settings.is_production,
        httponly=True,
        samesite="none" if settings.is_production else "lax",
        path=AUTH_COOKIE_PATH,
    )
