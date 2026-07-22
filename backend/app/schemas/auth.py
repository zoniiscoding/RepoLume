"""Authentication API response contracts."""

import uuid

from pydantic import BaseModel, ConfigDict


class AccessTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    github_user_id: int | None
    github_login: str | None
    display_name: str | None
    avatar_url: str | None
    email: str | None
    linked_providers: list[str]


class AuthenticationResponse(AccessTokenResponse):
    user: UserResponse
