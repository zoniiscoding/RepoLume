"""Safe GitHub webhook acknowledgement contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class WebhookResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted", "duplicate", "ignored"]
