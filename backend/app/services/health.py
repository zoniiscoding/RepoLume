"""Health application service."""

from app.db.session import DatabaseProtocol
from app.queue import JobQueueProtocol
from app.schemas.health import ReadinessChecks, ReadinessResponse


class HealthService:
    """Coordinate dependency readiness checks."""

    def __init__(self, database: DatabaseProtocol, queue: JobQueueProtocol) -> None:
        self._database = database
        self._queue = queue

    async def readiness(self) -> ReadinessResponse:
        """Return a safe aggregate readiness state."""
        database_ready = await self._database.is_ready()
        redis_ready = await self._queue.is_ready()
        if database_ready and redis_ready:
            return ReadinessResponse(
                status="ready",
                checks=ReadinessChecks(database="ready", redis="ready"),
            )
        return ReadinessResponse(
            status="not_ready",
            checks=ReadinessChecks(
                database="ready" if database_ready else "unavailable",
                redis="ready" if redis_ready else "unavailable",
            ),
        )
