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
    github_user_id: int
    github_login: str
    display_name: str | None
    avatar_url: str | None
    email: str | None


class AuthenticationResponse(AccessTokenResponse):
    user: UserResponse
