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
    IndexingJobStatus,
    IndexingJobType,
    IndexingMode,
    InstallationMemberRole,
    InstallationStatus,
    RepositoryIndexingStatus,
    RepositorySelection,
    WebhookDeliveryStatus,
)
from app.db.models.github_installation import GitHubInstallation, InstallationMember
from app.db.models.indexing_job import IndexingJob
from app.db.models.repository import Repository
from app.db.models.user import User
from app.db.models.webhook_delivery import WebhookDelivery
from app.db.session import Database
from app.github.schemas import GitHubInstallation as GitHubInstallationData
from app.github.schemas import GitHubRepository, GitHubUser
from app.queue import JobQueueProtocol, QueueUnavailableError

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
    ref: str | None = Field(
        default=None,
        pattern=r"^refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$",
    )
    before: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    after: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    forced: bool = False
    deleted: bool = False


class WebhookService:
    """Apply only short, durable access-state transitions in the request."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        queue: JobQueueProtocol | None = None,
    ) -> None:
        self._database = database
        self._queue = queue
        self._secret = settings.github_webhook_secret.get_secret_value().encode()

    def verify_signature(self, body: bytes, signature: str | None) -> None:
        """Authenticate the exact raw bytes with GitHub's HMAC-SHA256 contract."""
        if signature is None or not signature.startswith("sha256="):
            raise WebhookSignatureError
        expected = "sha256=" + hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise WebhookSignatureError

    async def handle(  # noqa: PLR0912 -- authentication and durable ack policy are centralized
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

            job = await self._apply_event(session, event_name, payload, delivery)
            if job is not None:
                delivery.status = WebhookDeliveryStatus.QUEUED
                delivery.indexing_job_id = job.id
            elif delivery.status is WebhookDeliveryStatus.RECEIVED:
                delivery.status = WebhookDeliveryStatus.PROCESSED
                delivery.processed_at = datetime.now(UTC)
            await session.commit()
        if job is not None and self._queue is not None:
            try:
                await self._queue.enqueue(job.id)
            except QueueUnavailableError:
                async with self._database.session() as session:
                    stored = await session.get(WebhookDelivery, inserted_id)
                    if stored is not None:
                        stored.status = WebhookDeliveryStatus.RETRYABLE
                        stored.safe_error_code = "queue_unavailable"
                        stored.retry_count += 1
                    await session.commit()
            else:
                async with self._database.session() as session:
                    await session.execute(
                        update(IndexingJob)
                        .where(IndexingJob.id == job.id)
                        .values(last_enqueued_at=datetime.now(UTC))
                    )
                    await session.commit()
        return "accepted"

    async def _apply_event(
        self,
        session: AsyncSession,
        event_name: str,
        payload: GitHubWebhookPayload,
        delivery: WebhookDelivery,
    ) -> IndexingJob | None:
        if event_name == "installation" and payload.installation is not None:
            await self._apply_installation(session, payload)
        elif event_name == "installation_repositories" and payload.installation is not None:
            await self._apply_repository_access(session, payload)
        elif event_name == "push":
            return await self._apply_push(session, payload, delivery)
        elif (
            event_name == "repository"
            and payload.action == "deleted"
            and payload.repository is not None
            and payload.installation is not None
        ):
            await session.execute(
                update(Repository)
                .where(
                    Repository.github_repository_id == payload.repository.id,
                    Repository.installation_id
                    == select(GitHubInstallation.id)
                    .where(GitHubInstallation.github_installation_id == payload.installation.id)
                    .scalar_subquery(),
                )
                .values(
                    access_revoked_at=datetime.now(UTC),
                    deleted_at=datetime.now(UTC),
                    indexing_status=RepositoryIndexingStatus.ACCESS_REVOKED,
                )
            )
        elif event_name == "repository":
            return await self._apply_repository_metadata(session, payload, delivery)
        return None

    async def _apply_push(  # noqa: PLR0911 -- explicit non-enumerating terminal states
        self,
        session: AsyncSession,
        payload: GitHubWebhookPayload,
        delivery: WebhookDelivery,
    ) -> IndexingJob | None:
        if (
            payload.installation is None
            or payload.repository is None
            or payload.ref is None
            or payload.before is None
            or payload.after is None
        ):
            raise WebhookPayloadError
        installation = await session.scalar(
            select(GitHubInstallation).where(
                GitHubInstallation.github_installation_id == payload.installation.id,
                GitHubInstallation.status == InstallationStatus.ACTIVE,
                GitHubInstallation.deleted_at.is_(None),
            )
        )
        if installation is None:
            self._finish_delivery(
                delivery,
                WebhookDeliveryStatus.UNAUTHORIZED,
                "installation_unauthorized",
            )
            return None
        repository = await session.scalar(
            select(Repository)
            .where(
                Repository.installation_id == installation.id,
                Repository.github_repository_id == payload.repository.id,
                Repository.access_revoked_at.is_(None),
                Repository.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if repository is None:
            self._finish_delivery(
                delivery,
                WebhookDeliveryStatus.UNAUTHORIZED,
                "repository_unauthorized",
            )
            return None
        delivery.repository_id = repository.id
        delivery.ref = payload.ref
        delivery.before_commit_sha = payload.before
        delivery.after_commit_sha = payload.after
        expected_ref = f"refs/heads/{repository.default_branch}"
        if payload.ref != expected_ref:
            self._finish_delivery(delivery, WebhookDeliveryStatus.IGNORED, "non_default_branch")
            return None
        if payload.deleted or payload.after == "0" * 40:
            self._finish_delivery(delivery, WebhookDeliveryStatus.IGNORED, "branch_deleted")
            return None
        if repository.last_indexed_commit_sha == payload.after:
            self._finish_delivery(delivery, WebhookDeliveryStatus.STALE, "already_active")
            repository.last_delivery_status = WebhookDeliveryStatus.STALE.value
            repository.last_delivery_at = datetime.now(UTC)
            return None
        existing = await session.scalar(
            select(IndexingJob).where(
                IndexingJob.repository_id == repository.id,
                IndexingJob.target_commit_sha == payload.after,
                IndexingJob.status.in_(
                    (
                        IndexingJobStatus.QUEUED,
                        IndexingJobStatus.RUNNING,
                        IndexingJobStatus.RETRYING,
                    )
                ),
            )
        )
        if existing is not None:
            delivery.status = WebhookDeliveryStatus.QUEUED
            delivery.indexing_job_id = existing.id
            return None
        repository.refresh_generation += 1
        repository.latest_webhook_commit_sha = payload.after
        repository.current_remote_sha = payload.after
        repository.last_delivery_status = WebhookDeliveryStatus.QUEUED.value
        repository.last_delivery_at = datetime.now(UTC)
        requested_mode = (
            IndexingMode.FULL
            if payload.forced
            or repository.index_version == 0
            or payload.before != repository.last_indexed_commit_sha
            else IndexingMode.INCREMENTAL
        )
        job = IndexingJob(
            repository_id=repository.id,
            requested_by_user_id=None,
            job_type=(
                IndexingJobType.FULL_REBUILD
                if requested_mode is IndexingMode.FULL
                else IndexingJobType.INCREMENTAL_REINDEX
            ),
            status=IndexingJobStatus.QUEUED,
            stage="queued",
            target_commit_sha=payload.after,
            target_branch=repository.default_branch,
            requested_mode=requested_mode,
            refresh_generation=repository.refresh_generation,
        )
        session.add(job)
        await session.flush()
        repository.indexing_status = RepositoryIndexingStatus.QUEUED
        repository.indexing_progress = 0
        repository.indexing_stage = "queued_refresh"
        return job

    async def _apply_repository_metadata(
        self,
        session: AsyncSession,
        payload: GitHubWebhookPayload,
        delivery: WebhookDelivery,
    ) -> IndexingJob | None:
        external = payload.repository
        installation_data = payload.installation
        if external is None or installation_data is None:
            return None
        installation = await session.scalar(
            select(GitHubInstallation).where(
                GitHubInstallation.github_installation_id == installation_data.id,
                GitHubInstallation.status == InstallationStatus.ACTIVE,
                GitHubInstallation.deleted_at.is_(None),
            )
        )
        if installation is None:
            self._finish_delivery(
                delivery,
                WebhookDeliveryStatus.UNAUTHORIZED,
                "installation_unauthorized",
            )
            return None
        repository = await session.scalar(
            select(Repository).where(
                Repository.installation_id == installation.id,
                Repository.github_repository_id == external.id,
                Repository.access_revoked_at.is_(None),
                Repository.deleted_at.is_(None),
            )
        )
        if repository is None:
            self._finish_delivery(
                delivery,
                WebhookDeliveryStatus.UNAUTHORIZED,
                "repository_unauthorized",
            )
            return None
        delivery.repository_id = repository.id
        branch_changed = repository.default_branch != external.default_branch
        repository.github_owner = external.owner.login
        repository.github_name = external.name
        repository.github_full_name = external.full_name
        repository.github_url = external.html_url
        repository.default_branch = external.default_branch
        repository.is_private = external.private
        if branch_changed:
            repository.refresh_generation += 1
            repository.indexing_status = RepositoryIndexingStatus.QUEUED
            repository.indexing_stage = "queued_full_rebuild"
            job = IndexingJob(
                repository_id=repository.id,
                requested_by_user_id=None,
                job_type=IndexingJobType.FULL_REBUILD,
                status=IndexingJobStatus.QUEUED,
                stage="queued",
                target_branch=external.default_branch,
                requested_mode=IndexingMode.FULL,
                full_rebuild_reason="default_branch_changed",
                refresh_generation=repository.refresh_generation,
            )
            session.add(job)
            await session.flush()
            return job
        return None

    @staticmethod
    def _finish_delivery(
        delivery: WebhookDelivery,
        status: WebhookDeliveryStatus,
        safe_error_code: str,
    ) -> None:
        delivery.status = status
        delivery.safe_error_code = safe_error_code
        delivery.processed_at = datetime.now(UTC)

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
