"""Safe health response schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """Process liveness response."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]


class ReadinessChecks(BaseModel):
    """Dependency readiness without provider details."""

    model_config = ConfigDict(extra="forbid")

    database: Literal["ready", "unavailable"]


class ReadinessResponse(BaseModel):
    """Overall readiness result."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "not_ready"]
    checks: ReadinessChecks
