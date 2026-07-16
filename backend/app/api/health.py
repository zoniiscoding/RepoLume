"""Safe liveness and readiness endpoints."""

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.db.session import DatabaseProtocol
from app.schemas.health import HealthResponse, ReadinessResponse
from app.services.health import HealthService

router = APIRouter()


@router.get("/live")
async def liveness() -> HealthResponse:
    """Report process liveness without touching dependencies."""
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readiness(request: Request) -> ReadinessResponse | JSONResponse:
    """Report whether PostgreSQL is reachable within the configured timeout."""
    database: DatabaseProtocol = request.app.state.database
    result = await HealthService(database).readiness()
    if result.status == "ready":
        return result
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=result.model_dump(mode="json"),
    )
