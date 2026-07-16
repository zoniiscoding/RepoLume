"""Redis Streams delivery for opaque durable job identifiers."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

from app.core.config import Settings

logger = structlog.get_logger(__name__)


class QueueUnavailableError(RuntimeError):
    """Redis could not durably accept or deliver a job identifier."""


@dataclass(frozen=True, slots=True)
class QueueDelivery:
    """One at-least-once Redis Stream delivery."""

    delivery_id: str
    job_id: uuid.UUID


class JobQueueProtocol(Protocol):
    async def is_ready(self) -> bool: ...

    async def enqueue(self, job_id: uuid.UUID) -> None: ...

    async def close(self) -> None: ...


class WorkerQueueProtocol(JobQueueProtocol, Protocol):
    async def ensure_group(self) -> None: ...

    async def receive(self, consumer_name: str) -> QueueDelivery | None: ...

    async def reclaim(self, consumer_name: str) -> Sequence[QueueDelivery]: ...

    async def acknowledge(self, delivery_id: str) -> None: ...


class RedisJobQueue:
    """Use Redis only for at-least-once wakeups; PostgreSQL owns job state."""

    def __init__(self, *, client: Redis, settings: Settings) -> None:
        self._client = client
        self._stream = settings.worker_stream_name
        self._group = settings.worker_consumer_group
        self._poll_timeout_ms = settings.worker_poll_timeout_ms
        self._abandoned_after_ms = settings.worker_abandoned_after_seconds * 1_000
        self._max_length = settings.worker_stream_max_length

    @classmethod
    def from_settings(cls, settings: Settings) -> "RedisJobQueue":
        client = Redis.from_url(
            settings.redis_url.get_secret_value(),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
        return cls(client=client, settings=settings)

    async def is_ready(self) -> bool:
        try:
            return bool(await self._client.ping())
        except (OSError, RedisError) as error:
            logger.warning("redis_readiness_failed", error_type=type(error).__name__)
            return False

    async def ensure_group(self) -> None:
        try:
            await self._client.xgroup_create(
                name=self._stream,
                groupname=self._group,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise QueueUnavailableError from error
        except (OSError, RedisError) as error:
            raise QueueUnavailableError from error

    async def enqueue(self, job_id: uuid.UUID) -> None:
        try:
            await self._client.xadd(
                self._stream,
                {"job_id": str(job_id)},
                maxlen=self._max_length,
                approximate=True,
            )
        except (OSError, RedisError) as error:
            raise QueueUnavailableError from error

    async def receive(self, consumer_name: str) -> QueueDelivery | None:
        try:
            raw = await self._client.xreadgroup(
                groupname=self._group,
                consumername=consumer_name,
                streams={self._stream: ">"},
                count=1,
                block=self._poll_timeout_ms,
            )
        except (OSError, RedisError) as error:
            raise QueueUnavailableError from error
        return self._first_delivery(cast(list[Any], raw))

    async def reclaim(self, consumer_name: str) -> Sequence[QueueDelivery]:
        try:
            raw = await self._client.xautoclaim(
                name=self._stream,
                groupname=self._group,
                consumername=consumer_name,
                min_idle_time=self._abandoned_after_ms,
                start_id="0-0",
                count=10,
            )
        except (OSError, RedisError) as error:
            raise QueueUnavailableError from error
        messages = raw[1]
        return tuple(
            delivery
            for delivery in (self._parse_delivery(item) for item in messages)
            if delivery is not None
        )

    async def acknowledge(self, delivery_id: str) -> None:
        try:
            await self._client.xack(self._stream, self._group, delivery_id)
            await self._client.xdel(self._stream, delivery_id)
        except (OSError, RedisError) as error:
            raise QueueUnavailableError from error

    @staticmethod
    def _first_delivery(streams: list[Any]) -> QueueDelivery | None:
        if not streams or not streams[0][1]:
            return None
        return RedisJobQueue._parse_delivery(streams[0][1][0])

    @staticmethod
    def _parse_delivery(item: Any) -> QueueDelivery | None:
        try:
            delivery_id, fields = item
            return QueueDelivery(
                delivery_id=str(delivery_id),
                job_id=uuid.UUID(str(fields["job_id"])),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("invalid_queue_delivery")
            return None

    async def close(self) -> None:
        await self._client.aclose()
