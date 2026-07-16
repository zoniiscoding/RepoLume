"""Health application service."""

from app.db.session import DatabaseProtocol
from app.schemas.health import ReadinessChecks, ReadinessResponse


class HealthService:
    """Coordinate dependency readiness checks."""

    def __init__(self, database: DatabaseProtocol) -> None:
        self._database = database

    async def readiness(self) -> ReadinessResponse:
        """Return a safe aggregate readiness state."""
        if await self._database.is_ready():
            return ReadinessResponse(
                status="ready",
                checks=ReadinessChecks(database="ready"),
            )
        return ReadinessResponse(
            status="not_ready",
            checks=ReadinessChecks(database="unavailable"),
        )
