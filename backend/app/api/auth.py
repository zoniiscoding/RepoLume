"""Multi-provider OAuth and RepoLume session endpoints."""

from typing import Annotated, cast

from fastapi import APIRouter, Cookie, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.auth.cookies import (
    OIDC_NONCE_COOKIE_NAME,
    PKCE_COOKIE_NAME,
    clear_oidc_nonce_cookie,
    clear_pkce_cookie,
    clear_refresh_cookie,
    set_oidc_nonce_cookie,
    set_pkce_cookie,
    set_refresh_cookie,
)
from app.auth.dependencies import CookieOrigin, CurrentUser
from app.auth.google import GoogleOIDCClientProtocol, GoogleOIDCError
from app.auth.tokens import TokenService
from app.core.config import Settings
from app.core.errors import APIError
from app.db.models.enums import AuthProvider
from app.db.models.user import User
from app.db.session import Database
from app.github.client import GitHubAPIError, GitHubClientProtocol
from app.schemas.auth import AccessTokenResponse, AuthenticationResponse, UserResponse
from app.schemas.errors import ErrorCode
from app.services.auth import (
    AccountLinkRequiredError,
    AuthenticationResult,
    AuthService,
    IdentityConflictError,
    OAuthStart,
    OAuthStateError,
    RefreshTokenError,
    RefreshTokenReuseError,
)

router = APIRouter()


def _service(request: Request) -> AuthService:
    return AuthService(
        database=cast(Database, request.app.state.database),
        github=cast(GitHubClientProtocol, request.app.state.github_client),
        google=cast(GoogleOIDCClientProtocol, request.app.state.google_client),
        tokens=cast(TokenService, request.app.state.token_service),
        settings=cast(Settings, request.app.state.settings),
    )


def _user_response(user: User, providers: tuple[AuthProvider, ...]) -> UserResponse:
    return UserResponse(
        id=user.id,
        github_user_id=user.github_user_id,
        github_login=user.github_login,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        email=user.email,
        linked_providers=[provider.value for provider in providers],
    )


def _authentication_response(
    result: AuthenticationResult, providers: tuple[AuthProvider, ...]
) -> AuthenticationResponse:
    return AuthenticationResponse(
        access_token=result.access_token.value,
        expires_in=result.access_token.expires_in,
        user=_user_response(result.user, providers),
    )


def _oauth_start_response(result: OAuthStart, request: Request) -> RedirectResponse:
    settings = cast(Settings, request.app.state.settings)
    response = RedirectResponse(
        result.authorization_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )
    set_pkce_cookie(response, result.credentials.code_verifier, settings)
    if result.nonce is not None:
        set_oidc_nonce_cookie(response, result.nonce, settings)
    return response


@router.get("/github/start", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
async def github_start(request: Request) -> RedirectResponse:
    result = await _service(request).start_oauth(provider=AuthProvider.GITHUB)
    return _oauth_start_response(result, request)


@router.get("/google/start", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
async def google_start(request: Request) -> RedirectResponse:
    settings = cast(Settings, request.app.state.settings)
    if not settings.google_auth_enabled:
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Google authentication is not configured",
        )
    result = await _service(request).start_oauth(provider=AuthProvider.GOOGLE)
    return _oauth_start_response(result, request)


@router.get("/link/{provider}/start", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
async def link_provider_start(
    provider: AuthProvider, request: Request, user: CurrentUser
) -> RedirectResponse:
    settings = cast(Settings, request.app.state.settings)
    if provider is AuthProvider.GOOGLE and not settings.google_auth_enabled:
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Google authentication is not configured",
        )
    result = await _service(request).start_oauth(provider=provider, linking_user_id=user.id)
    return _oauth_start_response(result, request)


def _callback_response(
    result: AuthenticationResult,
    providers: tuple[AuthProvider, ...],
    settings: Settings,
) -> Response:
    if settings.frontend_url is not None:
        response: Response = RedirectResponse(
            url=f"{str(settings.frontend_url).rstrip('/')}/auth/callback",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    else:
        response = Response(
            content=_authentication_response(result, providers).model_dump_json(),
            media_type="application/json",
        )
    clear_pkce_cookie(response, settings)
    clear_oidc_nonce_cookie(response, settings)
    set_refresh_cookie(response, result.refresh_token, settings)
    return response


def _oauth_identity_error(error: Exception) -> APIError:
    if isinstance(error, AccountLinkRequiredError):
        return APIError(
            status_code=status.HTTP_409_CONFLICT,
            code=ErrorCode.IDENTITY_LINK_REQUIRED,
            message="Sign in with your existing method, then link this provider in settings",
        )
    return APIError(
        status_code=status.HTTP_409_CONFLICT,
        code=ErrorCode.IDENTITY_CONFLICT,
        message="This identity cannot be linked to the current account",
    )


@router.get("/github/callback", response_model=AuthenticationResponse)
async def github_callback(
    request: Request,
    code: Annotated[str, Query(min_length=1, max_length=512)],
    state_value: Annotated[str, Query(alias="state", min_length=1, max_length=512)],
    code_verifier: Annotated[str | None, Cookie(alias=PKCE_COOKIE_NAME)] = None,
) -> Response:
    settings = cast(Settings, request.app.state.settings)
    try:
        result = await _service(request).authenticate_callback(
            code=code, state=state_value, code_verifier=code_verifier
        )
    except OAuthStateError as error:
        raise APIError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.OAUTH_STATE_INVALID,
            message="OAuth state is invalid or expired",
        ) from error
    except (AccountLinkRequiredError, IdentityConflictError) as error:
        raise _oauth_identity_error(error) from error
    except GitHubAPIError as error:
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="GitHub authentication is temporarily unavailable",
        ) from error
    providers = await _service(request).identity_providers(result.user.id)
    return _callback_response(result, providers, settings)


@router.get("/google/callback", response_model=AuthenticationResponse)
async def google_callback(
    request: Request,
    code: Annotated[str, Query(min_length=1, max_length=2048)],
    state_value: Annotated[str, Query(alias="state", min_length=1, max_length=512)],
    code_verifier: Annotated[str | None, Cookie(alias=PKCE_COOKIE_NAME)] = None,
    nonce: Annotated[str | None, Cookie(alias=OIDC_NONCE_COOKIE_NAME)] = None,
) -> Response:
    settings = cast(Settings, request.app.state.settings)
    try:
        result = await _service(request).authenticate_google_callback(
            code=code,
            state=state_value,
            code_verifier=code_verifier,
            nonce=nonce,
        )
    except OAuthStateError as error:
        raise APIError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.OAUTH_STATE_INVALID,
            message="OAuth state is invalid or expired",
        ) from error
    except (AccountLinkRequiredError, IdentityConflictError) as error:
        raise _oauth_identity_error(error) from error
    except GoogleOIDCError as error:
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Google authentication is temporarily unavailable",
        ) from error
    providers = await _service(request).identity_providers(result.user.id)
    return _callback_response(result, providers, settings)


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh_access_token(request: Request, origin: CookieOrigin) -> Response:
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
    del origin
    settings = cast(Settings, request.app.state.settings)
    await _service(request).logout(request.cookies.get(settings.refresh_cookie_name))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_refresh_cookie(response, settings)
    return response


@router.get("/me")
async def authenticated_user(request: Request, user: CurrentUser) -> UserResponse:
    providers = await _service(request).identity_providers(user.id)
    return _user_response(user, providers)
