"""Redis Streams adapter behavior and safe failure translation."""

import uuid
from typing import Any, cast

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError

from app.queue import QueueUnavailableError, RedisJobQueue
from tests.conftest import make_settings


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.fail: Exception | None = None
        self.read_result: list[Any] = []
        self.claim_result: list[Any] = ["0-0", [], []]

    def _raise(self) -> None:
        if self.fail is not None:
            raise self.fail

    async def ping(self) -> bool:
        self._raise()
        return True

    async def xgroup_create(self, **kwargs: Any) -> None:
        self._raise()
        self.calls.append(("group", kwargs))

    async def xadd(self, *args: Any, **kwargs: Any) -> None:
        self._raise()
        self.calls.append(("add", (args, kwargs)))

    async def xreadgroup(self, **kwargs: Any) -> list[Any]:
        self._raise()
        self.calls.append(("read", kwargs))
        return self.read_result

    async def xautoclaim(self, **kwargs: Any) -> list[Any]:
        self._raise()
        self.calls.append(("claim", kwargs))
        return self.claim_result

    async def xack(self, *args: Any) -> None:
        self._raise()
        self.calls.append(("ack", args))

    async def xdel(self, *args: Any) -> None:
        self.calls.append(("delete", args))

    async def aclose(self) -> None:
        self.calls.append(("close", None))


def queue(fake: FakeRedis) -> RedisJobQueue:
    return RedisJobQueue(client=cast(Redis, fake), settings=make_settings())


@pytest.mark.asyncio
async def test_queue_delivers_only_uuid_and_acknowledges() -> None:
    fake = FakeRedis()
    adapter = queue(fake)
    job_id = uuid.uuid4()
    fake.read_result = [["repolume:indexing", [["1-0", {"job_id": str(job_id)}]]]]
    fake.claim_result = ["0-0", [["2-0", {"job_id": str(job_id)}]], []]

    await adapter.ensure_group()
    await adapter.enqueue(job_id)
    delivery = await adapter.receive("worker")
    reclaimed = await adapter.reclaim("worker")
    assert delivery is not None
    await adapter.acknowledge(delivery.delivery_id)
    await adapter.close()

    add_call = next(item for item in fake.calls if item[0] == "add")
    assert add_call[1][0][1] == {"job_id": str(job_id)}
    assert delivery.job_id == job_id
    assert reclaimed[0].delivery_id == "2-0"
    assert {item[0] for item in fake.calls} >= {
        "group",
        "add",
        "read",
        "claim",
        "ack",
        "delete",
        "close",
    }


@pytest.mark.asyncio
async def test_queue_handles_empty_invalid_and_existing_group() -> None:
    fake = FakeRedis()
    adapter = queue(fake)
    fake.fail = ResponseError("BUSYGROUP Consumer Group name already exists")
    await adapter.ensure_group()
    fake.fail = None

    assert await adapter.receive("worker") is None
    assert RedisJobQueue._parse_delivery(["1-0", {}]) is None
    assert RedisJobQueue._parse_delivery(["1-0", {"job_id": "bad"}]) is None


@pytest.mark.asyncio
async def test_queue_redacts_redis_failures() -> None:
    fake = FakeRedis()
    adapter = queue(fake)
    fake.fail = RedisConnectionError("redis-password-sentinel")

    assert await adapter.is_ready() is False
    with pytest.raises(QueueUnavailableError) as captured:
        await adapter.enqueue(uuid.uuid4())

    assert "redis-password-sentinel" not in str(captured.value)
