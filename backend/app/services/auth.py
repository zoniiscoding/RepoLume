"""GitHub OAuth, user synchronization, and RepoLume token rotation."""

import hmac
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.google import GoogleIdentity, GoogleOIDCClientProtocol, GoogleOIDCError
from app.auth.tokens import AccessToken, OAuthCredentials, TokenService
from app.core.config import Settings
from app.db.models.auth import AuthIdentity, OAuthState, RefreshToken
from app.db.models.enums import (
    AuthProvider,
    GitHubAccountType,
    InstallationMemberRole,
    InstallationStatus,
    OAuthFlow,
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


class AccountLinkRequiredError(ValueError):
    """A verified provider email matches another unlinked identity."""


class IdentityConflictError(ValueError):
    """The provider identity already belongs to another canonical user."""


@dataclass(frozen=True, slots=True)
class OAuthStart:
    authorization_url: str
    credentials: OAuthCredentials
    nonce: str | None = None


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
        google: GoogleOIDCClientProtocol | None = None,
    ) -> None:
        self._database = database
        self._github = github
        self._google = google
        self._tokens = tokens
        self._settings = settings

    async def start_oauth(
        self,
        *,
        provider: AuthProvider = AuthProvider.GITHUB,
        linking_user_id: uuid.UUID | None = None,
    ) -> OAuthStart:
        credentials = self._tokens.new_oauth_credentials()
        nonce = self._tokens.new_oidc_nonce() if provider is AuthProvider.GOOGLE else None
        now = datetime.now(UTC)
        async with self._database.session() as session:
            if linking_user_id is not None and await session.get(User, linking_user_id) is None:
                raise IdentityConflictError
            session.add(
                OAuthState(
                    state_hash=credentials.state_hash,
                    code_verifier_hash=credentials.code_verifier_hash,
                    provider=provider,
                    flow=OAuthFlow.LINK if linking_user_id is not None else OAuthFlow.SIGN_IN,
                    nonce_hash=(
                        self._tokens.hash_opaque_token(nonce) if nonce is not None else None
                    ),
                    intended_user_id=linking_user_id,
                    expires_at=now + timedelta(seconds=self._settings.oauth_state_ttl_seconds),
                )
            )
            await session.commit()
        if provider is AuthProvider.GOOGLE:
            if nonce is None:
                raise AssertionError("missing_oidc_nonce")
            if self._google is None:
                raise GoogleOIDCError
            authorization_url = self._google.authorization_url(
                state=credentials.state,
                code_challenge=credentials.code_challenge,
                nonce=nonce,
            )
        else:
            authorization_url = self._github.authorization_url(
                state=credentials.state,
                code_challenge=credentials.code_challenge,
            )
        return OAuthStart(
            authorization_url=authorization_url,
            credentials=credentials,
            nonce=nonce,
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
        oauth_state = await self._consume_oauth_state(
            state=state,
            code_verifier=code_verifier,
            provider=AuthProvider.GITHUB,
        )
        github_token = await self._github.exchange_code(code=code, code_verifier=code_verifier)
        github_user = await self._github.get_authenticated_user(github_token)
        installations = await self._github.list_user_installations(github_token)
        return await self._synchronize_github_login(
            github_user,
            installations,
            linking_user_id=oauth_state.intended_user_id,
        )

    async def authenticate_google_callback(
        self,
        *,
        code: str,
        state: str,
        code_verifier: str | None,
        nonce: str | None,
    ) -> AuthenticationResult:
        if code_verifier is None or nonce is None:
            raise OAuthStateError
        oauth_state = await self._consume_oauth_state(
            state=state,
            code_verifier=code_verifier,
            provider=AuthProvider.GOOGLE,
            nonce=nonce,
        )
        if self._google is None:
            raise GoogleOIDCError
        identity = await self._google.authenticate(
            code=code,
            code_verifier=code_verifier,
            expected_nonce=nonce,
        )
        return await self._synchronize_google_login(
            identity,
            linking_user_id=oauth_state.intended_user_id,
        )

    async def _consume_oauth_state(
        self,
        *,
        state: str,
        code_verifier: str | None,
        provider: AuthProvider,
        nonce: str | None = None,
    ) -> OAuthState:
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
                or record.provider != provider
                or record.used_at is not None
                or record.expires_at <= now
                or not hmac.compare_digest(record.code_verifier_hash, verifier_hash)
            ):
                raise OAuthStateError
            if record.nonce_hash is not None:
                if nonce is None or not hmac.compare_digest(
                    record.nonce_hash, self._tokens.hash_opaque_token(nonce)
                ):
                    raise OAuthStateError
            elif nonce is not None:
                raise OAuthStateError
            record.used_at = now
            await session.commit()
            return record

    async def _synchronize_github_login(
        self,
        github_user: GitHubUser,
        installations: Sequence[GitHubInstallationData],
        *,
        linking_user_id: uuid.UUID | None,
    ) -> AuthenticationResult:
        now = datetime.now(UTC)
        async with self._database.session() as session:
            user = await self._resolve_identity(
                session,
                provider=AuthProvider.GITHUB,
                provider_subject=str(github_user.id),
                provider_email=github_user.email,
                email_verified=False,
                display_name=github_user.name,
                avatar_url=github_user.avatar_url,
                linking_user_id=linking_user_id,
                legacy_github_user_id=github_user.id,
            )
            user.github_user_id = github_user.id
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

            result = await self._issue_session(session, user, now)
            await session.commit()
        return result

    async def _synchronize_google_login(
        self,
        identity: GoogleIdentity,
        *,
        linking_user_id: uuid.UUID | None,
    ) -> AuthenticationResult:
        now = datetime.now(UTC)
        async with self._database.session() as session:
            user = await self._resolve_identity(
                session,
                provider=AuthProvider.GOOGLE,
                provider_subject=identity.subject,
                provider_email=identity.email,
                email_verified=identity.email_verified,
                display_name=identity.display_name,
                avatar_url=identity.avatar_url,
                linking_user_id=linking_user_id,
            )
            user.display_name = identity.display_name or user.display_name
            user.avatar_url = identity.avatar_url or user.avatar_url
            user.email = identity.email
            user.last_login_at = now
            result = await self._issue_session(session, user, now)
            await session.commit()
        return result

    async def _resolve_identity(
        self,
        session: AsyncSession,
        *,
        provider: AuthProvider,
        provider_subject: str,
        provider_email: str | None,
        email_verified: bool,
        display_name: str | None,
        avatar_url: str | None,
        linking_user_id: uuid.UUID | None,
        legacy_github_user_id: int | None = None,
    ) -> User:
        identity = await session.scalar(
            select(AuthIdentity).where(
                AuthIdentity.provider == provider,
                AuthIdentity.provider_subject == provider_subject,
            )
        )
        if identity is not None:
            if linking_user_id is not None and identity.user_id != linking_user_id:
                raise IdentityConflictError
            user = await session.get(User, identity.user_id)
            if user is None:
                raise IdentityConflictError
            identity.provider_email = provider_email
            identity.email_verified = email_verified
            return user

        user = await session.get(User, linking_user_id) if linking_user_id is not None else None
        if user is None and legacy_github_user_id is not None:
            user = await session.scalar(
                select(User).where(User.github_user_id == legacy_github_user_id)
            )
        if user is None and email_verified and provider_email is not None:
            matching_identity = await session.scalar(
                select(AuthIdentity).where(
                    AuthIdentity.provider_email == provider_email,
                    AuthIdentity.email_verified.is_(True),
                )
            )
            if matching_identity is not None:
                raise AccountLinkRequiredError
        if user is None:
            user = User(display_name=display_name, avatar_url=avatar_url, email=provider_email)
            session.add(user)
            await session.flush()
        session.add(
            AuthIdentity(
                user_id=user.id,
                provider=provider,
                provider_subject=provider_subject,
                provider_email=provider_email,
                email_verified=email_verified,
            )
        )
        await session.flush()
        return user

    async def _issue_session(
        self, session: AsyncSession, user: User, now: datetime
    ) -> AuthenticationResult:
        raw_refresh, refresh_hash = self._tokens.new_refresh_token()
        session.add(
            RefreshToken(
                family_id=uuid.uuid4(),
                user_id=user.id,
                token_hash=refresh_hash,
                expires_at=now + timedelta(seconds=self._tokens.refresh_ttl_seconds),
            )
        )
        await session.flush()
        return AuthenticationResult(
            user=user,
            access_token=self._tokens.issue_access_token(user.id, now=now),
            refresh_token=raw_refresh,
        )

    async def identity_providers(self, user_id: uuid.UUID) -> tuple[AuthProvider, ...]:
        async with self._database.session() as session:
            result = await session.scalars(
                select(AuthIdentity.provider)
                .where(AuthIdentity.user_id == user_id)
                .order_by(AuthIdentity.provider)
            )
            return tuple(result.all())

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
