"""Server-side installation/repository authorization and GitHub synchronization."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models.enums import InstallationStatus, RepositoryIndexingStatus
from app.db.models.github_installation import GitHubInstallation, InstallationMember
from app.db.models.repository import Repository
from app.db.session import Database
from app.github.client import GitHubClientProtocol
from app.github.schemas import GitHubRepository


class InstallationAccessError(LookupError):
    """Raised without revealing whether another tenant owns the resource."""


class InstallationService:
    """Authorize every lookup and keep repository access fail-closed."""

    def __init__(
        self,
        database: Database,
        github: GitHubClientProtocol,
        settings: Settings,
    ) -> None:
        self._database = database
        self._github = github
        self._membership_ttl = timedelta(seconds=settings.installation_membership_ttl_seconds)

    def _authorized_installation_query(
        self,
        *,
        user_id: uuid.UUID,
        installation_id: uuid.UUID | None = None,
    ) -> Select[tuple[GitHubInstallation]]:
        cutoff = datetime.now(UTC) - self._membership_ttl
        statement = (
            select(GitHubInstallation)
            .join(
                InstallationMember,
                InstallationMember.installation_id == GitHubInstallation.id,
            )
            .where(
                InstallationMember.user_id == user_id,
                InstallationMember.verified_at >= cutoff,
                GitHubInstallation.status == InstallationStatus.ACTIVE,
                GitHubInstallation.deleted_at.is_(None),
            )
        )
        if installation_id is not None:
            statement = statement.where(GitHubInstallation.id == installation_id)
        return statement

    async def list_authorized_installations(
        self, user_id: uuid.UUID
    ) -> Sequence[GitHubInstallation]:
        async with self._database.session() as session:
            result = await session.scalars(self._authorized_installation_query(user_id=user_id))
            return tuple(result.all())

    async def get_authorized_installation(
        self,
        *,
        user_id: uuid.UUID,
        installation_id: uuid.UUID,
    ) -> GitHubInstallation:
        async with self._database.session() as session:
            installation = await session.scalar(
                self._authorized_installation_query(
                    user_id=user_id,
                    installation_id=installation_id,
                )
            )
        if installation is None:
            raise InstallationAccessError
        return installation

    async def synchronize_repositories(
        self,
        *,
        user_id: uuid.UUID,
        installation_id: uuid.UUID,
    ) -> Sequence[Repository]:
        installation = await self.get_authorized_installation(
            user_id=user_id,
            installation_id=installation_id,
        )
        installation_token = await self._github.create_installation_token(
            installation.github_installation_id
        )
        external_repositories = await self._github.list_installation_repositories(
            installation_token
        )

        async with self._database.session() as session:
            reauthorized = await session.scalar(
                self._authorized_installation_query(
                    user_id=user_id,
                    installation_id=installation_id,
                )
            )
            if reauthorized is None:
                raise InstallationAccessError
            github_ids: list[int] = []
            for external in external_repositories:
                await self._upsert_repository(session, reauthorized.id, external)
                github_ids.append(external.id)

            revoke = update(Repository).where(
                Repository.installation_id == reauthorized.id,
                Repository.access_revoked_at.is_(None),
            )
            if github_ids:
                revoke = revoke.where(Repository.github_repository_id.not_in(github_ids))
            await session.execute(
                revoke.values(
                    access_revoked_at=datetime.now(UTC),
                    indexing_status=RepositoryIndexingStatus.ACCESS_REVOKED,
                )
            )
            await session.commit()

            result = await session.scalars(
                select(Repository)
                .where(
                    Repository.installation_id == reauthorized.id,
                    Repository.github_repository_id.in_(github_ids),
                    Repository.access_revoked_at.is_(None),
                    Repository.deleted_at.is_(None),
                )
                .order_by(Repository.github_full_name)
            )
            return tuple(result.all())

    async def _upsert_repository(
        self,
        session: AsyncSession,
        installation_id: uuid.UUID,
        external: GitHubRepository,
    ) -> Repository:
        repository = await session.scalar(
            select(Repository).where(
                Repository.installation_id == installation_id,
                Repository.github_repository_id == external.id,
            )
        )
        if repository is None:
            repository = Repository(
                installation_id=installation_id,
                github_repository_id=external.id,
                github_owner=external.owner.login,
                github_name=external.name,
                github_full_name=external.full_name,
                github_url=external.html_url,
                is_private=external.private,
                default_branch=external.default_branch,
            )
            session.add(repository)
        repository.github_owner = external.owner.login
        repository.github_name = external.name
        repository.github_full_name = external.full_name
        repository.github_url = external.html_url
        repository.is_private = external.private
        repository.default_branch = external.default_branch
        repository.primary_language = external.language
        repository.access_revoked_at = None
        repository.deleted_at = None
        if repository.indexing_status == RepositoryIndexingStatus.ACCESS_REVOKED:
            repository.indexing_status = RepositoryIndexingStatus.NOT_INDEXED
            repository.indexing_progress = 0
        await session.flush()
        return repository

    async def get_authorized_repository(
        self,
        *,
        user_id: uuid.UUID,
        repository_id: uuid.UUID,
    ) -> Repository:
        cutoff = datetime.now(UTC) - self._membership_ttl
        async with self._database.session() as session:
            repository = await session.scalar(
                select(Repository)
                .join(
                    GitHubInstallation,
                    GitHubInstallation.id == Repository.installation_id,
                )
                .join(
                    InstallationMember,
                    InstallationMember.installation_id == GitHubInstallation.id,
                )
                .where(
                    Repository.id == repository_id,
                    Repository.access_revoked_at.is_(None),
                    Repository.deleted_at.is_(None),
                    GitHubInstallation.status == InstallationStatus.ACTIVE,
                    GitHubInstallation.deleted_at.is_(None),
                    InstallationMember.user_id == user_id,
                    InstallationMember.verified_at >= cutoff,
                )
            )
        if repository is None:
            raise InstallationAccessError
        return repository
