"""Bearer authentication and cookie-request origin enforcement."""

from typing import Annotated, cast

from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.tokens import AccessTokenError, TokenService
from app.core.config import Settings
from app.core.errors import APIError
from app.db.models.user import User
from app.db.session import Database
from app.schemas.errors import ErrorCode

bearer_scheme = HTTPBearer(auto_error=False)


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    """Authenticate a short-lived RepoLume bearer token and load its user."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message="Authentication is required",
        )
    tokens = cast(TokenService, request.app.state.token_service)
    database = cast(Database, request.app.state.database)
    try:
        user_id = tokens.decode_access_token(credentials.credentials)
    except AccessTokenError as error:
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message="Access token is invalid or expired",
        ) from error
    async with database.session() as session:
        user = await session.get(User, user_id)
    if user is None:
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message="Access token is invalid or expired",
        )
    return user


def require_cookie_request_origin(request: Request) -> None:
    """Reject CSRF-shaped refresh/logout requests before reading their cookie."""
    settings = cast(Settings, request.app.state.settings)
    origin = request.headers.get("Origin")
    allowed_origins = {str(item).rstrip("/") for item in settings.cors_origins}
    if origin is None or origin.rstrip("/") not in allowed_origins:
        raise APIError(
            status_code=status.HTTP_403_FORBIDDEN,
            code=ErrorCode.FORBIDDEN,
            message="Request origin is not allowed",
        )


CurrentUser = Annotated[User, Depends(current_user)]
CookieOrigin = Annotated[None, Depends(require_cookie_request_origin)]
