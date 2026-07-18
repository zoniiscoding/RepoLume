"""GitHub App OAuth and RepoLume session endpoints."""

from typing import Annotated, cast

from fastapi import APIRouter, Cookie, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.auth.cookies import (
    PKCE_COOKIE_NAME,
    clear_pkce_cookie,
    clear_refresh_cookie,
    set_pkce_cookie,
    set_refresh_cookie,
)
from app.auth.dependencies import CookieOrigin, CurrentUser
from app.auth.tokens import TokenService
from app.core.config import Settings
from app.core.errors import APIError
from app.db.models.user import User
from app.db.session import Database
from app.github.client import GitHubAPIError, GitHubClientProtocol
from app.schemas.auth import AccessTokenResponse, AuthenticationResponse, UserResponse
from app.schemas.errors import ErrorCode
from app.services.auth import (
    AuthenticationResult,
    AuthService,
    OAuthStateError,
    RefreshTokenError,
    RefreshTokenReuseError,
)

router = APIRouter()


def _service(request: Request) -> AuthService:
    return AuthService(
        database=cast(Database, request.app.state.database),
        github=cast(GitHubClientProtocol, request.app.state.github_client),
        tokens=cast(TokenService, request.app.state.token_service),
        settings=cast(Settings, request.app.state.settings),
    )


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        github_user_id=user.github_user_id,
        github_login=user.github_login,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        email=user.email,
    )


def _authentication_response(result: AuthenticationResult) -> AuthenticationResponse:
    return AuthenticationResponse(
        access_token=result.access_token.value,
        expires_in=result.access_token.expires_in,
        user=_user_response(result.user),
    )


@router.get("/github/start", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
async def github_start(request: Request) -> RedirectResponse:
    """Persist one-time state and redirect to GitHub with S256 PKCE."""
    result = await _service(request).start_oauth()
    response = RedirectResponse(
        result.authorization_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )
    set_pkce_cookie(response, result.credentials.code_verifier, request.app.state.settings)
    return response


@router.get("/github/callback", response_model=AuthenticationResponse)
async def github_callback(
    request: Request,
    code: Annotated[str, Query(min_length=1, max_length=512)],
    state_value: Annotated[str, Query(alias="state", min_length=1, max_length=512)],
    code_verifier: Annotated[str | None, Cookie(alias=PKCE_COOKIE_NAME)] = None,
) -> Response:
    """Consume state once, exchange the code server-side, and issue RepoLume tokens."""
    settings = cast(Settings, request.app.state.settings)
    try:
        result = await _service(request).authenticate_callback(
            code=code,
            state=state_value,
            code_verifier=code_verifier,
        )
    except OAuthStateError as error:
        raise APIError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.OAUTH_STATE_INVALID,
            message="OAuth state is invalid or expired",
        ) from error
    except GitHubAPIError as error:
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="GitHub authentication is temporarily unavailable",
        ) from error
    if settings.frontend_url is not None:
        response: Response = RedirectResponse(
            url=f"{str(settings.frontend_url).rstrip('/')}/auth/callback",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    else:
        response = Response(
            content=_authentication_response(result).model_dump_json(),
            media_type="application/json",
        )
    clear_pkce_cookie(response, settings)
    set_refresh_cookie(response, result.refresh_token, settings)
    return response


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh_access_token(
    request: Request,
    origin: CookieOrigin,
) -> Response:
    """Rotate a refresh token and reject reuse by invalidating its family."""
    del origin
    settings = cast(Settings, request.app.state.settings)
    raw_token = request.cookies.get(settings.refresh_cookie_name)
    if raw_token is None:
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message="Refresh token is missing",
        )
    try:
        result = await _service(request).rotate_refresh_token(raw_token)
    except RefreshTokenReuseError as error:
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.TOKEN_REUSE_DETECTED,
            message="Refresh token reuse was detected",
        ) from error
    except RefreshTokenError as error:
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message="Refresh token is invalid or expired",
        ) from error
    response = Response(
        content=AccessTokenResponse(
            access_token=result.access_token.value,
            expires_in=result.access_token.expires_in,
        ).model_dump_json(),
        media_type="application/json",
    )
    set_refresh_cookie(response, result.refresh_token, settings)
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, origin: CookieOrigin) -> Response:
    """Invalidate the complete refresh-token family and clear the browser cookie."""
    del origin
    settings = cast(Settings, request.app.state.settings)
    await _service(request).logout(request.cookies.get(settings.refresh_cookie_name))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_refresh_cookie(response, settings)
    return response


@router.get("/me")
async def authenticated_user(user: CurrentUser) -> UserResponse:
    """Return the server-loaded identity for a valid RepoLume access token."""
    return _user_response(user)
