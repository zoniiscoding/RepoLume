"""Raw-body GitHub webhook validation, idempotency, and revocation handling."""

import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models.enums import (
    GitHubAccountType,
    InstallationMemberRole,
    InstallationStatus,
    RepositoryIndexingStatus,
    RepositorySelection,
    WebhookDeliveryStatus,
)
from app.db.models.github_installation import GitHubInstallation, InstallationMember
from app.db.models.repository import Repository
from app.db.models.user import User
from app.db.models.webhook_delivery import WebhookDelivery
from app.db.session import Database
from app.github.schemas import GitHubInstallation as GitHubInstallationData
from app.github.schemas import GitHubRepository, GitHubUser

DELIVERY_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,255}$")
EVENT_PATTERN = re.compile(r"^[a-z_]{1,64}$")
SUPPORTED_EVENTS = {
    "installation",
    "installation_repositories",
    "push",
    "repository",
}


class WebhookSignatureError(ValueError):
    """Raised before parsing when a webhook signature does not authenticate."""


class WebhookPayloadError(ValueError):
    """Raised for invalid headers or signed-but-malformed JSON."""


class GitHubRepositoryReference(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(gt=0)


class GitHubWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str | None = Field(default=None, max_length=64)
    installation: GitHubInstallationData | None = None
    sender: GitHubUser | None = None
    repository: GitHubRepository | None = None
    repositories_added: list[GitHubRepository] = Field(default_factory=list)
    repositories_removed: list[GitHubRepositoryReference] = Field(default_factory=list)


class WebhookService:
    """Apply only short, durable access-state transitions in the request."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self._database = database
        self._secret = settings.github_webhook_secret.get_secret_value().encode()

    def verify_signature(self, body: bytes, signature: str | None) -> None:
        """Authenticate the exact raw bytes with GitHub's HMAC-SHA256 contract."""
        if signature is None or not signature.startswith("sha256="):
            raise WebhookSignatureError
        expected = "sha256=" + hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise WebhookSignatureError

    async def handle(
        self,
        *,
        body: bytes,
        signature: str | None,
        delivery_id: str | None,
        event_name: str | None,
    ) -> Literal["accepted", "duplicate", "ignored"]:
        self.verify_signature(body, signature)
        if (
            delivery_id is None
            or DELIVERY_PATTERN.fullmatch(delivery_id) is None
            or event_name is None
            or EVENT_PATTERN.fullmatch(event_name) is None
        ):
            raise WebhookPayloadError
        try:
            payload = GitHubWebhookPayload.model_validate(json.loads(body))
        except (json.JSONDecodeError, TypeError, ValidationError) as error:
            raise WebhookPayloadError from error
        if event_name in SUPPORTED_EVENTS and payload.installation is None:
            raise WebhookPayloadError
        if event_name in {"push", "repository"} and payload.repository is None:
            raise WebhookPayloadError

        github_installation_id = payload.installation.id if payload.installation else None
        github_repository_id = payload.repository.id if payload.repository else None
        async with self._database.session() as session:
            inserted_id = await session.scalar(
                insert(WebhookDelivery)
                .values(
                    delivery_id=delivery_id,
                    event_name=event_name,
                    action=payload.action,
                    github_installation_id=github_installation_id,
                    github_repository_id=github_repository_id,
                    status=WebhookDeliveryStatus.RECEIVED,
                )
                .on_conflict_do_nothing(index_elements=[WebhookDelivery.delivery_id])
                .returning(WebhookDelivery.id)
            )
            if inserted_id is None:
                await session.commit()
                return "duplicate"

            delivery = await session.get(WebhookDelivery, inserted_id)
            if delivery is None:
                raise WebhookPayloadError
            if event_name not in SUPPORTED_EVENTS:
                delivery.status = WebhookDeliveryStatus.IGNORED
                delivery.processed_at = datetime.now(UTC)
                await session.commit()
                return "ignored"

            await self._apply_event(session, event_name, payload)
            if event_name in {"push"} or (
                event_name == "repository" and payload.action not in {"deleted"}
            ):
                delivery.status = WebhookDeliveryStatus.QUEUED
            else:
                delivery.status = WebhookDeliveryStatus.PROCESSED
                delivery.processed_at = datetime.now(UTC)
            await session.commit()
        return "accepted"

    async def _apply_event(
        self,
        session: AsyncSession,
        event_name: str,
        payload: GitHubWebhookPayload,
    ) -> None:
        if event_name == "installation" and payload.installation is not None:
            await self._apply_installation(session, payload)
        elif event_name == "installation_repositories" and payload.installation is not None:
            await self._apply_repository_access(session, payload)
        elif (
            event_name == "repository"
            and payload.action == "deleted"
            and payload.repository is not None
        ):
            await session.execute(
                update(Repository)
                .where(Repository.github_repository_id == payload.repository.id)
                .values(
                    access_revoked_at=datetime.now(UTC),
                    deleted_at=datetime.now(UTC),
                    indexing_status=RepositoryIndexingStatus.ACCESS_REVOKED,
                )
            )

    async def _apply_installation(
        self,
        session: AsyncSession,
        payload: GitHubWebhookPayload,
    ) -> None:
        external = payload.installation
        if external is None:
            return
        installation = await self._upsert_installation(session, external)
        now = datetime.now(UTC)
        if payload.action == "deleted":
            installation.status = InstallationStatus.DELETED
            installation.deleted_at = now
            await self._revoke_installation_repositories(session, installation.id, now)
        elif payload.action == "suspend":
            installation.status = InstallationStatus.SUSPENDED
            installation.suspended_at = external.suspended_at or now
            await self._revoke_installation_repositories(session, installation.id, now)
        elif payload.action in {"created", "unsuspend"}:
            installation.status = InstallationStatus.ACTIVE
            installation.suspended_at = None
            installation.deleted_at = None

        if payload.action == "created" and payload.sender is not None:
            user = await session.scalar(
                select(User).where(User.github_user_id == payload.sender.id)
            )
            if user is not None:
                membership = await session.scalar(
                    select(InstallationMember).where(
                        InstallationMember.installation_id == installation.id,
                        InstallationMember.user_id == user.id,
                    )
                )
                if membership is None:
                    session.add(
                        InstallationMember(
                            installation_id=installation.id,
                            user_id=user.id,
                            role=InstallationMemberRole.OWNER,
                            verified_at=now,
                        )
                    )

    async def _upsert_installation(
        self,
        session: AsyncSession,
        external: GitHubInstallationData,
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
        await session.flush()
        return installation

    async def _revoke_installation_repositories(
        self,
        session: AsyncSession,
        installation_id: uuid.UUID,
        now: datetime,
    ) -> None:
        await session.execute(
            update(Repository)
            .where(Repository.installation_id == installation_id)
            .values(
                access_revoked_at=now,
                indexing_status=RepositoryIndexingStatus.ACCESS_REVOKED,
            )
        )

    async def _apply_repository_access(
        self,
        session: AsyncSession,
        payload: GitHubWebhookPayload,
    ) -> None:
        external_installation = payload.installation
        if external_installation is None:
            return
        installation = await self._upsert_installation(session, external_installation)
        now = datetime.now(UTC)
        removed_ids = [repository.id for repository in payload.repositories_removed]
        if removed_ids:
            await session.execute(
                update(Repository)
                .where(
                    Repository.installation_id == installation.id,
                    Repository.github_repository_id.in_(removed_ids),
                )
                .values(
                    access_revoked_at=now,
                    indexing_status=RepositoryIndexingStatus.ACCESS_REVOKED,
                )
            )
        for external in payload.repositories_added:
            repository = await session.scalar(
                select(Repository).where(
                    Repository.installation_id == installation.id,
                    Repository.github_repository_id == external.id,
                )
            )
            if repository is None:
                repository = Repository(
                    installation_id=installation.id,
                    github_repository_id=external.id,
                    github_owner=external.owner.login,
                    github_name=external.name,
                    github_full_name=external.full_name,
                    github_url=external.html_url,
                    is_private=external.private,
                    default_branch=external.default_branch,
                )
                session.add(repository)
            repository.access_revoked_at = None
            repository.deleted_at = None
            if repository.indexing_status == RepositoryIndexingStatus.ACCESS_REVOKED:
                repository.indexing_status = RepositoryIndexingStatus.NOT_INDEXED
                repository.indexing_progress = 0
