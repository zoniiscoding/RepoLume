"""GitHub OAuth, user synchronization, and RepoLume token rotation."""

import hmac
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import AccessToken, OAuthCredentials, TokenService
from app.core.config import Settings
from app.db.models.auth import OAuthState, RefreshToken
from app.db.models.enums import (
    GitHubAccountType,
    InstallationMemberRole,
    InstallationStatus,
    RepositorySelection,
)
from app.db.models.github_installation import GitHubInstallation, InstallationMember
from app.db.models.user import User
from app.db.session import Database
from app.github.client import GitHubClientProtocol
from app.github.schemas import GitHubInstallation as GitHubInstallationData
from app.github.schemas import GitHubUser


class OAuthStateError(ValueError):
    """Raised for missing, expired, mismatched, or reused OAuth state."""


class RefreshTokenError(ValueError):
    """Raised for an absent, invalid, revoked, or expired refresh token."""


class RefreshTokenReuseError(RefreshTokenError):
    """Raised after invalidating a replayed token's entire family."""


@dataclass(frozen=True, slots=True)
class OAuthStart:
    authorization_url: str
    credentials: OAuthCredentials


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    user: User
    access_token: AccessToken
    refresh_token: str


class AuthService:
    """Coordinate OAuth without persisting any GitHub credential."""

    def __init__(
        self,
        database: Database,
        github: GitHubClientProtocol,
        tokens: TokenService,
        settings: Settings,
    ) -> None:
        self._database = database
        self._github = github
        self._tokens = tokens
        self._settings = settings

    async def start_oauth(self) -> OAuthStart:
        credentials = self._tokens.new_oauth_credentials()
        now = datetime.now(UTC)
        async with self._database.session() as session:
            session.add(
                OAuthState(
                    state_hash=credentials.state_hash,
                    code_verifier_hash=credentials.code_verifier_hash,
                    expires_at=now + timedelta(seconds=self._settings.oauth_state_ttl_seconds),
                )
            )
            await session.commit()
        return OAuthStart(
            authorization_url=self._github.authorization_url(
                state=credentials.state,
                code_challenge=credentials.code_challenge,
            ),
            credentials=credentials,
        )

    async def authenticate_callback(
        self,
        *,
        code: str,
        state: str,
        code_verifier: str | None,
    ) -> AuthenticationResult:
        if code_verifier is None:
            raise OAuthStateError
        await self._consume_oauth_state(state=state, code_verifier=code_verifier)
        github_token = await self._github.exchange_code(code=code, code_verifier=code_verifier)
        github_user = await self._github.get_authenticated_user(github_token)
        installations = await self._github.list_user_installations(github_token)
        return await self._synchronize_login(github_user, installations)

    async def _consume_oauth_state(self, *, state: str, code_verifier: str | None) -> None:
        if not state or not code_verifier:
            raise OAuthStateError
        now = datetime.now(UTC)
        state_hash = self._tokens.hash_opaque_token(state)
        verifier_hash = self._tokens.hash_opaque_token(code_verifier)
        async with self._database.session() as session:
            record = await session.scalar(
                select(OAuthState).where(OAuthState.state_hash == state_hash).with_for_update()
            )
            if (
                record is None
                or record.used_at is not None
                or record.expires_at <= now
                or not hmac.compare_digest(record.code_verifier_hash, verifier_hash)
            ):
                raise OAuthStateError
            record.used_at = now
            await session.commit()

    async def _synchronize_login(
        self,
        github_user: GitHubUser,
        installations: Sequence[GitHubInstallationData],
    ) -> AuthenticationResult:
        now = datetime.now(UTC)
        raw_refresh, refresh_hash = self._tokens.new_refresh_token()
        family_id = uuid.uuid4()
        async with self._database.session() as session:
            user = await session.scalar(select(User).where(User.github_user_id == github_user.id))
            if user is None:
                user = User(github_user_id=github_user.id, github_login=github_user.login)
                session.add(user)
            user.github_login = github_user.login
            user.display_name = github_user.name
            user.avatar_url = github_user.avatar_url
            user.email = github_user.email
            user.last_login_at = now
            await session.flush()

            synchronized_ids: list[uuid.UUID] = []
            for external in installations:
                installation = await self._upsert_installation(session, user, external, now)
                synchronized_ids.append(installation.id)

            membership_delete = delete(InstallationMember).where(
                InstallationMember.user_id == user.id
            )
            if synchronized_ids:
                membership_delete = membership_delete.where(
                    InstallationMember.installation_id.not_in(synchronized_ids)
                )
            await session.execute(membership_delete)

            session.add(
                RefreshToken(
                    family_id=family_id,
                    user_id=user.id,
                    token_hash=refresh_hash,
                    expires_at=now + timedelta(seconds=self._tokens.refresh_ttl_seconds),
                )
            )
            await session.commit()

        return AuthenticationResult(
            user=user,
            access_token=self._tokens.issue_access_token(user.id, now=now),
            refresh_token=raw_refresh,
        )

    async def _upsert_installation(
        self,
        session: AsyncSession,
        user: User,
        external: GitHubInstallationData,
        now: datetime,
    ) -> GitHubInstallation:
        installation = await session.scalar(
            select(GitHubInstallation).where(
                GitHubInstallation.github_installation_id == external.id
            )
        )
        account_type = (
            GitHubAccountType.USER
            if external.account.type == "User"
            else GitHubAccountType.ORGANIZATION
        )
        if installation is None:
            installation = GitHubInstallation(
                github_installation_id=external.id,
                account_type=account_type,
                account_github_id=external.account.id,
                account_login=external.account.login,
                repository_selection=RepositorySelection(external.repository_selection),
            )
            session.add(installation)
        installation.account_type = account_type
        installation.account_github_id = external.account.id
        installation.account_login = external.account.login
        installation.permissions_json = dict(external.permissions)
        installation.repository_selection = RepositorySelection(external.repository_selection)
        installation.suspended_at = external.suspended_at
        installation.deleted_at = None
        installation.status = (
            InstallationStatus.SUSPENDED
            if external.suspended_at is not None
            else InstallationStatus.ACTIVE
        )
        if external.account.type == "User" and external.account.id == user.github_user_id:
            installation.installed_by_user_id = user.id
        await session.flush()

        membership = await session.scalar(
            select(InstallationMember).where(
                InstallationMember.installation_id == installation.id,
                InstallationMember.user_id == user.id,
            )
        )
        role = (
            InstallationMemberRole.OWNER
            if external.account.type == "User" and external.account.id == user.github_user_id
            else InstallationMemberRole.MEMBER
        )
        if membership is None:
            membership = InstallationMember(
                installation_id=installation.id,
                user_id=user.id,
                role=role,
                verified_at=now,
            )
            session.add(membership)
        membership.role = role
        membership.verified_at = now
        return installation

    async def rotate_refresh_token(self, raw_token: str) -> AuthenticationResult:
        now = datetime.now(UTC)
        token_hash = self._tokens.hash_opaque_token(raw_token)
        async with self._database.session() as session:
            record = await session.scalar(
                select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
            )
            if record is None:
                raise RefreshTokenError
            if record.used_at is not None or record.revoked_at is not None:
                await session.execute(
                    update(RefreshToken)
                    .where(RefreshToken.family_id == record.family_id)
                    .values(revoked_at=now, revocation_reason="reuse_detected")
                )
                await session.commit()
                raise RefreshTokenReuseError
            if record.expires_at <= now:
                record.revoked_at = now
                record.revocation_reason = "expired"
                await session.commit()
                raise RefreshTokenError

            user = await session.get(User, record.user_id)
            if user is None:
                raise RefreshTokenError
            new_raw, new_hash = self._tokens.new_refresh_token()
            record.used_at = now
            record.revoked_at = now
            record.revocation_reason = "rotated"
            session.add(
                RefreshToken(
                    family_id=record.family_id,
                    user_id=record.user_id,
                    token_hash=new_hash,
                    parent_token_id=record.id,
                    expires_at=now + timedelta(seconds=self._tokens.refresh_ttl_seconds),
                )
            )
            await session.commit()

        return AuthenticationResult(
            user=user,
            access_token=self._tokens.issue_access_token(user.id, now=now),
            refresh_token=new_raw,
        )

    async def logout(self, raw_token: str | None) -> None:
        if raw_token is None:
            return
        now = datetime.now(UTC)
        token_hash = self._tokens.hash_opaque_token(raw_token)
        async with self._database.session() as session:
            record = await session.scalar(
                select(RefreshToken).where(RefreshToken.token_hash == token_hash)
            )
            if record is not None:
                await session.execute(
                    update(RefreshToken)
                    .where(RefreshToken.family_id == record.family_id)
                    .values(revoked_at=now, revocation_reason="logout")
                )
                await session.commit()
