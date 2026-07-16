"""Strict private embedding wire contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EmbeddingDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    text: str = Field(min_length=1)


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["document", "query"]
    documents: list[EmbeddingDocument] = Field(min_length=1)


class EmbeddingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    embedding: list[float]


class EmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    revision: str
    dimension: int
    normalized: bool
    results: list[EmbeddingResult]


class LivenessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["loading", "ready", "failed"]
    model: str
    revision: str
    dimension: int
    normalized: bool
    maximum_tokens: int
